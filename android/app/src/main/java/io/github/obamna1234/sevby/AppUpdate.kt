package io.github.obamna1234.sevby

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

/**
 * "A new SEVBY is available": looks for a newer android-v… release on GitHub. The APK is opened in the
 * browser, which downloads it; Android then installs it over this one (same signing key, so settings,
 * folder and queue are kept).
 */
object AppUpdate {

    private const val RELEASES = "https://api.github.com/repos/Obamna1234/sevby-hq/releases?per_page=30"

    fun currentVersion(context: Context): String =
        runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull().orEmpty()

    /** (checked, newest Android release). checked = false when GitHub couldn't be reached. */
    suspend fun latest(): Pair<Boolean, AppRelease?> = withContext(Dispatchers.IO) {
        runCatching {
            val con = (URL(RELEASES).openConnection() as HttpURLConnection).apply {
                connectTimeout = 10_000
                readTimeout = 15_000
                setRequestProperty("Accept", "application/vnd.github+json")
                setRequestProperty("User-Agent", "SEVBY-Android")
            }
            try {
                if (con.responseCode != 200) false to null
                else true to AppUpdateData.newest(con.inputStream.bufferedReader().readText(), Build.SUPPORTED_ABIS.toList())
            } finally {
                con.disconnect()
            }
        }.getOrDefault(false to null)
    }

    /** Check at most once a day; remembers the result. Returns the release if it's newer than this app. */
    suspend fun checkDaily(context: Context, prefs: Prefs, force: Boolean = false): AppRelease? {
        val now = System.currentTimeMillis()
        if (force || now - prefs.lastAppCheck > 24 * 60 * 60 * 1000L) {
            val (checked, rel) = latest()
            if (checked) {
                prefs.latestApp = rel?.version.orEmpty()
                prefs.latestAppApk = rel?.apkUrl.orEmpty()
                prefs.latestAppPage = rel?.pageUrl.orEmpty()
                prefs.lastAppCheck = now
            }
        }
        return available(context, prefs)
    }

    /** The remembered release, if it's newer than the installed app. */
    fun available(context: Context, prefs: Prefs): AppRelease? {
        val v = prefs.latestApp
        if (v.isEmpty() || prefs.latestAppApk.isEmpty()) return null
        return if (AppUpdateData.isNewer(v, currentVersion(context))) AppRelease(v, prefs.latestAppApk, prefs.latestAppPage) else null
    }

    fun open(context: Context, url: String): Boolean =
        runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))); true }.getOrDefault(false)
}
