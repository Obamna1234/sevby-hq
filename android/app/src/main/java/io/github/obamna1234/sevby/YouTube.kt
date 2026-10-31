package io.github.obamna1234.sevby

import kotlinx.coroutines.delay
import org.json.JSONObject
import java.io.File

/** Files yt-dlp left in the work folder after a successful download. */
data class Fetched(val mp3: File, val thumbnail: File?, val info: JSONObject?)

/** Hooks the downloader passes in, so Stop can kill the running yt-dlp and progress can be shown. */
class RunControl(
    val log: (String) -> Unit,
    val onStart: (String) -> Unit,
    val onProgress: (Float) -> Unit,
    val isStopped: () -> Boolean,
)

/** YouTube part of SEVBY (port of download_youtube_mp3 in sevby_app.py). */
object YouTube {

    /** Connection dropped / no internet / DNS failure: worth waiting and trying again. */
    fun isNetworkError(err: String): Boolean {
        val e = err.lowercase()
        return listOf(
            "errno 7", "errno -2", "errno -3", "errno 101", "errno 104", "errno 110", "errno 111",
            "name resolution", "no address associated", "network is unreachable", "connection reset",
            "connection refused", "connection aborted", "timed out", "read timeout", "remote end closed",
            "transporterror", "temporarily unavailable",
        ).any { it in e }
    }

    /** Seconds to wait before network retry 1, 2, ... */
    private val NET_WAITS = listOf(5, 15)

    /** Top YouTube results (title, length, channel) without downloading anything. */
    /**
     * Why the last [fetch] failed when YouTube itself gave an error (old yt-dlp, bot check, network),
     * or null when the song simply wasn't found. Lets the song list say which one it was.
     */
    @Volatile var lastError: String? = null
        private set

    /** Short reason for the song list when nothing suitable was found (null = generic "not found"). */
    @Volatile var lastNote: String? = null
        private set

    suspend fun search(song: String, ctl: RunControl, results: Int = Matching.FIRST_SEARCH_RESULTS): List<YtEntry> {
        repeat(1 + NET_WAITS.size) { attempt ->
            if (ctl.isStopped()) return emptyList()
            val r = Engine.run(
                listOf("-J", "--flat-playlist", "--no-warnings", "ytsearch$results:$song"),
                onStart = ctl.onStart,
            )
            if (r.ok) {
                lastError = null
                val entries = runCatching { JSONObject(r.out).optJSONArray("entries") }.getOrNull()
                    ?: return emptyList()
                return (0 until entries.length()).mapNotNull { i ->
                    val e = entries.optJSONObject(i) ?: return@mapNotNull null
                    val id = e.optString("id")
                    if (id.isEmpty() && e.optString("url").isEmpty()) return@mapNotNull null
                    YtEntry(
                        id = id,
                        url = e.optString("url"),
                        title = e.optString("title"),
                        channel = e.optString("channel").ifEmpty { e.optString("uploader") },
                        duration = if (e.has("duration") && !e.isNull("duration")) e.optDouble("duration") else null,
                    )
                }
            }
            if (r.stopped) return emptyList()
            lastError = r.errorLine
            if (attempt == NET_WAITS.size) return emptyList()
            if (isNetworkError(r.err)) {
                val wait = NET_WAITS[attempt]
                ctl.log("  Network problem – retrying in $wait s…")
                delay(wait * 1000L)
            } else if (attempt == 0) {
                delay(2000)
            } else {
                return emptyList()
            }
        }
        return emptyList()
    }

