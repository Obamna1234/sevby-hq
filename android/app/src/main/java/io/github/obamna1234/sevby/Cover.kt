package io.github.obamna1234.sevby

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Cover art from a video thumbnail: trim YouTube's black bars, then either crop to a square
 * (nearly-square pictures) or show the whole picture centred in a black square (wide ones).
 */
object Cover {
    private const val MAX_SIDE = 800

    /**
     * Download an album cover (e.g. from iTunes). Already-square JPEGs are used as they are;
     * anything else goes through [squareJpeg]. Null if it can't be downloaded or read.
     */
    fun fromUrl(url: String, tmp: File): ByteArray? = runCatching {
        val con = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 20_000
        }
        val bytes = try {
            if (con.responseCode != 200) return null
            con.inputStream.use { it.readBytes() }
        } finally {
            con.disconnect()
        }
        if (bytes.size < 1000) return null
        val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
        if (opts.outWidth <= 0) return null
        val isJpeg = bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte()
        if (isJpeg && opts.outWidth == opts.outHeight && opts.outWidth <= 1400) return bytes
        tmp.writeBytes(bytes)
        squareJpeg(tmp).also { tmp.delete() }
    }.getOrNull()

    /** Square JPEG, at most 800×800, or null if the image can't be read. */
    fun squareJpeg(image: File): ByteArray? = runCatching {
        val src = BitmapFactory.decodeFile(image.absolutePath) ?: return null
        val px = IntArray(src.width * src.height)
        src.getPixels(px, 0, src.width, 0, 0, src.width, src.height)
        val plan = CoverMath.plan(px, src.width, src.height)
        val b = plan.box
        val out: Bitmap
        if (plan.crop) {
            var sq = Bitmap.createBitmap(src, b.x, b.y, b.w, b.h)
            if (b.w > MAX_SIDE) sq = Bitmap.createScaledBitmap(sq, MAX_SIDE, MAX_SIDE, true)
            out = sq
        } else {
            val side = minOf(maxOf(b.w, b.h), MAX_SIDE)
            val scale = side.toFloat() / maxOf(b.w, b.h)
            val dw = (b.w * scale).toInt()
            val dh = (b.h * scale).toInt()
            out = Bitmap.createBitmap(side, side, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(out)
            canvas.drawColor(Color.BLACK)
            val dst = Rect((side - dw) / 2, (side - dh) / 2, (side - dw) / 2 + dw, (side - dh) / 2 + dh)
            canvas.drawBitmap(src, Rect(b.x, b.y, b.x + b.w, b.y + b.h), dst, Paint(Paint.FILTER_BITMAP_FLAG))
        }
        val bytes = ByteArrayOutputStream()
        out.compress(Bitmap.CompressFormat.JPEG, 90, bytes)
        bytes.toByteArray()
    }.getOrNull()
}
