package io.github.obamna1234.sevby

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.Locale

/** Album details for one song, from Apple's free iTunes Search API (no account needed). */
data class AlbumInfo(
    val title: String,
    val artist: String,
    val album: String,
    val year: String,
    val track: Int,
    val trackCount: Int,
    val seconds: Int,
    val coverUrl: String,
    /** Set by iTunes for compilations ("Various Artists", or a label like "Monstercat"). */
    val albumArtist: String = "",
    /** "2016-01-25T08:00:00Z" style; used to prefer the original release. */
    val releaseDate: String = "",
) {
    /** Apple's own spelling, for the tags: the cleaned track name and the artist name. */
    val titleCanon: String get() = Matching.cleanTitle(title)
    val artistCanon: String get() = artist
}

object Itunes {

    /** Look [song] up. Returns null when nothing matches well enough (or the lookup fails). */
    fun lookup(song: String): AlbumInfo? {
        val country = Locale.getDefault().country.takeIf { it.length == 2 } ?: "US"
        val countries = if (country.equals("US", true)) listOf("US") else listOf(country, "US")
        val (artist, title) = SongList.split(song)
        val term = "$artist ${Matching.cleanTitle(title)}".trim()
        for (c in countries) {
            val results = search(term, c) ?: continue
            choose(results, song)?.let { return it }
        }
        return null
    }

    private fun search(term: String, country: String): List<AlbumInfo>? = runCatching {
        val q = URLEncoder.encode(term, "UTF-8")
        val url = URL("https://itunes.apple.com/search?term=$q&media=music&entity=song&limit=25&country=$country")
        val con = (url.openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 15_000
            setRequestProperty("User-Agent", "SEVBY (Android)")
        }
        try {
            if (con.responseCode != 200) return null
            val json = JSONObject(con.inputStream.bufferedReader().readText())
            val arr = json.optJSONArray("results") ?: return null
            (0 until arr.length()).mapNotNull { i ->
                val r = arr.optJSONObject(i) ?: return@mapNotNull null
                val art = r.optString("artworkUrl100")
                AlbumInfo(
                    title = r.optString("trackName"),
                    artist = r.optString("artistName"),
                    album = r.optString("collectionName"),
                    year = r.optString("releaseDate").take(4).takeIf { y -> y.all { it.isDigit() } && y.length == 4 }.orEmpty(),
                    track = r.optInt("trackNumber", 0),
                    trackCount = r.optInt("trackCount", 0),
                    seconds = r.optInt("trackTimeMillis", 0) / 1000,
                    // Apple serves any size from the same address; 1000×1000 is plenty for a cover.
                    coverUrl = if (art.isNotEmpty()) art.replace(Regex("""/\d+x\d+bb\."""), "/1000x1000bb.") else "",
                    albumArtist = r.optString("collectionArtistName"),
                    releaseDate = r.optString("releaseDate"),
                )
            }
        } finally {
            con.disconnect()
        }
    }.getOrNull()

    /** Variant words that must match: if one side says "remix" and the other doesn't, it's a different recording. */
    private val VARIANTS = listOf("remix", "live", "instrumental", "acoustic", "cover", "edit", "mix", "version", "karaoke")

    /** Pick the result that really is this song (artist and title must both match). */
    fun choose(results: List<AlbumInfo>, song: String): AlbumInfo? {
        val (artist, title) = SongList.split(song)
        val wantTitle = Matching.titleKey(title)
        if (wantTitle.isEmpty()) return null
        val wantArtists = artist.split(Regex("""\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b|\band\b)\s*""", RegexOption.IGNORE_CASE))
            .map { Matching.norm(it) }.filter { it.isNotEmpty() }
        if (wantArtists.isEmpty()) return null   // no artist in the line: too risky to guess the album
        val wantVariants = VARIANTS.filter { Matching.words(Matching.cleanTitle(title)).contains(it) }.toSet()

        return results.filter { r ->
            val gotTitle = Matching.titleKey(r.title)
            val gotArtist = Matching.norm(r.artist)
            val titleOk = gotTitle == wantTitle
            val artistOk = gotArtist.isNotEmpty() && wantArtists.any { it in gotArtist || gotArtist in it }
            val gotVariants = VARIANTS.filter { Matching.words(Matching.cleanTitle(r.title)).contains(it) }.toSet()
            // A live recording is a different recording: never use it unless asked for.
            val liveOk = "live" in wantVariants || !LIVE_ALBUM.containsMatchIn(r.album)
            titleOk && artistOk && gotVariants == wantVariants && liveOk
        }.sortedWith(
            // Best first: the artist's own release, then album over single, then the earliest.
            compareByDescending<AlbumInfo> { releaseScore(it, wantArtists, "live" in wantVariants) }.thenBy { it.releaseDate.ifEmpty { "9999" } }
        ).firstOrNull()?.let { it.copy(album = tidyAlbum(it.album)) }
    }

    private val COMPILATION = Regex("""(?i)\b(hits|best of|collection|compilation|vol\.?|volume|anthology|sampler)\b""")

    /**
     * How much this looks like the artist's own release (not a label / Various Artists compilation).
     * Compilations get a big minus, so a single by the artist beats them; among the artist's own
     * releases, the album beats the single.
     */
    private val LIVE_ALBUM = Regex("""(?i)(\blive\b|\bunplugged\b|\bin concert\b)""")
    private val REISSUE = Regex("""(?i)\b(expanded|deluxe|anniversary|remaster(ed)?|reissue|special edition|bonus)\b""")

    fun releaseScore(r: AlbumInfo, wantArtists: List<String>, wantsLive: Boolean = false): Int {
        var s = 0
        // A live album unless the song line asks for a live version.
        if (!wantsLive && LIVE_ALBUM.containsMatchIn(r.album)) s -= 6
        // Prefer the original edition over re-releases (small nudge only).
        if (REISSUE.containsMatchIn(r.album)) s -= 1
        val by = Matching.norm(r.albumArtist)
        val isCompilation = by.isNotEmpty() && wantArtists.none { it in by || by in it }
        if (isCompilation || "various" in r.albumArtist.lowercase()) s -= 10
        if (COMPILATION.containsMatchIn(r.album)) s -= 5
        if (!r.album.contains(" - Single") && !r.album.contains(" - EP")) s += 2
        return s
    }

    /** "Aeon - Single" → "Aeon", "Night Drive - EP" → "Night Drive". */
    fun tidyAlbum(a: String) = a.replace(Regex("""\s+-\s+(Single|EP)$"""), "").trim()
}
