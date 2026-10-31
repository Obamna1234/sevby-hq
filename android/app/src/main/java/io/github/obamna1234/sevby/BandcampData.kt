package io.github.obamna1234.sevby

import org.json.JSONObject

/** One Bandcamp search result (a track). */
data class BcHit(
    val name: String,
    val band: String,
    val url: String,
    val album: String = "",
)

/** Everything SEVBY needs from a Bandcamp track page. */
data class BcTrack(
    val pageUrl: String,
    val title: String,
    val artist: String,
    val album: String,
    val year: String,
    val trackNumber: Int,
    val seconds: Int,
    /** Bandcamp's own 128 kbps MP3 stream; empty if the track can't be streamed. */
    val mp3Url: String,
    val coverUrl: String,
)

/**
 * Bandcamp logic that needs no network or Android (so it can be tested anywhere):
 * picking the right search result and reading a track page.
 */
object BandcampData {

    private val COMPILATION = Regex(
        """(?i)\b(compilation|vol\.?|volume|various|sampler|presents|best of|collection|anthology|\d{2,3})\b"""
    )
    private val VARIANTS = listOf("remix", "live", "instrumental", "acoustic", "cover", "edit", "mix", "version", "karaoke")

    /** Tribute bands, remakes, covers, karaoke, "dubs", lullaby/8-bit versions … (same list as the desktop app). */
    private val NOT_ORIGINAL = Regex(
        """remake|re-?record|tribute|\bcovers?\b|bootleg|karaoke|\bdubs?\b|made famous|originally (performed|by)|""" +
            """in the style of|\bversions?\b|rerecord|lullaby|8-?bit|piano (version|tribute)""",
        RegexOption.IGNORE_CASE,
    )
    private val LIVE_ALBUM = Regex("""\blive\b|unplugged|in concert""", RegexOption.IGNORE_CASE)

    private fun artists(artist: String): List<String> =
        artist.split(Regex("""\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b|\band\b)\s*""", RegexOption.IGNORE_CASE))
            .map { Matching.norm(it) }.filter { it.isNotEmpty() }

    private fun variants(title: String) =
        VARIANTS.filter { Matching.words(Matching.cleanTitle(title)).contains(it) }.toSet()

    /**
     * True if the Bandcamp band name is the artist asked for: equal to one of the artists (or the whole
     * artist string), or one name contains the other and they are nearly the same length (≥ 75 %).
     * "Queen" ≠ "Queen Mary Band", but "Power Glove & PYLOT" = "Power Glove".
     */
    fun bandMatches(artist: String, band: String): Boolean {
        val b = Matching.norm(band)
        if (b.isEmpty()) return false
        return nameMatches(artistNames(artist), b)
    }

    /** The split artists plus the whole artist string ("&" read as "and", and also with it left out). */
    private fun artistNames(artist: String): List<String> =
        (artists(artist) + Matching.norm(artist) + Matching.norm(artist.replace("&", " ")))
            .filter { it.isNotEmpty() }.distinct()

    /** Equal, or one contains the other and they are nearly the same length (≥ 75 %). */
    private fun nameMatches(candidates: List<String>, name: String): Boolean = candidates.any { c ->
        c == name || ((c in name || name in c) && minOf(c.length, name.length).toDouble() / maxOf(c.length, name.length) >= 0.75)
    }

    /**
     * The account part of a Bandcamp page address: "https://pylot.bandcamp.com/track/x" → "pylot",
     * and for a custom domain "https://store.sigurros.com/track/x" → "sigurros".
     */
    fun pageAccount(url: String): String {
        val host = Regex("""https?://([^/:?#]+)""").find(url)?.groupValues?.get(1)?.lowercase().orEmpty()
        val parts = host.split('.').filter { it.isNotEmpty() }
        if (parts.size < 2) return host
        if (host.endsWith(".bandcamp.com")) return parts.first()
        val secondLevel = parts[parts.size - 2]
        // "label.co.uk" style addresses: the account is one step further left.
        return if (parts.size >= 3 && secondLevel in setOf("co", "com", "org", "net")) parts[parts.size - 3] else secondLevel
    }

