package io.github.obamna1234.sevby

import android.content.Context
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

data class RunSummary(val ok: Int, val skipped: Int, val failed: List<String>, val stopped: Boolean)

/** Result of saving one file. */
private enum class Saved { NEW, ALREADY_THERE, FAILED }

/**
 * Runs one song list, one song at a time, into one folder.
 * Bandcamp first (when the source allows it), then YouTube with iTunes album info.
 *
 * [log] gets the technical details, [step] what's happening right now, [done] one tidy line
 * per finished song, [status] the progress numbers.
 */
class Downloader(
    private val context: Context,
    private val folder: DocumentFile,
    private val log: (String) -> Unit,
    private val status: (index: Int, total: Int, song: String, percent: Float?) -> Unit,
    private val bandcamp: Bandcamp? = null,
    /** Test switch: act as if Bandcamp blocked every request (to check the YouTube fallback). */
    private val pretendBlocked: Boolean = false,
    private val step: (String) -> Unit = {},
    private val done: (SongLine) -> Unit = {},
) {
    private var blocksInARow = 0
    private var bandcampOff = false
    @Volatile private var libraryNoted = false
    @Volatile private var stopped = false
    @Volatile private var paused = false
    @Volatile private var currentId: String? = null

    fun stop() {
        stopped = true
        currentId?.let { Engine.stop(it) }
    }

    /** Pause right away: the current download is cancelled and that song starts again on resume. */
    fun pause() {
        if (paused || stopped) return
        paused = true
        currentId?.let { Engine.stop(it) }
    }

    fun resume() {
        paused = false
    }

    val isPaused: Boolean get() = paused

    /** Carry Bandcamp's "keeps blocking" state over from the previous list in a queue. */
    fun inheritBandcampState(from: Downloader?) {
        if (from == null) return
        blocksInARow = from.blocksInARow
        bandcampOff = from.bandcampOff
        libraryNoted = from.libraryNoted   // the music-library note shows once per run, not once per list
    }

    /** Length of an MP3 in seconds (0 if unknown). Bandcamp streams are 128 kbps, so size works as a backup. */
    private fun mp3Seconds(file: File): Double {
        val ms = runCatching {
            val r = android.media.MediaMetadataRetriever()
            try {
                r.setDataSource(file.absolutePath)
                r.extractMetadata(android.media.MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
            } finally {
                r.release()
            }
        }.getOrNull()
        if (ms != null && ms > 0) return ms / 1000.0
        return if (file.length() > 0) file.length() * 8.0 / 128_000 else 0.0
    }

    private suspend fun waitWhilePaused() {
        while (paused && !stopped) delay(300)
    }

    private fun stepLog(text: String) {
        step(text)
        log("  $text")
    }

    suspend fun run(songs: List<String>, source: Source): RunSummary {
        var ok = 0
        var skipped = 0
        val failed = mutableListOf<String>()
        val index = FolderIndex(context, folder, log)
        withContext(Dispatchers.IO) { index.mp3Count() }?.let {
            log("Save folder has $it MP3 file" + (if (it == 1) "" else "s") + " – songs already there are skipped")
        }
        val seen = SeenSongs()                  // songs met in this run (any capitals, typed or official name)
        val work = File(context.cacheDir, "work").apply { mkdirs() }

        fun fail(song: String, why: String) {
            failed += song
            done(SongLine(song, Mark.FAILED, why))
        }

        /** True if a whole copy of [fileName] is in the folder; a cut-off leftover is deleted. */
        suspend fun inFolder(fileName: String): Boolean {
            val size = withContext(Dispatchers.IO) { index.sizeOf(fileName) } ?: return false
            if (size >= MIN_MP3_BYTES) return true
            log("  Found an incomplete copy – downloading again")
            withContext(Dispatchers.IO) { runCatching { folder.findFile(fileName)?.delete() } }
            return false
        }

        songLoop@ for ((i, typed) in songs.withIndex()) {
            waitWhilePaused()
            if (stopped) break
            status(i + 1, songs.size, typed, null)
            step("Checking the folder…")
            log("[${i + 1}/${songs.size}] $typed")

            // Listed twice (in any capitals)? Already there under the name as typed?
            if (seen.contains(typed)) {
                log("  SKIP (listed twice)")
                skipped++
                done(SongLine(typed, Mark.SKIPPED, "listed twice"))
                continue
            }
            if (inFolder(SongList.safeFileName(typed) + ".mp3")) {
                seen.add(typed)
                log("  SKIP (already downloaded)")
                skipped++
                done(SongLine(typed, Mark.SKIPPED, "already downloaded"))
                continue
            }

            // The official spelling from iTunes ("kataklysm - black sheep" → "Kataklysm - The Black Sheep"),
            // used for everything from here on: file name, checks, search, tags and log.
            step("Looking up the song…")
            val apple = withContext(Dispatchers.IO) { Itunes.lookup(typed) }
            val song = Matching.officialName(typed, apple)
            if (song != typed) {
                log("  Official name: $song")
                if (seen.contains(song)) {
                    seen.add(typed)
                    log("  SKIP (listed twice)")
                    skipped++
                    done(SongLine(song, Mark.SKIPPED, "listed twice"))
                    continue
                }
                if (inFolder(SongList.safeFileName(song) + ".mp3")) {
                    seen.add(typed, song)
                    log("  SKIP (already downloaded)")
                    skipped++
                    done(SongLine(song, Mark.SKIPPED, "already downloaded"))
                    continue
                }
                status(i + 1, songs.size, song, null)
            }
            seen.add(typed, song)
            val name = SongList.safeFileName(song) + ".mp3"
            val ctl = RunControl(
                log = log,
                onStart = { currentId = it },
                onProgress = { p -> status(i + 1, songs.size, song, p) },
                isStopped = { stopped || paused },
            )

            // iTunes details (real length, album, cover): already looked up above.
            suspend fun itunesInfo(): AlbumInfo? = apple

            // ── 1. Bandcamp first (unless YouTube only, or Bandcamp kept blocking this run) ──
            var bcTrack: BcTrack? = null
            var bcMp3: File? = null
            var bandcampNote = ""
            if (source != Source.YOUTUBE && bandcamp != null && !bandcampOff) {
                stepLog("Searching Bandcamp…")
                val r = if (pretendBlocked) BcResult.Blocked("test switch is on") else bandcamp.find(song, log) { itunesInfo()?.album }
                if (stopped) break
                when (r) {
                    is BcResult.Found -> {
                        blocksInARow = 0
                        log("  Bandcamp: ${r.track.pageUrl}")
                        step("Downloading from Bandcamp…")
                        val dest = File(work, "bandcamp.mp3")
                        while (true) {
                            waitWhilePaused()
                            if (stopped) break@songLoop
                            val okDl = bandcamp.download(r.track, dest, { stopped || paused }) { p -> status(i + 1, songs.size, song, p) }
                            if (stopped) break@songLoop
                            if (paused) { log("  Paused – this song will start again when you resume"); continue }
                            if (okDl) {
                                // A remix or other version can sit under the right name: compare with the real length.
                                val real = itunesInfo()?.seconds ?: 0
                                val fileSec = mp3Seconds(dest)
                                if (Matching.lengthMismatch(fileSec, real)) {
                                    log("  Bandcamp copy is ${Matching.fmtLen(fileSec)} but the real song is ${Matching.fmtLen(real.toDouble())}" +
                                        " - probably a remix or other version, skipping it")
                                    dest.delete()
                                    bandcampNote = "Bandcamp had a different version"
                                } else {
                                    bcTrack = r.track; bcMp3 = dest
                                }
                            } else {
                                log("  Bandcamp download failed")
                                bandcampNote = "Bandcamp download failed"
                            }
                            break
                        }
                    }
                    is BcResult.NotFound -> {
                        blocksInARow = 0
                        log("  Not on Bandcamp")
                        bandcampNote = "not on Bandcamp"
                    }
                    is BcResult.Blocked -> {
                        blocksInARow++
                        log("  Bandcamp blocked this connection (bot check: ${r.why})")
                        bandcampNote = "Bandcamp blocked"
                        if (blocksInARow >= 3) {
                            bandcampOff = true
                            log("  Bandcamp keeps blocking – skipping Bandcamp for the rest of this run")
                            Runner.log("Bandcamp keeps blocking this connection – using YouTube for the rest of this run")
                        }
                    }
                }
            } else if (source != Source.YOUTUBE && bandcampOff) {
                bandcampNote = "Bandcamp blocked"
            }
            if (bcTrack != null && bcMp3 != null) {
                step("Saving…")
                when (withContext(Dispatchers.IO) { saveBandcamp(bcMp3, bcTrack, song, name, work, apple) }) {
                    Saved.NEW -> {
                        ok++
                        log("  OK (Bandcamp) → $name")
                        val album = bcTrack.album.ifEmpty { bcTrack.title }
                        done(SongLine(song, Mark.OK, "Bandcamp · $album" + (if (bcTrack.year.isNotEmpty()) " (${bcTrack.year})" else "")))
                    }
                    Saved.ALREADY_THERE -> { skipped++; done(SongLine(song, Mark.SKIPPED, "already downloaded")) }
                    Saved.FAILED -> fail(song, "couldn't save the file")
                }
                work.listFiles()?.forEach { it.deleteRecursively() }
                continue
            }
            if (source == Source.BANDCAMP) {
                log("  Skipped (Bandcamp only)")
                fail(song, bandcampNote.ifEmpty { "not on Bandcamp" })
                continue
            }
            if (source == Source.BOTH && bandcamp != null) log("  Using YouTube instead")

            // ── 2. YouTube, with album details + real cover from iTunes ──
            step("Looking up album info…")
            val album = itunesInfo()
            if (album != null) {
                val bits = listOfNotNull(album.year.ifEmpty { null }, album.track.takeIf { it > 0 }?.let { "track $it" })
                log("  Album: ${album.album}" + (if (bits.isNotEmpty()) " (${bits.joinToString(", ")})" else "") + " — iTunes")
            } else {
                log("  No album info found on iTunes")
            }

            // Download; if Pause interrupts it, wait and start this song again.
            var got: Fetched?
            while (true) {
                waitWhilePaused()
                if (stopped) break@songLoop
                step("Searching YouTube…")
                got = YouTube.fetch(song, work, ctl, album?.seconds ?: 0)
                if (stopped) break@songLoop
                if (paused) {
                    log("  Paused – this song will start again when you resume")
                    status(i + 1, songs.size, song, null)
                    continue
                }
                break
            }
            if (got == null) {
                val why = when {
                    YouTube.lastError != null && Engine.wrongApk(context) != null -> "wrong APK for this device – see About & updates"
                    YouTube.lastError != null -> "YouTube error – update yt-dlp in About & updates"
                    YouTube.lastNote != null -> YouTube.lastNote!!
                    else -> "not found on YouTube"
                }
                fail(song, why + if (bandcampNote.isNotEmpty() && source == Source.BOTH) " ($bandcampNote)" else "")
                continue
            }

            step("Saving…")
            when (withContext(Dispatchers.IO) { save(got, song, name, album, work) }) {
                Saved.NEW -> {
                    ok++
                    log("  OK → $name")
                    val tagAlbum = album?.album?.takeIf { it.isNotEmpty() } ?: got.info?.optString("album")?.takeIf { it.isNotEmpty() }
                    done(SongLine(song, Mark.OK, "YouTube" + (tagAlbum?.let { " · $it" } ?: "") +
                        if (bandcampNote.isNotEmpty() && source == Source.BOTH) " ($bandcampNote)" else ""))
                }
                Saved.ALREADY_THERE -> { skipped++; done(SongLine(song, Mark.SKIPPED, "already downloaded")) }
                Saved.FAILED -> fail(song, "couldn't save the file")
            }
            work.listFiles()?.forEach { it.deleteRecursively() }
        }
        work.listFiles()?.forEach { it.deleteRecursively() }
        return RunSummary(ok, skipped, failed, stopped)
    }

    /** Tag a Bandcamp MP3 (Bandcamp's own album info and cover) and copy it into the folder. */
    private fun saveBandcamp(mp3: File, t: BcTrack, song: String, name: String, work: File, apple: AlbumInfo? = null): Saved {
        val (listArtist, typedTitle) = SongList.split(song)
        val typedArtist = tidyArtists(listArtist.ifEmpty { t.artist })
        // Apple's spelling when it's the same title/artist written differently.
        val title = apple?.let { Matching.preferAppleTitle(typedTitle, it.title) } ?: typedTitle
        val artist = apple?.let { Matching.preferAppleArtist(typedArtist, it.artistCanon) } ?: typedArtist
        val album = t.album.ifEmpty { t.title }            // a single is its own album
        val tags = Tags(
            title = title, artist = artist, albumArtist = artist, album = album, year = t.year,
            track = if (t.trackNumber > 0) "${t.trackNumber}" else "1",
        )
        val cover = t.coverUrl.takeIf { it.isNotEmpty() }?.let { Cover.fromUrl(it, File(work, "cover.download")) }
        return write(mp3, tags, cover, name)
    }

    /** Tag a YouTube MP3 and copy it into the user's folder. */
    private fun save(got: Fetched, song: String, name: String, album: AlbumInfo?, work: File): Saved {
        val tags = tagsFor(song, got.info, album)
        // Real album cover first; the video thumbnail only if there is none.
        val cover = album?.coverUrl?.takeIf { it.isNotEmpty() }?.let { Cover.fromUrl(it, File(work, "cover.download")) }
            ?: got.thumbnail?.let { Cover.squareJpeg(it) }
        return write(got.mp3, tags, cover, name)
    }

    private fun write(mp3: File, tags: Tags, cover: ByteArray?, name: String): Saved {
        // Last check right before saving: never make a second copy ("name (1).mp3").
        folder.findFile(name)?.let { there ->
            if (there.isFile() && there.length() >= MIN_MP3_BYTES) {
                log("  Already in folder – kept the existing copy")
                return Saved.ALREADY_THERE
            }
            runCatching { there.delete() }      // an empty / cut-off leftover
        }
        val doc = folder.createFile("audio/mpeg", name) ?: run {
            log("  Couldn't create $name in the folder")
            return Saved.FAILED
        }
        if (doc.name != null && !doc.name.equals(name, ignoreCase = true)) {
            // Android renamed it because the name was taken after all: don't leave a duplicate.
            runCatching { doc.delete() }
            log("  Already in folder – kept the existing copy")
            return Saved.ALREADY_THERE
        }
        return try {
            context.contentResolver.openOutputStream(doc.uri)!!.use { out ->
                Id3.writeTagged(mp3, tags, cover, out)
            }
            if (cover == null) log("  Note: no cover art could be found for this track.")
            MediaLibrary.announce(context, folder.uri, name) { listed ->
                if (!listed && !libraryNoted) {
                    libraryNoted = true
                    log("(Note: Android's music library is slow to list new songs in this folder. Music apps that scan folders, like VLC, find them straight away; others after their next rescan.)")
                }
            }
            Saved.NEW
        } catch (e: Exception) {
            log("  Couldn't save $name: ${e.message}")
            runCatching { doc.delete() }
            Saved.FAILED
        }
    }

    companion object {
        /** Anything smaller than this can't be a whole song (128 kbps ≈ 16 KB per second). */
        private const val MIN_MP3_BYTES = 100_000L

        /**
         * Title and artist come from the song list (what the user asked for). Album, year and
         * track number come from iTunes when it found the song, else from YouTube when it knows
         * them (official "Topic" uploads).
         */
        fun tagsFor(song: String, info: JSONObject?, itunes: AlbumInfo? = null): Tags {
            val (listArtist, title) = SongList.split(song)
            val ytArtist = info?.optString("artist").orEmpty()
            val artist = tidyArtists(listArtist.ifEmpty { ytArtist })
            if (itunes != null && itunes.album.isNotEmpty()) {
                val track = when {
                    itunes.track > 0 && itunes.trackCount > 0 -> "${itunes.track}/${itunes.trackCount}"
                    itunes.track > 0 -> "${itunes.track}"
                    else -> ""
                }
                // Apple's spelling when it's the same title/artist written differently ("the black sheep" → "The Black Sheep").
                val t = Matching.preferAppleTitle(title, itunes.title)
                val a = Matching.preferAppleArtist(artist, itunes.artistCanon)
                return Tags(title = t, artist = a, albumArtist = a, album = itunes.album, year = itunes.year, track = track)
            }
            val album = info?.optString("album").orEmpty()
            var year = ""
            var track = ""
            if (album.isNotEmpty() && info != null) {
                year = info.optInt("release_year", 0).takeIf { it > 0 }?.toString()
                    ?: info.optString("release_date").take(4).takeIf { it.length == 4 }.orEmpty()
                track = info.optInt("track_number", 0).takeIf { it > 0 }?.toString().orEmpty()
            }
            return Tags(title = title, artist = artist, albumArtist = artist, album = album, year = year, track = track)
        }

        /** "Power Glove,PYLOT" → "Power Glove, PYLOT" (Spotify exports leave out the space). */
        fun tidyArtists(s: String): String =
            s.split(',').map { it.trim() }.filter { it.isNotEmpty() }.joinToString(", ")
    }
}