    /**
     * Find the best match for [song] and download it as a 128 kbps MP3 into [work].
     * Returns null on failure (the reason is already logged).
     */
    suspend fun fetch(song: String, work: File, ctl: RunControl, targetSeconds: Int = 0): Fetched? {
        ctl.log("  Searching YouTube…")
        lastError = null
        lastNote = null
        // Only videos whose title shares at least half of the song title's words (no "first hit" guesses).
        // If the normal search has none, up to three other wordings are tried (artist must match too).
        val find = Matching.findOnYouTube(song, targetSeconds) { query, n ->
            if (ctl.isStopped()) null
            else search(query, ctl, n).takeUnless { it.isEmpty() && lastError != null }
        }
        val all = find.firstResults
        if (all.isEmpty() && lastError != null) ctl.log("  YouTube search failed: $lastError")
        if (ctl.isStopped()) return null

        val ranked = find.ranked
        if (ranked.isEmpty() && all.isNotEmpty()) lastError = null   // an extra search failing doesn't make it a YouTube error
        if (find.extraSearch) ctl.log("  (found with a different search wording)")
        if (ranked.isEmpty()) {
            if (lastError == null) {
                lastNote = "no matching video found on YouTube"
                ctl.log("  No matching video on YouTube" +
                    (all.firstOrNull()?.second?.let { " (closest: ${it.title} — ${it.channel})" } ?: ""))
            }
            return null
        }

        val targets = mutableListOf<String>()
        ranked.take(2).forEachIndexed { i, (score, e) ->
            if (i == 0) {
                val by = if (e.channel.isNotEmpty()) " — ${e.channel}" else ""
                ctl.log("  Match: ${e.title} [${Matching.fmtLen(e.duration)}]$by" + if (score < 20) "  (best guess)" else "")
            }
            targets += e.watchUrl
        }

        var lastErr = "no result"
        for (target in targets) {
            if (ctl.isStopped()) return null
            var r = download(target, work, ctl)
            if (!r.ok && !r.stopped && ("403" in r.err || "forbidden" in r.err.lowercase())) {
                delay(3000)                     // YouTube sometimes refuses one request; try once more
                r = download(target, work, ctl)
            }
            for (wait in NET_WAITS) {
                if (r.ok || r.stopped || ctl.isStopped() || !isNetworkError(r.err)) break
                ctl.log("  Network problem – retrying in $wait s…")
                delay(wait * 1000L)
                r = download(target, work, ctl)
            }
            if (r.stopped) return null
            if (r.ok) {
                val mp3 = File(work, "sevby.mp3")
                if (mp3.isFile && mp3.length() > 0) {
                    val thumb = listOf("jpg", "webp", "png", "jpeg").map { File(work, "sevby.$it") }.firstOrNull { it.isFile }
                    val info = File(work, "sevby.info.json").takeIf { it.isFile }
                        ?.let { runCatching { JSONObject(it.readText()) }.getOrNull() }
                    return Fetched(mp3, thumb, info)
                }
                lastErr = "yt-dlp finished but no MP3 was made"
            } else {
                lastErr = r.errorLine
                if (!looksLikeNoResult(lastErr)) lastError = lastErr
            }
        }
        ctl.log("  FAIL: $lastErr")
        if (isNetworkError(lastErr)) ctl.log("  Tip: the internet connection dropped. Run the list again later to fetch the missing songs.")
        if ("403" in lastErr || "forbidden" in lastErr.lowercase() || "sign in" in lastErr.lowercase()) {
            ctl.log("  Tip: YouTube changes often — update yt-dlp in About & updates, then retry.")
        }
        return null
    }

    private fun looksLikeNoResult(err: String): Boolean {
        val e = err.lowercase()
        return "no video" in e || "no results" in e || "no result" == e || "unavailable" in e || "private video" in e
    }

    private suspend fun download(target: String, work: File, ctl: RunControl): RunResult {
        work.listFiles()?.forEach { it.deleteRecursively() }
        return Engine.run(
            listOf(
                "--no-playlist", "--no-warnings", "--no-mtime", "--newline",
                "-f", "bestaudio/best",
                "-x", "--audio-format", "mp3", "--audio-quality", "128K",
                "--write-thumbnail", "--convert-thumbnails", "jpg",
                "--write-info-json", "--no-write-playlist-metafiles",
                "-o", File(work, "sevby.%(ext)s").absolutePath,
                target,
            ),
            onStart = ctl.onStart,
            onProgress = ctl.onProgress,
        )
    }
}
