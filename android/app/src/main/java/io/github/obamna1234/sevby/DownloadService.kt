package io.github.obamna1234.sevby

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Runs the downloads in the background with a notification (progress bar + Stop),
 * so they keep going when the app is minimised or the screen is locked.
 */
class DownloadService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastNotify = 0L
    private var lastIndex = -1
    @Volatile private var done = false
    private var web: BandcampWeb? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { Runner.stop(); return START_NOT_STICKY }
            ACTION_PAUSE -> { Runner.pause(); return START_NOT_STICKY }
            ACTION_RESUME -> { Runner.resume(); return START_NOT_STICKY }
        }
        val job = Runner.takePending()
        createChannels()
        goForeground(progressNotification(0, job?.items?.firstOrNull()?.songs?.size ?: 0, "Starting…", null))
        if (job == null) {
            Runner.finished("")
            stopSelfCleanly()
            return START_NOT_STICKY
        }
        acquireWakeLock()
        scope.launch { run(job) }
        return START_NOT_STICKY
    }

    /**
     * YouTube stops working with old yt-dlp versions, and a fresh install brings the one built into the app.
     * So before a run that may use YouTube, update yt-dlp if a newer one is out (checked at most once a day).
     */
    private suspend fun freshenYtDlp(prefs: Prefs) {
        val v = Engine.run("--version")
        if (!v.ok) return
        val installed = v.out.trim()
        val now = System.currentTimeMillis()
        if (prefs.latestYtDlp.isEmpty() || now - prefs.lastUpdateCheck > 24 * 60 * 60 * 1000L) {
            Engine.latestYtDlp()?.let { prefs.latestYtDlp = it; prefs.lastUpdateCheck = now }
        }
        val latest = prefs.latestYtDlp
        if (latest.isEmpty() || !Engine.isNewer(latest, installed)) return
        Runner.step("Updating yt-dlp…")
        Runner.log("yt-dlp $installed is out of date – updating to $latest first (YouTube needs a recent version)…")
        val msg = Engine.updateYtDlp(this)
        Runner.log("yt-dlp: $msg")
        Runner.step("")
    }

    private var stoppedAt = 0

    /** The lists from the one that was interrupted onwards (queued ones only if still in the queue). */
    private fun remaining(job: Job, prefs: Prefs): List<QueueItem> =
        job.items.drop(stoppedAt).filter { item ->
            !job.fromQueue || prefs.queue.any { it.name == item.name && it.songs == item.songs }
        }

    private suspend fun run(job: Job) {
        var summary = "Stopped"
        var wasStopped = true
        val prefs = Prefs(this)
        try {
            Engine.init(this)
            val root = DocumentFile.fromTreeUri(this, Uri.parse(job.folderUri))
            if (root == null || !root.canWrite()) {
                Runner.log("SEVBY can no longer write to the save folder. Choose it again and press Start.")
                Runner.song(SongLine("Can't write to the save folder – choose it again", Mark.FAILED))
                summary = "Couldn't write to the save folder"
                return
            }
            if (job.source != Source.BANDCAMP) freshenYtDlp(prefs)
            web = BandcampWeb(this)
            val bandcamp = Bandcamp(web!!)
            Runner.onChange = { refreshNotification() }

            var ok = 0
            var skipped = 0
            val failed = mutableListOf<QueueItem>()
            var previous: Downloader? = null
            var stopped = false
            stoppedAt = 0

            for ((k, item) in job.items.withIndex()) {
                stoppedAt = k
                if (Runner.stopRequested) { stopped = true; break }
                // A queued list removed while an earlier one was downloading: skip it.
                if (job.fromQueue && k > 0 && prefs.queue.none { it.name == item.name && it.songs == item.songs }) {
                    Runner.log("Skipped \"${item.name}\" (removed from the queue)")
                    continue
                }
                Runner.item(k + 1, job.items.size, item.name, item.songs.size)
                val folder = if (item.subfolder == null) root else subfolder(root, item.subfolder)
                if (folder == null) {
                    Runner.log("Couldn't create the folder \"${item.subfolder}\" – skipping this list")
                    Runner.song(SongLine("${item.name}: couldn't create its folder", Mark.FAILED))
                    failed += item
                    continue
                }
                if (job.items.size > 1 || item.subfolder != null) {
                    Runner.log("════ List ${k + 1}/${job.items.size}: ${item.name} (${item.songs.size} songs) → ${folder.name}")
                    Runner.song(SongLine("${item.name} · ${item.songs.size} songs → ${folder.name}", Mark.HEADER))
                }
                val d = Downloader(
                    this, folder,
                    log = { Runner.log(it) },
                    status = { i, n, song, pct ->
                        Runner.progress(i, n, song, pct)
                        updateProgress(i, n, song, pct)
                    },
                    bandcamp = bandcamp,
                    pretendBlocked = prefs.pretendBandcampBlocked,
                    step = { Runner.step(it) },
                    done = { Runner.song(it) },
                )
                d.inheritBandcampState(previous)
                previous = d
                Runner.downloader = d
                val r = d.run(item.songs, job.source)
                ok += r.ok
                skipped += r.skipped
                if (r.failed.isNotEmpty()) failed += QueueItem(item.name, r.failed, item.subfolder)
                if (r.stopped) {
                    stopped = true
                    break
                }
                // This list is finished: take it off the saved queue.
                prefs.queue = prefs.queue.let { q ->
                    val at = q.indexOfFirst { it.name == item.name && it.songs == item.songs }
                    if (at >= 0) q.toMutableList().apply { removeAt(at) } else q
                }
            }

            // Remember what failed, for "Retry failed" (a stopped run keeps the old list).
            if (!stopped) prefs.failed = failed
            // A stopped run can be resumed: the unfinished lists run again and finished songs are skipped.
            prefs.resume = if (stopped) remaining(job, prefs) else emptyList()
            val nFailed = failed.sumOf { it.songs.size }
            wasStopped = stopped
            summary = (if (stopped) "Stopped · " else "Done · ") +
                "$ok downloaded" +
                (if (skipped > 0) " · $skipped already there" else "") +
                (if (nFailed > 0) " · $nFailed failed" else "")
            Runner.log("────────────────────────")
            Runner.log(summary)
            failed.forEach { f -> f.songs.forEach { Runner.log("  ✗ $it" + (f.subfolder?.let { s -> "  (in $s)" } ?: "")) } }
        } catch (e: Exception) {
            Runner.log("Something went wrong: ${e.message}")
            summary = "Stopped after an error"
            prefs.resume = remaining(job, prefs)
        } finally {
            done = true
            web?.close()
            web = null
            Runner.onChange = null
            Runner.downloader = null
            Runner.finished(summary)
            showDone(summary, silent = wasStopped)
            stopSelfCleanly()
        }
    }

    /** The sub-folder [name] inside [root], created if needed. */
    private fun subfolder(root: DocumentFile, name: String): DocumentFile? {
        val safe = SongList.safeFileName(name).trim('.', ' ').ifEmpty { "Song list" }
        root.findFile(safe)?.let { if (it.isDirectory()) return it }
        return root.createDirectory(safe)
    }

    // ── Notifications ───────────────────────────────────────────────────

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_PROGRESS, "Download progress", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shows the song being downloaded"
                setShowBadge(false)
            }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_DONE, "Finished downloads", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Tells you when a song list has finished"
            }
        )
    }

    private fun builder(channel: String): Notification.Builder =
        if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, channel)
        else @Suppress("DEPRECATION") Notification.Builder(this)

    private fun openAppIntent(): PendingIntent = PendingIntent.getActivity(
        this, 0,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun serviceIntent(action: String, code: Int): PendingIntent = PendingIntent.getService(
        this, code,
        Intent(this, DownloadService::class.java).setAction(action),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun progressNotification(index: Int, total: Int, song: String, pct: Float?): Notification {
        val paused = Runner.state.value.paused
        val st = Runner.state.value
        val list = if (st.itemCount > 1) "List ${st.itemIndex}/${st.itemCount} · " else ""
        val count = if (total > 0 && index > 0) "$list$index / $total" else ""
        val b = builder(CH_PROGRESS)
            .setSmallIcon(if (paused) android.R.drawable.ic_media_pause else android.R.drawable.stat_sys_download)
            .setContentTitle(if (paused) "Paused $count".trim() else if (count.isNotEmpty()) "Downloading $count" else "SEVBY")
            .setContentText(song)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(openAppIntent())
            .addAction(
                if (paused) Notification.Action.Builder(null, "Resume", serviceIntent(ACTION_RESUME, 3)).build()
                else Notification.Action.Builder(null, "Pause", serviceIntent(ACTION_PAUSE, 2)).build()
            )
            .addAction(Notification.Action.Builder(null, "Stop", serviceIntent(ACTION_STOP, 1)).build())
        // One bar for the whole list (song 12 of 94 ≈ 12%); the current song's % goes beside it.
        if (total > 0) {
            val within = if (paused) 0f else ((pct ?: 0f).coerceIn(0f, 100f) / 100f)
            val overall = (((index - 1).coerceAtLeast(0) + within) / total * 1000).toInt().coerceIn(0, 1000)
            b.setProgress(1000, overall, false)
            if (!paused && pct != null && pct > 0f) b.setSubText("${pct.toInt()}%")
        } else {
            b.setProgress(0, 0, true)
        }
        return b.build()
    }

    /** Redraw the notification now (after Pause / Resume). */
    private fun refreshNotification() {
        if (done) return
        val st = Runner.state.value
        lastNotify = SystemClock.elapsedRealtime()
        getSystemService(NotificationManager::class.java)
            .notify(ID_PROGRESS, progressNotification(st.index, st.total, st.song, st.percent))
    }

    private fun updateProgress(index: Int, total: Int, song: String, pct: Float?) {
        // Android drops updates that come too fast, so: at most ~1 per second, but always on a new song.
        if (done) return   // a late progress update must not bring the notification back
        val now = SystemClock.elapsedRealtime()
        if (index == lastIndex && now - lastNotify < 1000) return
        lastIndex = index
        lastNotify = now
        getSystemService(NotificationManager::class.java)
            .notify(ID_PROGRESS, progressNotification(index, total, song, pct))
    }

    /** Finished runs make the notification sound; stopped ones (or errors) appear silently. */
    private fun showDone(summary: String, silent: Boolean) {
        val n = builder(if (silent) CH_PROGRESS else CH_DONE)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(if (silent) "SEVBY stopped" else "SEVBY finished")
            .setContentText(summary)
            .setAutoCancel(true)
            .setContentIntent(openAppIntent())
            .build()
        getSystemService(NotificationManager::class.java).notify(ID_DONE, n)
    }

    private fun goForeground(n: Notification) {
        if (Build.VERSION.SDK_INT >= 29) startForeground(ID_PROGRESS, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else startForeground(ID_PROGRESS, n)
    }

    // ── Keeping the phone awake during a run ────────────────────────────

    private fun acquireWakeLock() {
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sevby:download").apply {
            setReferenceCounted(false)
            acquire(6 * 60 * 60 * 1000L)   // safety limit: 6 hours
        }
    }

    private fun stopSelfCleanly() {
        runCatching { wakeLock?.release() }
        wakeLock = null
        if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        Runner.stop()
        scope.cancel()
        runCatching { wakeLock?.release() }
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "io.github.obamna1234.sevby.STOP"
        const val ACTION_PAUSE = "io.github.obamna1234.sevby.PAUSE"
        const val ACTION_RESUME = "io.github.obamna1234.sevby.RESUME"
        private const val CH_PROGRESS = "progress"
        private const val CH_DONE = "done"
        private const val ID_PROGRESS = 1
        private const val ID_DONE = 2
    }
}
