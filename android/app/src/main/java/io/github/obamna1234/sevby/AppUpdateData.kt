package io.github.obamna1234.sevby

import org.json.JSONArray

/** A newer SEVBY for Android release on GitHub. */
data class AppRelease(val version: String, val apkUrl: String, val pageUrl: String)

/**
 * Pure logic for the "new SEVBY version" check: pick the newest android-v… release from the
 * GitHub releases list and the APK that fits this phone. No Android, so it can be tested on its own.
 */
object AppUpdateData {

    const val TAG_PREFIX = "android-v"

    /**
     * The newest non-draft `android-v…` release in [json] (GitHub's /releases list), with the APK for
     * the first ABI in [abis] that has one (else the universal APK). Null if there is none.
     */
    fun newest(json: String, abis: List<String>): AppRelease? {
        val arr = runCatching { JSONArray(json) }.getOrNull() ?: return null
        var best: AppRelease? = null
        for (i in 0 until arr.length()) {
            val r = arr.optJSONObject(i) ?: continue
            val tag = r.optString("tag_name")
            if (!tag.startsWith(TAG_PREFIX) || r.optBoolean("draft")) continue
            val version = tag.removePrefix(TAG_PREFIX)
            val assets = r.optJSONArray("assets") ?: continue
            val apks = (0 until assets.length()).mapNotNull { assets.optJSONObject(it) }
                .filter { it.optString("name").endsWith(".apk") }
            val pick = abis.firstNotNullOfOrNull { abi -> apks.firstOrNull { it.optString("name").endsWith("-$abi.apk") } }
                ?: apks.firstOrNull { it.optString("name").endsWith("-universal.apk") }
                ?: continue                                    // release without APKs yet (still building)
            val rel = AppRelease(version, pick.optString("browser_download_url"), r.optString("html_url"))
            if (best == null || isNewer(rel.version, best.version)) best = rel
        }
        return best
    }

    /**
     * True if version [a] is newer than [b]. Numbers compare as numbers ("0.10.0" > "0.9.2"), and a
     * finished version beats its test builds: 0.2.0 > 0.2.0-rc > 0.2.0-beta.2 > 0.2.0-beta > 0.2.0-dev.5.
     */
    fun isNewer(a: String, b: String): Boolean = compare(a, b) > 0

    fun compare(a: String, b: String): Int {
        val (coreA, preA) = split(a)
        val (coreB, preB) = split(b)
        for (i in 0 until maxOf(coreA.size, coreB.size)) {
            val c = coreA.getOrElse(i) { 0 }.compareTo(coreB.getOrElse(i) { 0 })
            if (c != 0) return c
        }
        if (preA.isEmpty() && preB.isEmpty()) return 0
        if (preA.isEmpty()) return 1                               // a finished version beats its test builds
        if (preB.isEmpty()) return -1
        for (i in 0 until maxOf(preA.size, preB.size)) {
            val x = preA.getOrNull(i) ?: return -1
            val y = preB.getOrNull(i) ?: return 1
            val c = partCompare(x, y)
            if (c != 0) return c
        }
        return 0
    }

    private val WORD_RANK = mapOf("dev" to 0, "alpha" to 1, "beta" to 2, "rc" to 3)

    private fun partCompare(x: String, y: String): Int {
        val nx = x.toIntOrNull()
        val ny = y.toIntOrNull()
        return when {
            nx != null && ny != null -> nx.compareTo(ny)
            nx != null -> 1                                        // "beta.2" vs "beta.rc": numbers after words
            ny != null -> -1
            else -> (WORD_RANK[x] ?: 2).compareTo(WORD_RANK[y] ?: 2).let { if (it != 0) it else x.compareTo(y) }
        }
    }

    /** "0.1.0-beta.2" → ([0, 1, 0], ["beta", "2"]). Anything after a space (e.g. "(stage 5)") is ignored. */
    private fun split(v: String): Pair<List<Int>, List<String>> {
        val s = v.trim().removePrefix("v").substringBefore(' ').lowercase()
        val core = s.substringBefore('-')
        val pre = if ('-' in s) s.substringAfter('-') else ""
        return core.split('.').map { it.toIntOrNull() ?: 0 } to
            pre.split('.', '-').filter { it.isNotEmpty() }
    }
}