    /** True if the page address belongs to the artist (artist.bandcamp.com or the artist's own domain). */
    fun isArtistPage(artist: String, url: String): Boolean {
        val account = Matching.norm(pageAccount(url))
        return account.isNotEmpty() && nameMatches(artistNames(artist), account)
    }

    /** Same album, ignoring case, punctuation, accents, a leading "The/A/An" and "(Remastered)"-style extras. */
    fun sameAlbum(a: String, b: String): Boolean {
        val x = Matching.titleKey(a)
        return x.isNotEmpty() && x == Matching.titleKey(b)
    }

    /**
     * Why a release isn't the original recording, or null if it's fine: remakes, tributes, covers,
     * dubs… (unless the list line asks for that), and live albums (unless the title asks for live).
     */
    fun notOriginal(song: String, trackName: String, album: String): String? {
        val (_, title) = SongList.split(song)
        val text = "$trackName $album"
        if (NOT_ORIGINAL.containsMatchIn(text) && !NOT_ORIGINAL.containsMatchIn(song)) return "not the original release"
        if (album.isNotEmpty() && LIVE_ALBUM.containsMatchIn(album) && "live" !in Matching.words(title)) return "live album"
        return null
    }

    /** True if [gotTitle] by [gotArtist] is the song asked for (same rules as the iTunes lookup). */
    fun isSameSong(song: String, gotTitle: String, gotArtist: String): Boolean {
        val (artist, title) = SongList.split(song)
        val want = Matching.titleKey(title)
        val got = Matching.titleKey(gotTitle)
        if (want.isEmpty() || got.isEmpty()) return false
        val wantArtists = artists(artist)
        if (wantArtists.isEmpty()) return false
        return bandMatches(artist, gotArtist) && got == want && variants(title) == variants(gotTitle)
    }

    /** Results that pass the title, band-name and version checks (before the artist-page rule). */
    private fun valid(hits: List<BcHit>, song: String): List<BcHit> =
        hits.filter { it.url.contains("/track/") && isSameSong(song, it.name, it.band) && notOriginal(song, it.name, it.album) == null }
            .distinctBy { it.url }

    /**
     * True if the only matching results are on other accounts (label, DJ, fan, compilation), so the
     * iTunes album is needed to decide. Lets the caller skip the iTunes lookup when it isn't.
     */
    fun needsItunesAlbum(hits: List<BcHit>, song: String): Boolean {
        val (artist, _) = SongList.split(song)
        val v = valid(hits, song)
        return v.isNotEmpty() && v.none { isArtistPage(artist, it.url) }
    }

    /**
     * Matching results, best first, in two steps:
     * 1. results on the artist's own page (artist.bandcamp.com or the artist's own domain);
     * 2. only if there are none: results from other accounts whose album equals the album iTunes lists
     *    for this song ([itunesAlbum]). No iTunes album, or a different/empty one → rejected (use YouTube).
     * Real albums before compilations, albums before singles.
     */
    fun choose(hits: List<BcHit>, song: String, itunesAlbum: String? = null): List<BcHit> {
        val (artist, _) = SongList.split(song)
        val v = valid(hits, song)
        val own = v.filter { isArtistPage(artist, it.url) }
        val picked = own.ifEmpty {
            if (itunesAlbum.isNullOrBlank()) emptyList()
            else v.filter { it.album.isNotBlank() && sameAlbum(it.album, itunesAlbum) }
        }
        return picked.sortedByDescending { h ->
            var s = 0
            if (h.album.isNotEmpty() && COMPILATION.containsMatchIn(h.album)) s -= 5
            if (h.album.isNotEmpty() && !COMPILATION.containsMatchIn(h.album)) s += 2
            s
        }
    }

