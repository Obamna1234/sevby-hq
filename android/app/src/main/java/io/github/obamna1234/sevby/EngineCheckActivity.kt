package io.github.obamna1234.sevby

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.view.View
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import io.github.obamna1234.sevby.databinding.ActivityEngineCheckBinding
import kotlinx.coroutines.launch

/**
 * "About & updates": app and yt-dlp versions with a one-tap yt-dlp update, project info,
 * and (under Advanced) the engine report, a network test and the Bandcamp test switch.
 */
class EngineCheckActivity : AppCompatActivity() {

    private lateinit var b: ActivityEngineCheckBinding
    private val report = StringBuilder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityEngineCheckBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.testInput.setText(YT_EXAMPLE)
        b.presetYoutube.setOnClickListener { b.testInput.setText(YT_EXAMPLE) }
        b.presetBandcamp.setOnClickListener { b.testInput.setText(BC_EXAMPLE) }
        b.runTest.setOnClickListener { runTest() }
        b.copyReport.setOnClickListener { copyReport() }
        b.updateYtdlp.setOnClickListener { updateYtdlp() }
        b.back.setOnClickListener { finish() }
        b.projectPage.setOnClickListener {
            runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(PROJECT_URL))) }
        }
        b.advancedToggle.setOnClickListener {
            val open = b.advanced.visibility != View.VISIBLE
            b.advanced.visibility = if (open) View.VISIBLE else View.GONE
            b.advancedToggle.text = (if (open) "▾" else "▸") + "  Advanced (for testing and bug reports)"
        }
        val prefs = Prefs(this)
        b.pretendBlocked.isChecked = prefs.pretendBandcampBlocked
        b.pretendBlocked.setOnCheckedChangeListener { _, on -> prefs.pretendBandcampBlocked = on }

        b.appUpdateButton.setOnClickListener { appUpdateClicked() }
        showAppUpdate(AppUpdate.available(this, prefs), checked = false)
        lifecycleScope.launch { checkEngine() }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    // ── SEVBY app updates ───────────────────────────────────────────────

    private fun showAppUpdate(rel: AppRelease?, checked: Boolean) {
        if (rel != null) {
            b.appUpdateStatus.text = "Update available: ${rel.version}"
            b.appUpdateStatus.setTextColor(getColor(R.color.accent))
            b.appUpdateButton.text = "Download ${rel.version}"
        } else {
            b.appUpdateStatus.text = "Version ${AppUpdate.currentVersion(this)}" + if (checked) " · Up to date ✓" else ""
            b.appUpdateStatus.setTextColor(getColor(R.color.muted))
            b.appUpdateButton.text = "Check for app updates"
        }
    }

    private fun appUpdateClicked() {
        val prefs = Prefs(this)
        val rel = AppUpdate.available(this, prefs)
        if (rel != null) {
            if (AppUpdate.open(this, rel.apkUrl)) toast("Downloading SEVBY ${rel.version} – open it when it's finished and tap Update")
            else toast("No browser found to download the update")
            return
        }
        b.appUpdateButton.isEnabled = false
        b.appUpdateButton.text = "Checking…"
        lifecycleScope.launch {
            val before = prefs.lastAppCheck
            val found = AppUpdate.checkDaily(this@EngineCheckActivity, prefs, force = true)
            b.appUpdateButton.isEnabled = true
            if (found == null && prefs.lastAppCheck == before) {
                showAppUpdate(null, checked = false)
                toast("Couldn't check for updates – are you online?")
            } else {
                showAppUpdate(found, checked = true)
            }
        }
    }

    private fun line(s: String = "") {
        report.append(s).append('\n')
        b.report.text = report.toString()
    }

    /** Show the installed yt-dlp version and whether a newer one exists. */
    private suspend fun showYtDlp() {
        val v = Engine.run("--version")
        val installed = v.out.trim()
        if (!v.ok || installed.isEmpty()) {
            val wrong = Engine.wrongApk(this)
            b.ytdlpVersion.text = if (wrong != null) "Wrong APK for this device" else "Not working – see Advanced"
            b.ytdlpStatus.text = wrong ?: ""
            b.updateYtdlp.text = "Check for updates"
            return
        }
        b.ytdlpVersion.text = "Version $installed"
        val prefs = Prefs(this)
        val latest = Engine.latestYtDlp()?.also {
            prefs.latestYtDlp = it
            prefs.lastUpdateCheck = System.currentTimeMillis()
        } ?: prefs.latestYtDlp
        when {
            latest.isEmpty() -> {
                b.ytdlpStatus.text = "Couldn't check for updates (no connection?)"
                b.updateYtdlp.text = "Check for updates"
            }
            Engine.isNewer(latest, installed) -> {
                b.ytdlpStatus.text = "Update available: $latest"
                b.ytdlpStatus.setTextColor(getColor(R.color.accent))
                b.updateYtdlp.text = "Update to $latest"
            }
            else -> {
                b.ytdlpStatus.text = "Up to date ✓"
                b.ytdlpStatus.setTextColor(getColor(R.color.muted))
                b.updateYtdlp.text = "Check for updates"
            }
        }
    }

    private suspend fun checkEngine() {
        val pi = packageManager.getPackageInfo(packageName, 0)
        b.appVersion.text = "SEVBY ${pi.versionName}"
        line("SEVBY ${pi.versionName}")
        line("Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT}), ${Build.MANUFACTURER} ${Build.MODEL}")
        line("CPU: ${Build.SUPPORTED_ABIS.joinToString()}")
        Engine.wrongApk(this)?.let { line("WRONG APK: $it") }
        line()
        line("Starting engine (first launch unpacks files, can take ~10 s)...")

        val t0 = SystemClock.elapsedRealtime()
        try {
            Engine.init(this)
        } catch (e: Exception) {
            line("ENGINE FAILED TO START: ${e.message}")
            e.cause?.let { line("  cause: ${it.message}") }
            b.ytdlpVersion.text = "The engine didn't start – see Advanced"
            return
        }
        line("Engine started in ${(SystemClock.elapsedRealtime() - t0) / 1000.0} s")

        val ver = Engine.run("--version")
        line("yt-dlp: " + if (ver.ok) ver.out.trim() else "FAILED: ${lastLine(ver.err)}")
        b.updateYtdlp.isEnabled = true
        showYtDlp()

        // '-v' with no link prints yt-dlp's debug header (Python, FFmpeg, optional libraries)
        // and then complains about the missing link, which is expected.
        val dbg = Engine.run("-v")
        val keys = listOf("Python", "exe versions", "Optional libraries", "JS runtimes")
        (dbg.err + "\n" + dbg.out).lines()
            .filter { l -> l.startsWith("[debug]") && keys.any { l.contains(it) } }
            .forEach { line(it.removePrefix("[debug] ")) }

        val imp = Engine.run("--list-impersonate-targets")
        val rows = (imp.out + "\n" + imp.err).lines().filter { it.contains("curl_cffi") }
        val usable = rows.count { !it.contains("unavailable", ignoreCase = true) }
        line()
        line(
            if (usable > 0) "Browser impersonation: YES ($usable targets) - Bandcamp should work"
            else "Browser impersonation: NO (curl_cffi not in this engine) - " +
                "SEVBY reads Bandcamp through Android's browser engine when needed"
        )
        line()
        line(if (ver.ok) "Engine OK." else "Engine NOT working – yt-dlp didn't start (see above).")
        b.runTest.isEnabled = true
    }

    private fun updateYtdlp() {
        if (Runner.state.value.running) {
            Toast.makeText(this, "Wait until the downloads finish, then update.", Toast.LENGTH_LONG).show()
            return
        }
        b.updateYtdlp.isEnabled = false
        lifecycleScope.launch {
            b.updateYtdlp.text = "Updating…"
            b.ytdlpStatus.text = "Downloading the newest yt-dlp…"
            val msg = Engine.updateYtDlp(this@EngineCheckActivity)
            line()
            line("yt-dlp update: $msg")
            showYtDlp()
            if (msg.startsWith("Update failed")) b.ytdlpStatus.text = msg
            Toast.makeText(this@EngineCheckActivity, "yt-dlp: $msg", Toast.LENGTH_SHORT).show()
            b.updateYtdlp.isEnabled = true
        }
    }

    private fun runTest() {
        val input = b.testInput.text?.toString()?.trim().orEmpty()
        if (input.isEmpty()) return
        b.runTest.isEnabled = false
        lifecycleScope.launch {
            line()
            line("TEST: $input")
            val t0 = SystemClock.elapsedRealtime()
            val r = if (input.startsWith("http")) {
                Engine.run(
                    "--simulate", "--no-playlist", "--no-warnings",
                    "--print", "%(title)s | %(artist,uploader)s | %(album|-)s | %(duration>%M:%S|?)s",
                    input,
                )
            } else {
                Engine.run(
                    "--flat-playlist", "--no-warnings",
                    "--print", "%(title)s [%(duration>%M:%S|?)s] - %(channel,uploader)s",
                    "ytsearch3:$input",
                )
            }
            val secs = (SystemClock.elapsedRealtime() - t0) / 1000.0
            if (r.ok && r.out.isNotBlank()) {
                r.out.trim().lines().forEach { line("  $it") }
                line("  OK in $secs s")
            } else {
                val err = lastLine(r.err)
                line("  FAILED in $secs s: $err")
                if (err.contains("tralbum", ignoreCase = true)) {
                    line("  -> Bandcamp blocked the page (bot check). This connection blocks yt-dlp on Bandcamp.")
                    line("     SEVBY's own downloads use Android's browser engine for Bandcamp instead.")
                }
            }
            b.runTest.isEnabled = true
        }
    }

    private fun copyReport() {
        val cm = getSystemService(ClipboardManager::class.java)
        cm.setPrimaryClip(ClipData.newPlainText("SEVBY diagnostics", report.toString()))
        Toast.makeText(this, "Diagnostics copied – paste them into a bug report", Toast.LENGTH_SHORT).show()
    }

    private fun lastLine(s: String) =
        s.lines().map { it.trim() }.lastOrNull { it.isNotEmpty() } ?: "unknown error"

    companion object {
        const val YT_EXAMPLE = "Perturbator - Future Club"
        const val BC_EXAMPLE = "https://noisia.bandcamp.com/track/found"
        const val PROJECT_URL = "https://github.com/Obamna1234/sevby-hq"
    }
}
