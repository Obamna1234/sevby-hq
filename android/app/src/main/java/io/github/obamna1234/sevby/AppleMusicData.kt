package io.github.obamna1234.sevby

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/** A public Apple Music playlist or album, read from its web page. */
data class AmList(val name: String, val songs: List<String>, val declaredCount: Int?)

/**
 * Reads the song list out of a public music.apple.com page. No Apple account is needed: the page
 * itself contains the track list as JSON (for search engines and the web player).
 * Pure logic, no Android, so it can be tested on its own.
 */
object AppleMusicData {

    private val LINK = Regex("""https?://(?:[a-z]+\.)?music\.apple\.com/[^\s"'<>]+""", RegexOption.IGNORE_CASE)

    /** The first Apple Music playlist/album link in [text], or null. */
    fun findLink(text: String): String? =
        LINK.findAll(text).map { it.value.trimEnd('.', ',', ')') }
            .firstOrNull { "/playlist/" in it || "/album/" in it }

    /** Same order as the desktop app: schema.org data first, the embedded app data as a backup. */
    fun parse(html: String): AmList {
        val (ldName, ldSongs) = fromLdJson(html)
        val songs = ldSongs.ifEmpty { fromServerData(html) }
        return AmList(ldName.ifEmpty { name(html) }, songs.distinctSongs(), declaredCount(html))
    }

    /** Links from the embed player point at the same page. */
    fun normalise(link: String): String =
        link.trim().substringBefore('#').replace(Regex("//embed\\.music\\.apple\\.com", RegexOption.IGNORE_CASE), "//music.apple.com")

    // ── serialized-server-data (the main source) ────────────────────────

    private fun fromServerData(html: String): List<String> {
        val raw = scriptById(html, "serialized-server-data") ?: return emptyList()
        val root = runCatching { JSONTokener(raw).nextValue() }.getOrNull() ?: return emptyList()
        val trackLists = mutableListOf<List<String>>()   // arrays inside a "track list" section
        val others = mutableListOf<List<String>>()       // any other array of songs (e.g. seoData.ogSongs)
        walk(root, parentHint = "") { arr, hint ->
            val songs = songsIn(arr)
            if (songs.size >= 1 && songs.size * 2 >= arr.length()) {
                if ("track" in hint) trackLists += songs else others += songs
            }
        }
        if (trackLists.isNotEmpty()) return trackLists.flatten()
        return others.maxByOrNull { it.size }.orEmpty()
    }

    /** Visit every JSON array, telling [visit] what its parent object says about it (id / itemKind / key). */
    private fun walk(node: Any?, parentHint: String, visit: (JSONArray, String) -> Unit) {
        when (node) {
            is JSONObject -> {
                val hint = listOf("id", "itemKind", "kind").joinToString(" ") { node.optString(it) }.lowercase()
                for (key in node.keys()) walk(node.opt(key), "$hint ${key.lowercase()}", visit)
            }
            is JSONArray -> {
                visit(node, parentHint)
                for (i in 0 until node.length()) walk(node.opt(i), parentHint, visit)
            }
        }
    }

    private val NOT_SONGS = setOf("albums", "playlists", "artists", "stations", "curators", "apple-curators")

    /** "Artist - Title" for every song-like object in [arr]. */
    private fun songsIn(arr: JSONArray): List<String> {
        val out = mutableListOf<String>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            if (o.optString("type") in NOT_SONGS) continue
            val a = o.optJSONObject("attributes") ?: o
            val artist = a.optString("artistName").trim()
            val title = (a.optString("name").ifBlank { a.optString("title") }).trim()
            if (artist.isNotEmpty() && title.isNotEmpty()) out += "$artist - $title"
        }
        return out
    }

    // ── JSON-LD (backup) ────────────────────────────────────────────────

    /** (name, songs) from `<script type="application/ld+json">` MusicPlaylist / MusicAlbum `track` lists. */
    private fun fromLdJson(html: String): Pair<String, List<String>> {
        var bestName = ""
        var best = emptyList<String>()
        Regex("""<script[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>""", setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE))
            .findAll(html).forEach { m ->
                val root = runCatching { JSONTokener(m.groupValues[1]).nextValue() }.getOrNull() ?: return@forEach
                val nodes = when (root) {
                    is JSONArray -> (0 until root.length()).mapNotNull { root.optJSONObject(it) }
                    is JSONObject -> listOf(root)
                    else -> emptyList()
                }
                for (node in nodes) {
                    val tracks = node.optJSONArray("track") ?: node.optJSONArray("tracks") ?: continue
                    val owner = artistOf(node.opt("byArtist"))
                    val found = mutableListOf<String>()
                    for (k in 0 until tracks.length()) {
                        val t = tracks.optJSONObject(k)?.let { it.optJSONObject("item") ?: it } ?: continue
                        val title = t.optString("name").trim()
                        val artist = artistOf(t.opt("byArtist")).ifEmpty { owner }
                        if (title.isNotEmpty() && artist.isNotEmpty()) found += "$artist - $title"
                    }
                    if (found.size > best.size) { best = found; bestName = node.optString("name").trim() }
                }
            }
        return bestName to best
    }

    private fun artistOf(v: Any?): String = when (v) {
        is JSONObject -> v.optString("name").trim()
        is JSONArray -> (0 until v.length()).mapNotNull { v.optJSONObject(it)?.optString("name")?.trim() }
            .filter { it.isNotEmpty() }.joinToString(", ")
        is String -> v.trim()
        else -> ""
    }

    // ── Name and size ───────────────────────────────────────────────────

    private fun name(html: String): String {
        val t = meta(html, "og:title") ?: Regex("""<title>(.*?)</title>""", RegexOption.DOT_MATCHES_ALL)
            .find(html)?.groupValues?.get(1) ?: return ""
        return BandcampData.unescape(t.replace("&lrm;", "").replace("&rlm;", ""))
            .replace("‎", "").replace("‏", "")
            .replace(Regex("""\s+on Apple\s*Music\s*$""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*[-–]\s*Apple\s*Music\s*$""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*[-–]\s*(Playlist|Album|EP|Single)\b.*$""", RegexOption.IGNORE_CASE), "")
            .trim()
    }

    /** "50 Songs" in the page description, if Apple says how many there are. */
    private fun declaredCount(html: String): Int? {
        val d = meta(html, "og:description") ?: meta(html, "description") ?: return null
        return Regex("""(\d[\d,.]*)\s+(?:songs?|tracks?)\b""", RegexOption.IGNORE_CASE).find(d)
            ?.groupValues?.get(1)?.replace(",", "")?.replace(".", "")?.toIntOrNull()
    }

    private fun meta(html: String, key: String): String? =
        Regex("""<meta[^>]+(?:property|name)=["']${Regex.escape(key)}["'][^>]*>""", RegexOption.IGNORE_CASE)
            .find(html)?.value?.let { tag ->
                Regex("""content=["']([^"']*)["']""").find(tag)?.groupValues?.get(1)
            }

    private fun scriptById(html: String, id: String): String? =
        Regex("""<script[^>]*id=["']${Regex.escape(id)}["'][^>]*>(.*?)</script>""", RegexOption.DOT_MATCHES_ALL)
            .find(html)?.groupValues?.get(1)

    private fun List<String>.distinctSongs(): List<String> {
        // The same song can be picked up from two copies of the list in the page.
        val seen = LinkedHashSet<String>()
        return filter { seen.add(it.lowercase()) }
    }
}
