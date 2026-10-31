package io.github.obamna1234.sevby

import android.content.Context
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLException
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicInteger

/** Result of one yt-dlp run. [ok] is false when yt-dlp exited with an error or was stopped. */
data class RunResult(val ok: Boolean, val out: String, val err: String, val stopped: Boolean = false) {
    /** The most useful error line (yt-dlp's "ERROR: ..." line if there is one). */
    val errorLine: String
        get() {
            val lines = err.lines().map { it.trim() }.filter { it.isNotEmpty() }
            return (lines.lastOrNull { it.startsWith("ERROR:") }?.removePrefix("ERROR:")?.trim()
                ?: lines.lastOrNull() ?: "unknown error")
        }
}

/**
 * Thin wrapper around youtubedl-android: starts the bundled Python + yt-dlp + FFmpeg once,
 * then runs yt-dlp with plain command-line arguments (the same ones the desktop SEVBY uses).
 */
object Engine {
    private val lock = Mutex()
    private val ids = AtomicInteger()
    @Volatile var ready = false
        private set

    /** Unpacks Python / FFmpeg on first launch (a few seconds), then is instant. */
    suspend fun init(context: Context) = lock.withLock {
        if (ready) return@withLock
        withContext(Dispatchers.IO) {
            YoutubeDL.getInstance().init(context.applicationContext)
            FFmpeg.getInstance().init(context.applicationContext)
        }
        ready = true
    }

    suspend fun run(vararg args: String): RunResult = run(args.toList())

    /**
     * Run yt-dlp with these arguments. Never throws for a normal yt-dlp failure.
     * [onStart] receives an id that [stop] accepts; [onProgress] gets download percent (0-100).
     */
    suspend fun run(
        args: List<String>,
        onStart: ((String) -> Unit)? = null,
        onProgress: ((Float) -> Unit)? = null,
    ): RunResult = withContext(Dispatchers.IO) {
        val req = YoutubeDLRequest(emptyList<String>()).addCommands(args)
        val id = "sevby-${ids.incrementAndGet()}"
        onStart?.invoke(id)
        try {
            val r = YoutubeDL.getInstance().execute(req, id, false) { pct, _, _ -> onProgress?.invoke(pct) }
            RunResult(r.exitCode == 0, r.out, r.err)
        } catch (e: YoutubeDL.CanceledException) {
            RunResult(false, "", "stopped", stopped = true)
        } catch (e: InterruptedException) {
            RunResult(false, "", "stopped", stopped = true)
        } catch (e: YoutubeDLException) {
            RunResult(false, "", e.message ?: e.toString())
        }
    }

    /**
     * If the installed APK is for the wrong kind of processor (e.g. the ARM APK on an x86_64 emulator),
     * a short explanation; null when it fits. The bundled Python/FFmpeg can't run in that case.
     * (A 32-bit ARM APK on a 64-bit ARM phone is fine, so only ARM vs x86 counts.)
     */
    fun wrongApk(context: Context): String? {
        val dir = context.applicationInfo.nativeLibraryDir ?: return null
        val appAbi = when {
            dir.endsWith("/arm64") -> "arm64-v8a"
            dir.endsWith("/arm") -> "armeabi-v7a"
            dir.endsWith("/x86_64") -> "x86_64"
            dir.endsWith("/x86") -> "x86"
            else -> return null
        }
        val device = android.os.Build.SUPPORTED_ABIS.firstOrNull() ?: return null
        if (appAbi.startsWith("x86") == device.startsWith("x86")) return null
        return "This is the $appAbi APK, but this device needs $device. Install SEVBY-…-$device.apk or the universal APK."
    }

    /** Kill a running yt-dlp started by [run]. */
    fun stop(id: String) {
        runCatching { YoutubeDL.getInstance().destroyProcessById(id) }
    }

    /** Download the newest stable yt-dlp. Returns a short message for the user. */
    suspend fun updateYtDlp(context: Context): String = withContext(Dispatchers.IO) {
        try {
            when (YoutubeDL.getInstance().updateYoutubeDL(context.applicationContext, YoutubeDL.UpdateChannel.STABLE)) {
                YoutubeDL.UpdateStatus.DONE -> "Updated to ${run("--version").out.trim()}"
                YoutubeDL.UpdateStatus.ALREADY_UP_TO_DATE -> "Already up to date"
                else -> "Update finished"
            }
        } catch (e: Exception) {
            "Update failed: ${e.message}"
        }
    }

    /** Newest stable yt-dlp version on GitHub (e.g. "2026.10.02"), or null if it can't be checked. */
    suspend fun latestYtDlp(): String? = withContext(Dispatchers.IO) {
        runCatching {
            val con = (java.net.URL("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest")
                .openConnection() as java.net.HttpURLConnection).apply {
                connectTimeout = 10_000
                readTimeout = 10_000
                setRequestProperty("Accept", "application/vnd.github+json")
                setRequestProperty("User-Agent", "SEVBY-Android")
            }
            try {
                if (con.responseCode != 200) null
                else org.json.JSONObject(con.inputStream.bufferedReader().readText()).optString("tag_name").ifEmpty { null }
            } finally {
                con.disconnect()
            }
        }.getOrNull()
    }

    /** True if version [a] is newer than [b] (versions look like 2026.08.19 or 2026.08.19.1). */
    fun isNewer(a: String, b: String): Boolean {
        val x = a.trim().split(".").map { it.toIntOrNull() ?: 0 }
        val y = b.trim().split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) {
            val p = x.getOrElse(i) { 0 }
            val q = y.getOrElse(i) { 0 }
            if (p != q) return p > q
        }
        return false
    }

    /** Days since this yt-dlp version was released (versions look like 2025.11.12), or null. */
    fun ageDays(version: String): Long? = runCatching {
        val p = version.trim().split(".")
        val cal = java.util.Calendar.getInstance()
        cal.clear()
        cal.set(p[0].toInt(), p[1].toInt() - 1, p[2].toInt())
        (System.currentTimeMillis() - cal.timeInMillis) / 86_400_000L
    }.getOrNull()
}
