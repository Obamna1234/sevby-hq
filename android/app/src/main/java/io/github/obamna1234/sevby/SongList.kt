package io.github.obamna1234.sevby

/** Song-list helpers, ported from sevby_app.py so both versions read lists the same way. */
object SongList {

    /** One song per line. Skips blanks, "===" headers and links; trims Chosic-style "  →  " suffixes. */
    fun parse(text: String): List<String> =
        text.removePrefix("﻿").lines().mapNotNull { raw ->
            var line = raw.trim()
            if (line.isEmpty() || line.startsWith("===") || line.startsWith("http")) return@mapNotNull null
            if ("  →  " in line) line = line.substringBefore("  →  ").trim()
            line.ifEmpty { null }
        }

    private val TITLE_COLS = listOf("track name", "track", "title", "name", "song", "song name", "track title")
    private val ARTIST_COLS = listOf("artist name(s)", "artist name", "artist", "artists", "artist(s)", "album artist")

    /**
     * Turn an M3U playlist, or a CSV / tab-separated export (Chosic, Exportify, TuneMyMusic, Soundiiz,
     * Apple Music...) into "Artist - Title" lines. Null if the text isn't one of those, so a normal
     * song list is left alone. Same rules as the desktop app.
     */
    fun fromExport(text: String): List<String>? {
        val lines = text.removePrefix("\uFEFF").lines().filter { it.isNotBlank() }
        if (lines.isEmpty()) return null
        // M3U / M3U8: "#EXTINF:123,Artist - Title"
        if (lines[0].trim().uppercase().startsWith("#EXTM3U") || lines.take(5).any { it.startsWith("#EXTINF:") }) {
            return lines.filter { it.startsWith("#EXTINF:") && ',' in it }
                .map { it.substringAfter(',').trim() }.filter { it.isNotEmpty() }.ifEmpty { null }
        }
        // CSV / TSV with a header row naming a title column and an artist column
        val head = lines[0]
        val delim = when { '\t' in head -> '\t'; ',' in head -> ','; else -> return null }
        val rows = csvRows(lines.joinToString("\n"), delim)
        if (rows.isEmpty()) return null
        val cols = rows[0].map { it.trim().lowercase() }
        val ti = TITLE_COLS.firstOrNull { it in cols }?.let { cols.indexOf(it) } ?: return null
        val ai = ARTIST_COLS.firstOrNull { it in cols }?.let { cols.indexOf(it) } ?: return null
        if (ti == ai) return null
        val out = mutableListOf<String>()
        for (r in rows.drop(1)) {
            if (r.size <= maxOf(ti, ai)) continue
            val title = r[ti].trim()
            val artist = r[ai].trim().split(Regex("""\s*[;/]\s*""")).first().trim()
            if (title.isNotEmpty() && artist.isNotEmpty()) out += "$artist - $title"
            else if (title.isNotEmpty()) out += title
        }
        return out.ifEmpty { null }
    }

    /** Minimal CSV reader: quoted fields, "" inside quotes, line breaks inside quotes. */
    private fun csvRows(text: String, delim: Char): List<List<String>> {
        val rows = mutableListOf<List<String>>()
        var row = mutableListOf<String>()
        val field = StringBuilder()
        var quoted = false
        var i = 0
        while (i < text.length) {
            val c = text[i]
            if (quoted) {
                if (c == '"') {
                    if (i + 1 < text.length && text[i + 1] == '"') { field.append('"'); i++ } else quoted = false
                } else field.append(c)
            } else when (c) {
                '"' -> quoted = true
                delim -> { row += field.toString(); field.clear() }
                '\r' -> {}
                '\n' -> { row += field.toString(); field.clear(); rows += row; row = mutableListOf() }
                else -> field.append(c)
            }
            i++
        }
        if (field.isNotEmpty() || row.isNotEmpty()) { row += field.toString(); rows += row }
        return rows
    }

    /** File bytes as text: UTF-16 (Apple Music on Windows) or UTF-8. */
    fun decode(bytes: ByteArray): String {
        val bom = if (bytes.size >= 2) (bytes[0].toInt() and 0xFF) to (bytes[1].toInt() and 0xFF) else null
        return if (bom == 0xFF to 0xFE || bom == 0xFE to 0xFF) String(bytes, Charsets.UTF_16)
        else String(bytes, Charsets.UTF_8).removePrefix("\uFEFF")
    }

    private val SMALL_WORDS = setOf("a", "an", "the", "of", "and", "or", "but", "in", "on", "at", "to", "for", "by", "vs", "with", "from")

    /**
     * A line typed all in lowercase gets proper capitals ("kataklysm - the black sheep" →
     * "Kataklysm - The Black Sheep"). Small words stay lowercase unless they come first in the artist or
     * title; letters after "(" and "/" are capitalised too. A line with any capital is left exactly as is.
     */
    fun titleCaseIfLower(line: String): String {
        if (line.any { it.isUpperCase() } || line.none { it.isLetter() }) return line
        return line.split(Regex("""(?<=\s[-–—]\s)|(?=\s[-–—]\s)""")).joinToString("") { part ->
            if (Regex("""\s[-–—]\s""").matches(part)) part else titleCasePart(part)
        }
    }

    private fun titleCasePart(part: String): String {
        val words = part.split(" ")
        var first = true
        return words.joinToString(" ") { w ->
            if (w.isEmpty()) return@joinToString w
            val bare = w.trimStart('(', '[', '"', '\'').lowercase()
            val keepSmall = !first && bare in SMALL_WORDS && !w.startsWith("(")
            first = false
            if (keepSmall) w else capitalise(w)
        }
    }

    /** Capitalise the first letter of a word and every letter that follows "(" or "/". */
    private fun capitalise(w: String): String {
        val sb = StringBuilder(w)
        var up = true
        for (i in sb.indices) {
            val c = sb[i]
            if (c.isLetter()) {
                if (up) sb.setCharAt(i, c.uppercaseChar())
                up = false
            } else if (c == '(' || c == '/') {
                up = true
            }
        }
        return sb.toString()
    }

    /** (artist, title). Artist is "" when the line has no " - " separator. */
    fun split(song: String): Pair<String, String> {
        for (sep in listOf(" - ", " – ", " — ")) {
            val i = song.indexOf(sep)
            if (i >= 0) return song.substring(0, i).trim() to song.substring(i + sep.length).trim()
        }
        return "" to song.trim()
    }

    /** Same rules as the desktop app, so files get identical names on every platform. */
    fun safeFileName(name: String): String =
        name.replace(Regex("""[<>:"/\\|?*]"""), "")
            .replace(Regex("""\s+"""), " ")
            .trim()
            .take(180)
}

/**
 * Songs already seen in this run, by file name in any capitals, under the typed and the official
 * spelling. A second "kataklysm - black sheep" after "Kataklysm - The Black Sheep" is "listed twice".
 */
class SeenSongs {
    private val keys = HashSet<String>()
    private fun key(s: String) = SongList.safeFileName(s).lowercase()
    fun contains(vararg names: String): Boolean = names.any { key(it) in keys }
    fun add(vararg names: String) { names.forEach { keys += key(it) } }
}