    /** Results from Bandcamp's search API (both of its reply styles). */
    fun parseSearch(json: String): List<BcHit> {
        val root = JSONObject(json)
        val arr = root.optJSONObject("auto")?.optJSONArray("results") ?: root.optJSONArray("results") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            val r = arr.optJSONObject(i) ?: return@mapNotNull null
            if (r.optString("type") != "t") return@mapNotNull null
            val url = r.optString("item_url_path").ifEmpty { r.optString("item_url_root") }.ifEmpty { r.optString("url") }
            if (url.isEmpty()) return@mapNotNull null
            BcHit(
                name = r.optString("name"),
                band = r.optString("band_name"),
                url = fixUrl(url),
                album = r.optString("album_name"),
            )
        }
    }

    /** Some Bandcamp links come doubled ("https://x.bandcamp.comhttps://...") – keep the last one. */
    fun fixUrl(url: String): String {
        val i = url.lastIndexOf("https://")
        return if (i > 0) url.substring(i) else url
    }

    /** Pull the data-tralbum / data-embed attributes and og:image out of a page's HTML. */
    fun parseHtml(html: String, pageUrl: String): BcTrack? {
        fun attr(name: String): String? =
            Regex("""data-$name\s*=\s*(["'])(\{.+?\})\1""", RegexOption.DOT_MATCHES_ALL).find(html)
                ?.groupValues?.get(2)?.let { unescape(it) }
        val tralbum = attr("tralbum") ?: return null
        val og = Regex("""<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']""").find(html)?.groupValues?.get(1)
        return parsePage(tralbum, attr("embed"), og, pageUrl)
    }

    /** Read a track page's data (the JSON behind data-tralbum, plus data-embed for the album name). */
    fun parsePage(tralbumJson: String, embedJson: String?, ogImage: String?, pageUrl: String): BcTrack? = runCatching {
        val t = JSONObject(tralbumJson)
        val embed = embedJson?.let { runCatching { JSONObject(it) }.getOrNull() }
        val current = t.optJSONObject("current") ?: JSONObject()
        val info = t.optJSONArray("trackinfo")?.optJSONObject(0)
        val mp3 = info?.optJSONObject("file")?.optString("mp3-128").orEmpty()
        val title = info?.optString("title").orEmpty().ifEmpty { current.optString("title") }
        val artist = embed?.optString("artist").orEmpty()
            .ifEmpty { current.optString("artist") }.ifEmpty { t.optString("artist") }
        val dates = listOf(current.optString("release_date"), t.optString("album_release_date"), current.optString("publish_date"))
        val year = dates.firstNotNullOfOrNull { Regex("""\b(19|20)\d{2}\b""").find(it)?.value }.orEmpty()
        val artId = t.optLong("art_id", 0L)
        val cover = when {
            artId > 0 -> "https://f4.bcbits.com/img/a${artId}_10.jpg"
            !ogImage.isNullOrEmpty() -> ogImage
            else -> ""
        }
        BcTrack(
            pageUrl = pageUrl,
            title = title,
            artist = artist,
            album = embed?.optString("album_title").orEmpty(),
            year = year,
            trackNumber = info?.optInt("track_num", 0) ?: 0,
            seconds = (info?.optDouble("duration", 0.0) ?: 0.0).toInt(),
            mp3Url = if (mp3.startsWith("//")) "https:$mp3" else mp3,
            coverUrl = cover,
        )
    }.getOrNull()

    /** HTML attribute text → plain text ("&quot;" → '"', "&#39;" → "'", "&amp;" → "&" ...). */
    fun unescape(s: String): String =
        Regex("""&(#x[0-9a-fA-F]+|#\d+|quot|amp|lt|gt|apos);""").replace(s) { m ->
            when (val e = m.groupValues[1]) {
                "quot" -> "\""
                "amp" -> "&"
                "lt" -> "<"
                "gt" -> ">"
                "apos" -> "'"
                else -> if (e.startsWith("#x")) String(Character.toChars(e.substring(2).toInt(16)))
                        else String(Character.toChars(e.substring(1).toInt()))
            }
        }
}
