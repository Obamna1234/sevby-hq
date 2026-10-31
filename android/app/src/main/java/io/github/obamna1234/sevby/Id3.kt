package io.github.obamna1234.sevby

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.OutputStream
import java.io.RandomAccessFile

/** Tag values for one MP3. Blank fields are left out. */
data class Tags(
    val title: String,
    val artist: String = "",
    val albumArtist: String = "",
    val album: String = "",
    val year: String = "",
    val track: String = "",
)

/**
 * Minimal ID3v2.3 writer (the version every music app and car stereo reads).
 * Copies an MP3 to [out] with any old tags removed and SEVBY's tags + cover in front.
 */
object Id3 {

    fun writeTagged(mp3: File, tags: Tags, coverJpeg: ByteArray?, out: OutputStream) {
        val (start, end) = audioRange(mp3)
        out.write(buildTag(tags, coverJpeg))
        RandomAccessFile(mp3, "r").use { f ->
            f.seek(start)
            val buf = ByteArray(64 * 1024)
            var left = end - start
            while (left > 0) {
                val n = f.read(buf, 0, minOf(buf.size.toLong(), left).toInt())
                if (n <= 0) break
                out.write(buf, 0, n)
                left -= n
            }
        }
    }

    /** Byte range of the audio itself, skipping an ID3v2 tag at the start and ID3v1 at the end. */
    private fun audioRange(mp3: File): Pair<Long, Long> {
        RandomAccessFile(mp3, "r").use { f ->
            var start = 0L
            var end = f.length()
            val head = ByteArray(10)
            if (end >= 10) {
                f.readFully(head)
                if (head[0] == 'I'.code.toByte() && head[1] == 'D'.code.toByte() && head[2] == '3'.code.toByte()) {
                    val size = ((head[6].toInt() and 0x7F) shl 21) or ((head[7].toInt() and 0x7F) shl 14) or
                        ((head[8].toInt() and 0x7F) shl 7) or (head[9].toInt() and 0x7F)
                    val footer = if (head[5].toInt() and 0x10 != 0) 10 else 0
                    start = 10L + size + footer
                }
            }
            if (end - start > 128) {
                val tail = ByteArray(3)
                f.seek(end - 128)
                f.readFully(tail)
                if (String(tail, Charsets.ISO_8859_1) == "TAG") end -= 128
            }
            return start.coerceAtMost(end) to end
        }
    }

    private fun buildTag(t: Tags, cover: ByteArray?): ByteArray {
        val frames = ByteArrayOutputStream()
        fun text(id: String, value: String) {
            if (value.isBlank()) return
            // Encoding 1 = UTF-16 with BOM, so any language/emoji works.
            val body = byteArrayOf(1) + byteArrayOf(0xFF.toByte(), 0xFE.toByte()) +
                value.trim().toByteArray(Charsets.UTF_16LE)
            frame(frames, id, body)
        }
        text("TIT2", t.title)
        text("TPE1", t.artist)
        text("TPE2", t.albumArtist)
        text("TALB", t.album)
        text("TYER", t.year)
        text("TRCK", t.track)
        if (cover != null) {
            val body = ByteArrayOutputStream()
            body.write(0)                                    // text encoding: ISO-8859-1
            body.write("image/jpeg".toByteArray(Charsets.ISO_8859_1)); body.write(0)
            body.write(3)                                    // picture type: front cover
            body.write(0)                                    // empty description
            body.write(cover)
            frame(frames, "APIC", body.toByteArray())
        }
        val payload = frames.toByteArray()
        val n = payload.size
        val header = byteArrayOf(
            'I'.code.toByte(), 'D'.code.toByte(), '3'.code.toByte(), 3, 0, 0,
            ((n shr 21) and 0x7F).toByte(), ((n shr 14) and 0x7F).toByte(),
            ((n shr 7) and 0x7F).toByte(), (n and 0x7F).toByte(),
        )
        return header + payload
    }

    private fun frame(out: ByteArrayOutputStream, id: String, body: ByteArray) {
        out.write(id.toByteArray(Charsets.ISO_8859_1))
        val n = body.size
        out.write(byteArrayOf((n ushr 24).toByte(), (n ushr 16).toByte(), (n ushr 8).toByte(), n.toByte()))
        out.write(byteArrayOf(0, 0))
        out.write(body)
    }
}
