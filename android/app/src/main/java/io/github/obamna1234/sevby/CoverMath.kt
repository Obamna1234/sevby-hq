package io.github.obamna1234.sevby

/** A rectangle inside an image: left, top, width, height. */
data class Box(val x: Int, val y: Int, val w: Int, val h: Int)

/**
 * Pure pixel maths for cover art (no Android types, so it can be tested anywhere).
 * YouTube thumbnails often have black bars baked in (letterbox top/bottom, pillarbox
 * left/right, or both). Trim those first, then take the centre square of what's left.
 */
object CoverMath {

    /** A row/column counts as "bar" when nearly all its pixels are very dark. */
    private const val DARK = 28          // max brightness (0-255) for a bar pixel
    private const val SHARE = 0.97       // share of pixels that must be dark

    private fun luma(argb: Int): Int {
        val r = (argb shr 16) and 0xFF
        val g = (argb shr 8) and 0xFF
        val b = argb and 0xFF
        return (r * 299 + g * 587 + b * 114) / 1000
    }

    /** The picture without its black bars. Returns the whole image if trimming would remove too much. */
    fun contentBox(px: IntArray, w: Int, h: Int): Box {
        fun rowDark(y: Int, x0: Int, x1: Int): Boolean {
            var dark = 0
            for (x in x0 until x1) if (luma(px[y * w + x]) <= DARK) dark++
            return dark >= (x1 - x0) * SHARE
        }
        fun colDark(x: Int, y0: Int, y1: Int): Boolean {
            var dark = 0
            for (y in y0 until y1) if (luma(px[y * w + x]) <= DARK) dark++
            return dark >= (y1 - y0) * SHARE
        }
        // Real bars are the same size on both sides, so only trim the amount both sides share.
        // (A dark cover that is only dark at the top keeps its top.)
        var top = 0; var bottom = h
        while (top < h / 2 && rowDark(top, 0, w)) top++
        while (bottom > h / 2 && rowDark(bottom - 1, 0, w)) bottom--
        val tb = minOf(top, h - bottom)
        top = tb; bottom = h - tb
        var left = 0; var right = w
        while (left < w / 2 && colDark(left, top, bottom)) left++
        while (right > w / 2 && colDark(right - 1, top, bottom)) right--
        val lr = minOf(left, w - right)
        left = lr; right = w - lr
        val box = Box(left, top, right - left, bottom - top)
        // An almost entirely dark image (e.g. a black title card): don't trim at all.
        return if (box.w < w / 4 || box.h < h / 4) Box(0, 0, w, h) else box
    }

    /** Content wider/taller than this ratio is shown whole (with bars) instead of cropped. */
    const val MAX_CROP_RATIO = 1.25

    /**
     * How to make a square cover: [crop] = take the centre square of [box];
     * otherwise fit the whole [box] inside a black square (wide video pictures).
     */
    data class Plan(val box: Box, val crop: Boolean)

    fun plan(px: IntArray, w: Int, h: Int): Plan {
        val c = contentBox(px, w, h)
        val ratio = maxOf(c.w, c.h).toDouble() / minOf(c.w, c.h)
        return if (ratio <= MAX_CROP_RATIO) Plan(squareOf(c), true) else Plan(c, false)
    }

    private fun squareOf(c: Box): Box {
        val side = minOf(c.w, c.h)
        return Box(c.x + (c.w - side) / 2, c.y + (c.h - side) / 2, side, side)
    }

    /** Centre square of the content box. */
    fun squareBox(px: IntArray, w: Int, h: Int): Box {
        return squareOf(contentBox(px, w, h))
    }
}
