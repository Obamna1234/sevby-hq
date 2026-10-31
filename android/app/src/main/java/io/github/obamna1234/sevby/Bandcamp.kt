package io.github.obamna1234.sevby

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** What a Bandcamp lookup ended in. */
sealed class BcResult {
    data class Found(val track: BcTrack) : BcResult()
    object NotFound : BcResult()
    data class Blocked(val why: String) : BcResult()
}

/**
 * Bandcamp part of SEVBY: search, pick the right track, read its page, download its MP3.
 * Every step first tries a plain request and, if Bandcamp answers with a bot check,
 * repeats it through Android's browser engine ([BandcampWeb]).
 */
class Bandcamp(private val web: BandcampWeb) {

    private val simpleUa = "Mozilla/5.0 (compatible; BandcampSongLinker/1.0)"
    private val browserUa = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"

    /**
     * Find [song] on Bandcamp. [log] gets short progress notes. [itunesAlbum] gives the album iTunes lists
     * for the song; it's only asked for when the sole matches are on other accounts (labels, DJs, fans).
     */
    suspend fun find(song: String, log: (String) -> Unit, itunesAlbum: suspend () -> String? = { null }): BcResult {
        val (artist, title) = SongList.split(song)
        if (artist.isEmpty()) return BcResult.NotFound      // no artist: too easy to pick the wrong song

        // 1. Search (artist + title, then title alone like the desktop app).
        var blockedWhy: String? = null
        var picks = emptyList<BcHit>()
        for (q in listOf("$artist $title", Matching.cleanTitle(title))) {
            when (val hits = search(q)) {
                null -> blockedWhy = "search refused"
                else -> {
                    picks = BandcampData.choose(hits, song)
                    if (picks.isEmpty() && BandcampData.needsItunesAlbum(hits, song)) {
                        val album = itunesAlbum()
                        picks = BandcampData.choose(hits, song, album)
                        if (picks.isEmpty()) log("  Bandcamp only has this on other accounts" +
                            (if (album.isNullOrBlank()) " and iTunes has no album to compare" else ", not on the album \"$album\"") +
                            " – skipping them")
                    }
                    if (picks.isNotEmpty()) break
                }
            }
        }
        if (picks.isEmpty()) return if (blockedWhy != null) BcResult.Blocked(blockedWhy) else BcResult.NotFound

        // 2. Open the best match (and the runner-up if the first can't be streamed).
        for (hit in picks.take(2)) {
            val track = page(hit.url) ?: return BcResult.Blocked("track page refused")
            if (!BandcampData.isSameSong(song, track.title, track.artist.ifEmpty { hit.band })) continue
            // The page knows the album name even when the search result didn't.
            BandcampData.notOriginal(song, track.title, track.album)?.let { why ->
                log("  Skipped a Bandcamp match: $why (${track.album})")
                continue
            }
            if (track.mp3Url.isEmpty()) {
                log("  Bandcamp has this track, but it can't be streamed (purchase only)")
                continue
            }
            return BcResult.Found(track)
        }
        return BcResult.NotFound
    }

    /** Search results, or null if Bandcamp refused every way of asking. */
    private suspend fun search(q: String): List<BcHit>? {
        val enc = URLEncoder.encode(q, "UTF-8")
        // a) the app search API, b) the website's own search API, c) the search page in the browser engine
        get("https://bandcamp.com/api/fuzzysearch/2/app_autocomplete?q=$enc&param_with_locations=true", simpleUa)
            ?.let { body -> runCatching { return BandcampData.parseSearch(body) } }
        post(
            "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic",
            JSONObject().put("search_text", q).put("search_filter", "t").put("full_page", false).put("fan_id", JSONObject.NULL).toString(),
        )?.let { body -> runCatching { return BandcampData.parseSearch(body) } }
        val viaBrowser = web.read("https://bandcamp.com/search?q=$enc&item_type=t", BandcampWeb.SEARCH_SCRIPT) ?: return null
        return runCatching { BandcampData.parseSearch(viaBrowser) }.getOrNull()
    }

    /** A track page's data, or null if Bandcamp refused both ways of opening it. */
    private suspend fun page(url: String): BcTrack? {
        get(url, browserUa, wantJson = false)?.let { html -> BandcampData.parseHtml(html, url)?.let { return it } }
        val raw = web.read(url, BandcampWeb.TRACK_SCRIPT) ?: return null
        return runCatching {
            val o = JSONObject(raw)
            BandcampData.parsePage(o.getString("tralbum"), o.optString("embed").ifEmpty { null }, o.optString("og"), url)
        }.getOrNull()
    }

    /** Download Bandcamp's 128 kbps MP3 into [dest]. [progress] gets 0–100. */
    suspend fun download(track: BcTrack, dest: File, isStopped: () -> Boolean, progress: (Float) -> Unit): Boolean =
        withContext(Dispatchers.IO) {
            runCatching {
                val con = (URL(track.mp3Url).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 20_000
                    readTimeout = 30_000
                    setRequestProperty("User-Agent", browserUa)
                    setRequestProperty("Referer", track.pageUrl)
                }
                try {
                    if (con.responseCode != 200) return@runCatching false
                    val total = con.contentLengthLong
                    var got = 0L
                    con.inputStream.use { inp ->
                        dest.outputStream().use { out ->
                            val buf = ByteArray(64 * 1024)
                            while (true) {
                                if (isStopped()) return@runCatching false
                                val n = inp.read(buf)
                                if (n < 0) break
                                out.write(buf, 0, n)
                                got += n
                                if (total > 0) progress(got * 100f / total)
                            }
                        }
                    }
                    got > 100_000
                } finally {
                    con.disconnect()
                }
            }.getOrDefault(false).also { ok -> if (!ok) dest.delete() }
        }

    // ── Plain requests ──────────────────────────────────────────────────

    /** Body text, or null on failure or when Bandcamp answers with a bot-check page. */
    private suspend fun get(url: String, ua: String, wantJson: Boolean = true): String? = withContext(Dispatchers.IO) {
        runCatching {
            val con = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 15_000
                readTimeout = 20_000
                setRequestProperty("User-Agent", ua)
                setRequestProperty("Accept", if (wantJson) "application/json" else "text/html")
            }
            try {
                if (con.responseCode != 200) return@runCatching null
                val body = con.inputStream.bufferedReader().readText()
                if (wantJson && !body.trimStart().startsWith("{")) null else body
            } finally {
                con.disconnect()
            }
        }.getOrNull()
    }

    private suspend fun post(url: String, json: String): String? = withContext(Dispatchers.IO) {
        runCatching {
            val con = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 15_000
                readTimeout = 20_000
                requestMethod = "POST"
                doOutput = true
                setRequestProperty("User-Agent", browserUa)
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("Accept", "application/json")
                setRequestProperty("Origin", "https://bandcamp.com")
                setRequestProperty("Referer", "https://bandcamp.com/")
            }
            try {
                con.outputStream.use { it.write(json.toByteArray()) }
                if (con.responseCode != 200) return@runCatching null
                val body = con.inputStream.bufferedReader().readText()
                if (!body.trimStart().startsWith("{")) null else body
            } finally {
                con.disconnect()
            }
        }.getOrNull()
    }
}
