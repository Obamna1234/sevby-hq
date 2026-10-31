package io.github.obamna1234.sevby

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.res.ColorStateList
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.widget.doAfterTextChanged
import androidx.documentfile.provider.DocumentFile
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import androidx.lifecycle.repeatOnLifecycle
import io.github.obamna1234.sevby.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Main screen. Paste or load a song list (or queue several), choose a folder and a source,
 * press Start. The downloads run in [DownloadService]; this screen only shows [Runner.state],
 * so it can be closed and reopened while a run continues in the background.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding
    private lateinit var prefs: Prefs
    private var installedVersion = ""

    // What the log already shows (so only new lines are added).
    private var shownRun = -1
    private var shownSongs = 0L
    private var shownDetails = 0L
    private var detailsOpen = false
    private var wasRunning = false
    private var lastItemIndex = -1
    private val songText = SpannableStringBuilder()

    private val pickTxt = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) loadTxt(uri)
    }

    private val pickFolder = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) setFolder(uri)
    }

    /** Asked once, on the first Start: needed to show the progress notification (Android 13+). */
    private var afterPermission: (() -> Unit)? = null
    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) toast("Notifications are off, so there's no progress in the notification shade. Downloads still work.")
        afterPermission?.invoke()
        afterPermission = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)
        prefs = Prefs(this)

        // Song list (remembered between launches)
        b.songs.setText(prefs.draft)
        updateCount()
        b.songs.doAfterTextChanged {
            prefs.draft = it?.toString().orEmpty()
            updateCount()
            // A different list can't be "resumed": the button goes back to Start.
            if (!Runner.state.value.running && prefs.resume.isNotEmpty()) { prefs.resume = emptyList(); refreshQueue() }
        }
        // The song box scrolls on its own (like the log box) instead of moving the whole page.
        b.songs.setOnTouchListener { v, e ->
            if (v.canScrollVertically(1) || v.canScrollVertically(-1)) {
                v.parent.requestDisallowInterceptTouchEvent(
                    e.actionMasked != MotionEvent.ACTION_UP && e.actionMasked != MotionEvent.ACTION_CANCEL
                )
            }
            false
        }
        setLabel(b.loadTxt, "Import", ".txt or .csv", R.drawable.ic_import_file)
        setLabel(b.chosic, "Chosic", "Spotify playlists", R.drawable.ic_website)
        setLabel(b.appleMusic, "Apple Music", "paste a link", R.drawable.ic_link)
        b.loadTxt.setOnClickListener {
            pickTxt.launch(arrayOf("text/*", "application/csv", "application/vnd.ms-excel", "audio/x-mpegurl", "audio/mpegurl", "application/vnd.apple.mpegurl"))
        }
        b.chosic.setOnClickListener { openChosic() }
        b.appleMusic.setOnClickListener { askAppleMusicLink() }
        b.clearSongs.setOnClickListener {
            b.songs.setText("")
            prefs.listName = ""
        }
        b.addToQueue.setOnClickListener { addToQueue() }
        b.clearQueue.setOnClickListener {
            prefs.queue = emptyList()
            refreshQueue()
        }

        // Folder
        showFolder()
        b.chooseFolder.setOnClickListener { pickFolder.launch(null) }

        // Source
        b.sourceGroup.check(
            when (prefs.source) {
                Source.BOTH -> R.id.srcBoth
                Source.BANDCAMP -> R.id.srcBandcamp
                Source.YOUTUBE -> R.id.srcYoutube
            }
        )
        b.sourceGroup.setOnCheckedChangeListener { _, id ->
            prefs.source = when (id) {
                R.id.srcBandcamp -> Source.BANDCAMP
                R.id.srcYoutube -> Source.YOUTUBE
                else -> Source.BOTH
            }
        }

        b.start.setOnClickListener { onStartPressed() }
        b.pause.setOnClickListener { if (Runner.state.value.paused) Runner.resume() else Runner.pause() }
        b.retryFailed.setOnClickListener { retryFailed() }
        b.about.setOnClickListener { startActivity(Intent(this, EngineCheckActivity::class.java)) }
        b.updateNow.setOnClickListener { updateYtDlp() }
        b.appUpdateNow.setOnClickListener { AppUpdate.available(this, prefs)?.let { offerAppUpdate(it) } }
        showAppUpdateRow()
        lifecycleScope.launch {
            AppUpdate.checkDaily(this@MainActivity, prefs)
            showAppUpdateRow()
        }

        // Log box: fold away / details / copy
        setLogCollapsed(prefs.logCollapsed)
        b.logHeader.setOnClickListener { setLogCollapsed(!prefs.logCollapsed) }
        b.toggleDetails.setOnClickListener { setDetailsOpen(!detailsOpen) }
        b.copyLog.setOnClickListener { copyLog() }

        refreshQueue()
        refreshRetry()

        // Show the background run (progress + log), live.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                Runner.state.collect { render(it) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (!Runner.state.value.running) refreshEngineStatus()   // also picks up an update made on the About screen
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    // ── Engine version + update check ───────────────────────────────────

    /** Warm up the engine, show its version, and (once a day) check whether a newer yt-dlp exists. */
    private fun refreshEngineStatus() {
        lifecycleScope.launch {
            try {
                Engine.init(this@MainActivity)
                val v = Engine.run("--version")
                if (!v.ok) {
                    b.engineStatus.text = if (Engine.wrongApk(this@MainActivity) != null)
                        "Wrong APK for this device – tap About & updates"
                    else "Engine problem – tap About & updates for details"
                    return@launch
                }
                installedVersion = v.out.trim()
                b.engineStatus.text = "Ready · yt-dlp $installedVersion"
                val now = System.currentTimeMillis()
                if (now - prefs.lastUpdateCheck > DAY_MS) {
                    Engine.latestYtDlp()?.let {
                        prefs.latestYtDlp = it
                        prefs.lastUpdateCheck = now
                    }
                }
                showUpdateRow()
            } catch (e: Exception) {
                b.engineStatus.text = "Engine failed to start – tap About & updates for details"
            }
        }
    }

    private fun showUpdateRow() {
        val latest = prefs.latestYtDlp
        val available = latest.isNotEmpty() && installedVersion.isNotEmpty() && Engine.isNewer(latest, installedVersion)
        b.updateRow.visibility = if (available) View.VISIBLE else View.GONE
        if (available) b.updateText.text = "A newer yt-dlp ($latest) is available"
    }

    // ── New SEVBY version ───────────────────────────────────────────────

    private fun showAppUpdateRow() {
        val rel = AppUpdate.available(this, prefs)
        b.appUpdateRow.visibility = if (rel != null) View.VISIBLE else View.GONE
        if (rel != null) b.appUpdateText.text = "SEVBY ${rel.version} is available"
    }

    private fun offerAppUpdate(rel: AppRelease) {
        MaterialAlertDialogBuilder(this)
            .setTitle("Update SEVBY to ${rel.version}?")
            .setMessage("Your browser downloads the new version. When it's done, open it and tap Update. " +
                "Your folder, settings and queue are kept." +
                if (Runner.state.value.running) "\n\nDownloads are running – updating stops them. You can press Resume afterwards." else "")
            .setPositiveButton("Download") { _, _ ->
                if (!AppUpdate.open(this, rel.apkUrl)) toast("No browser found to download the update")
                else toast("Downloading SEVBY ${rel.version} – open it when it's finished")
            }
            .setNeutralButton("What's new") { _, _ -> AppUpdate.open(this, rel.pageUrl) }
            .setNegativeButton("Later", null)
            .show()
    }

    private fun updateYtDlp() {
        if (Runner.state.value.running) {
            toast("Wait until the downloads finish, then update.")
            return
        }
        b.updateNow.isEnabled = false
        b.updateText.text = "Updating…"
        lifecycleScope.launch {
            val msg = Engine.updateYtDlp(this@MainActivity)
            b.updateNow.isEnabled = true
            toast("yt-dlp: $msg")
            refreshEngineStatus()
        }
    }

    // ── Song list ───────────────────────────────────────────────────────

    private fun songs() = SongList.parse(b.songs.text?.toString().orEmpty())

    /** Button text with an optional smaller second line. */
    /** Two-line button label: [main] (with an optional small [icon] in front) over a smaller, softer [sub]. */
    private fun setLabel(button: android.widget.Button, main: String, sub: String?, icon: Int? = null) {
        val t = SpannableStringBuilder()
        if (icon != null) {
            ContextCompat.getDrawable(this, icon)?.mutate()?.let { d ->
                val px = (button.textSize * 0.95f).toInt()
                d.setBounds(0, 0, px, px)
                d.setTint(color(R.color.accent))
                t.append("\u00A0").setSpan(CenteredIconSpan(d), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                t.append("\u2009")
            }
        }
        t.append(main)
        if (sub != null) {
            t.append('\n')
            val at = t.length
            t.append(sub)
            t.setSpan(RelativeSizeSpan(0.78f), at, t.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            t.setSpan(ForegroundColorSpan(color(R.color.tonal_sub)), at, t.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        button.isAllCaps = false
        button.text = t
        // On a narrow phone, shrink the text a little until the name fits on one line (sub line below it).
        val maxLines = if (sub != null) 2 else 1
        button.post {
            val lines = button.layout?.lineCount ?: return@post
            val sp = button.textSize / resources.displayMetrics.scaledDensity
            if (lines > maxLines && sp > 10.5f) {
                button.textSize = sp - 0.5f
                setLabel(button, main, sub, icon)
            }
        }
    }

    /** An icon drawn in the middle of the text line (ImageSpan's own centring needs Android 10). */
    private class CenteredIconSpan(d: android.graphics.drawable.Drawable) : android.text.style.ImageSpan(d) {
        override fun draw(canvas: android.graphics.Canvas, text: CharSequence?, start: Int, end: Int, x: Float,
                          top: Int, y: Int, bottom: Int, paint: android.graphics.Paint) {
            val fm = paint.fontMetricsInt
            val d = drawable
            val shift = y + (fm.ascent + fm.descent) / 2 - d.bounds.height() / 2
            canvas.save()
            canvas.translate(x, shift.toFloat())
            d.draw(canvas)
            canvas.restore()
        }
    }

    private fun updateCount() {
        val n = songs().size
        b.songCount.text = when (n) {
            1 -> "1 song"
            else -> "$n songs"
        }
    }

    private fun loadTxt(uri: Uri) {
        lifecycleScope.launch {
            val name = displayName(uri) ?: "Song list.txt"
            val text = try {
                withContext(Dispatchers.IO) {
                    contentResolver.openInputStream(uri)!!.use { SongList.decode(it.readBytes()) }
                }
            } catch (e: Exception) {
                toast("Could not read $name: ${e.message}")
                return@launch
            }
            // A CSV / M3U export becomes "Artist - Title" lines; anything else goes in as it is.
            b.songs.setText(SongList.fromExport(text)?.joinToString("\n") ?: text)
            prefs.listName = SongList.safeFileName(name.substringBeforeLast('.'))
            toast("Loaded $name: ${songs().size} songs")
        }
    }

    private fun displayName(uri: Uri): String? =
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }

    // ── Queue ───────────────────────────────────────────────────────────

    /** The current song box as a queue item (named after the loaded .txt, else "Song list N"). */
    private fun boxAsItem(queue: List<QueueItem>): QueueItem? {
        val list = songs()
        if (list.isEmpty()) return null
        var name = prefs.listName.ifEmpty { "Song list ${queue.size + 1}" }
        val base = name
        var n = 2
        while (queue.any { it.name.equals(name, ignoreCase = true) }) name = "$base ($n)".also { n++ }
        return QueueItem(name, list, subfolder = name)
    }

    private fun addToQueue() {
        if (Runner.state.value.running) return
        val q = prefs.queue
        val item = boxAsItem(q) ?: run { toast("Add some songs first."); return }
        prefs.queue = q + item
        b.songs.setText("")
        prefs.listName = ""
        refreshQueue()
        toast("Added \"${item.name}\" (${item.songs.size} songs) to the queue")
    }

    private fun refreshQueue() {
        val q = prefs.queue
        val running = Runner.state.value.running
        b.queueBox.visibility = if (q.isEmpty()) View.GONE else View.VISIBLE
        b.queueTitle.text = "Queue (${q.size})"
        b.clearQueue.isEnabled = !running
        b.queueList.removeAllViews()
        q.forEachIndexed { i, item ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val left = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
            left.addView(TextView(this).apply {
                text = "${i + 1}. ${item.name} · ${item.songs.size} songs"
                setTextColor(ContextCompat.getColor(this@MainActivity, R.color.text))
            })
            left.addView(TextView(this).apply {
                val base = folder()?.name ?: "(save folder)"
                text = "Saves to: " + (item.subfolder?.let { "$base/$it" } ?: base)
                textSize = 12f
                setTextColor(ContextCompat.getColor(this@MainActivity, R.color.muted))
            })
            row.addView(left)
            row.addView(TextView(this).apply {
                text = "✕"
                textSize = 18f
                val removable = !running || i > 0      // the list that is downloading stays
                setTextColor(ContextCompat.getColor(this@MainActivity, if (removable) R.color.accent else R.color.muted))
                val pad = (12 * resources.displayMetrics.density).toInt()
                setPadding(pad, pad / 2, pad / 2, pad / 2)
                isEnabled = removable
                contentDescription = "Remove ${item.name}"
                setOnClickListener {
                    prefs.queue = prefs.queue.filterIndexed { k, _ -> k != i }
                    refreshQueue()
                }
            })
            b.queueList.addView(row)
        }
        b.addToQueue.isEnabled = !running
        if (!running) {
            val canResume = prefs.resume.isNotEmpty()
            b.start.text = when {
                q.isNotEmpty() && canResume -> "Resume (${q.size} list" + (if (q.size == 1) ")" else "s)")
                q.isNotEmpty() -> "Start Download (${q.size} list" + (if (q.size == 1) ")" else "s)")
                canResume -> "Resume"
                else -> "Start Download"
            }
        }
    }

    private fun refreshRetry() {
        val n = prefs.failed.sumOf { it.songs.size }
        b.retryFailed.visibility = if (n > 0 && !Runner.state.value.running) View.VISIBLE else View.GONE
        b.retryFailed.text = "Retry failed ($n)"
    }

    // ── Save folder ─────────────────────────────────────────────────────

    private fun setFolder(uri: Uri) {
        val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        try {
            contentResolver.takePersistableUriPermission(uri, flags)
        } catch (e: SecurityException) {
            toast("Android didn't allow SEVBY to keep access to that folder. Try another one.")
            return
        }
        prefs.folderUri?.let { old ->
            if (old != uri.toString()) runCatching {
                contentResolver.releasePersistableUriPermission(Uri.parse(old), flags)
            }
        }
        prefs.folderUri = uri.toString()
        showFolder()
    }

    /** The chosen folder, if SEVBY still has permission to write there. */
    private fun folder(): DocumentFile? {
        val s = prefs.folderUri ?: return null
        val uri = Uri.parse(s)
        val held = contentResolver.persistedUriPermissions.any { it.uri == uri && it.isWritePermission }
        if (!held) return null
        return DocumentFile.fromTreeUri(this, uri)?.takeIf { it.canWrite() }
    }

    private fun showFolder() {
        b.folderName.text = folder()?.name ?: "Not chosen – tap Choose"
    }

    // ── Start / Stop / Retry ────────────────────────────────────────────

    private fun onStartPressed() {
        if (Runner.state.value.running) {
            Runner.stop()
            toast("Stopping…")
            return
        }
        var queue = prefs.queue
        val items: List<QueueItem>
        if (queue.isNotEmpty()) {
            // Whatever is still in the song box joins the end of the queue.
            boxAsItem(queue)?.let {
                queue = queue + it
                prefs.queue = queue
                b.songs.setText("")
                prefs.listName = ""
                refreshQueue()
            }
            items = queue
            launchRun(items, fromQueue = true)
            return
        } else if (prefs.resume.isNotEmpty()) {
            // Run the stopped list again; songs that are already saved are skipped.
            items = prefs.resume
        } else {
            val list = songs()
            if (list.isEmpty()) { toast("Add some songs first (one \"Artist - Title\" per line)."); return }
            items = listOf(QueueItem(prefs.listName.ifEmpty { "Song list" }, list, subfolder = null))
        }
        launchRun(items)
    }

    private fun retryFailed() {
        val items = prefs.failed
        if (items.isEmpty() || Runner.state.value.running) return
        launchRun(items)
    }

    private fun launchRun(items: List<QueueItem>, fromQueue: Boolean = false) {
        if (folder() == null) { toast("Choose a save folder first."); return }
        if (!Engine.ready) { toast("The engine is still starting – try again in a few seconds."); return }
        val go = {
            val src = when (prefs.source) {
                Source.BOTH -> "Bandcamp + YouTube"
                Source.BANDCAMP -> "Bandcamp only"
                Source.YOUTUBE -> "YouTube only"
            }
            if (Runner.start(this, Job(items, prefs.folderUri!!, prefs.source, fromQueue))) {
                prefs.resume = emptyList()
                val songsTotal = items.sumOf { it.songs.size }
                Runner.log("$songsTotal songs in ${items.size} list(s) → ${folder()?.name}  ·  $src" +
                    if (prefs.source != Source.YOUTUBE && prefs.pretendBandcampBlocked) "  (test: pretending Bandcamp is blocked)" else "")
                Runner.log("────────────────────────")
                if (prefs.logCollapsed) setLogCollapsed(false)
            }
        }
        val needAsk = Build.VERSION.SDK_INT >= 33 && !prefs.askedNotifications &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        if (needAsk) {
            prefs.askedNotifications = true
            afterPermission = go
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            go()
        }
    }

    // ── Log box ─────────────────────────────────────────────────────────

    private fun setLogCollapsed(collapsed: Boolean) {
        prefs.logCollapsed = collapsed
        b.logBody.visibility = if (collapsed) View.GONE else View.VISIBLE
        b.logArrow.text = if (collapsed) "▸" else "▾"
    }

    private fun setDetailsOpen(open: Boolean) {
        detailsOpen = open
        b.details.visibility = if (open) View.VISIBLE else View.GONE
        b.songLog.visibility = if (open) View.GONE else View.VISIBLE
        b.toggleDetails.text = if (open) "Song list" else "Details"
        if (open) {
            val s = Runner.state.value
            b.details.text = s.lines.joinToString("\n")
            shownDetails = s.added
        }
        scrollLogToEnd(force = true)
    }

    private fun copyLog() {
        val s = Runner.state.value
        val text = buildString {
            s.songs.forEach { l ->
                val mark = when (l.mark) { Mark.OK -> "✓"; Mark.SKIPPED -> "✓"; Mark.FAILED -> "✗"; Mark.HEADER -> "▸" }
                append("$mark ${l.text}").append(if (l.note.isNotEmpty()) "  ·  ${l.note}" else "").append('\n')
            }
            if (s.summary.isNotEmpty()) append(s.summary).append('\n')
            append("\n── Details ──\n")
            s.lines.forEach { append(it).append('\n') }
        }
        getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("SEVBY log", text))
        toast("Log copied")
    }

    /**
     * Keep the newest line in view, scrolling only the log box (never the whole page), and only
     * if the reader was already at the bottom – scrolling up to read stays put.
     */
    private fun scrollLogToEnd(force: Boolean = false) {
        val sv = b.logScroll
        val child = sv.getChildAt(0) ?: return
        val wasAtBottom = sv.scrollY + sv.height >= child.height - BOTTOM_SLACK_PX
        if (force || wasAtBottom) sv.post { sv.scrollTo(0, child.height) }
    }

    private fun color(id: Int) = ContextCompat.getColor(this, id)

    /** One tidy line per song: mark, name, and a short note underneath. */
    private fun appendSong(l: SongLine) {
        val start = songText.length
        when (l.mark) {
            Mark.HEADER -> {
                if (songText.isNotEmpty()) songText.append('\n')
                songText.append("▸ ${l.text}\n")
                songText.setSpan(ForegroundColorSpan(color(R.color.accent)), start, songText.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                songText.setSpan(StyleSpan(Typeface.BOLD), start, songText.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                return
            }
            Mark.OK -> songText.append("✓ ")
            Mark.SKIPPED -> songText.append("✓ ")     // already downloaded: done, shown muted
            Mark.FAILED -> songText.append("✗ ")
        }
        val markColor = when (l.mark) {
            Mark.OK -> color(R.color.accent)
            Mark.FAILED -> ERROR_RED
            else -> color(R.color.muted)
        }
        songText.setSpan(ForegroundColorSpan(markColor), start, start + 2, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        val nameStart = songText.length
        songText.append(l.text)
        songText.setSpan(
            ForegroundColorSpan(if (l.mark == Mark.SKIPPED) color(R.color.muted) else color(R.color.text)),
            nameStart, songText.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
        )
        if (l.note.isNotEmpty()) {
            val noteStart = songText.length
            songText.append("\n    ${l.note}")
            songText.setSpan(ForegroundColorSpan(if (l.mark == Mark.FAILED) ERROR_RED else color(R.color.muted)),
                noteStart, songText.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            songText.setSpan(RelativeSizeSpan(0.85f), noteStart, songText.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        songText.append('\n')
    }

    // ── Showing the run ─────────────────────────────────────────────────

    // ── Apple Music link ────────────────────────────────────────────────

    /** The open Apple Music dialog (only one at a time). */
    private var appleDialog: androidx.appcompat.app.AlertDialog? = null

    /**
     * Ask for a public Apple Music playlist/album share link (pre-filled from the clipboard), read it,
     * and put the songs in the song box. Errors are shown in the dialog, which stays open to try again.
     */
    private fun askAppleMusicLink() {
        appleDialog?.let { if (it.isShowing) return }
        val clip = (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager).primaryClip
            ?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
        val pad = (20 * resources.displayMetrics.density).toInt()
        val input = EditText(this).apply {
            hint = "https://music.apple.com/…/playlist/…"
            setSingleLine()
            setText(AppleMusicData.findLink(clip).orEmpty())
        }
        val status = TextView(this).apply {
            setTextColor(color(R.color.muted))
            textSize = 13f
            setPadding(0, pad / 3, 0, 0)
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
            addView(input)
            addView(status)
        }
        val dlg = MaterialAlertDialogBuilder(this)
            .setTitle("Apple Music playlist link")
            .setMessage("In Apple Music open the playlist, tap Share → Copy Link, and paste it here. " +
                "No sign-in needed. Only playlists anyone can open work (not private ones).")
            .setView(box)
            .setPositiveButton("Get songs", null)      // click handled below so the dialog can stay open
            .setNegativeButton("Cancel", null)
            .create()
        dlg.setOnShowListener {
            val go = dlg.getButton(android.content.DialogInterface.BUTTON_POSITIVE)
            go.setOnClickListener {
                val link = AppleMusicData.findLink(input.text.toString())
                if (link == null) {
                    status.setTextColor(ERROR_RED)
                    status.text = if (input.text.isBlank()) "Paste the link first."
                        else "That doesn't look like an Apple Music playlist or album link (music.apple.com/…)."
                    return@setOnClickListener
                }
                go.isEnabled = false
                input.isEnabled = false
                status.setTextColor(color(R.color.muted))
                status.text = "Reading the playlist…"
                lifecycleScope.launch {
                    when (val r = AppleMusic.read(link)) {
                        is AmResult.Failed -> {
                            status.setTextColor(ERROR_RED)
                            status.text = r.why
                            go.isEnabled = true
                            input.isEnabled = true
                        }
                        is AmResult.Ok -> {
                            dlg.dismiss()
                            useAppleList(r.list)
                        }
                    }
                }
            }
        }
        dlg.setOnDismissListener { if (appleDialog === dlg) appleDialog = null }
        appleDialog = dlg
        dlg.show()
    }

    /** Fill the song box with the playlist; its name becomes the list name (queue sub-folder). */
    private fun useAppleList(list: AmList) {
        val name = list.name.ifEmpty { "Apple Music playlist" }
        b.songs.setText(list.songs.joinToString("\n"))
        prefs.listName = SongList.safeFileName(name)
        toast("Loaded ${list.songs.size} songs from $name")
        val total = list.declaredCount
        if (total != null && total > list.songs.size) {
            MaterialAlertDialogBuilder(this)
                .setTitle("${total - list.songs.size} songs couldn't be loaded")
                .setMessage("This playlist has $total songs, but Apple Music's public web page only shows the first " +
                    "${list.songs.size}, so the last ${total - list.songs.size} aren't in the list.\n\n" +
                    "To get them too, split the playlist into smaller ones (about 100 songs each) in Apple Music, " +
                    "or export it to a .txt file with another service and use Import.")
                .setPositiveButton("OK", null)
                .show()
        }
    }

    /** No Spotify login on Android: Chosic turns a playlist into "Artist - Title" text to paste or load. */
    private fun openChosic() {
        val ok = runCatching {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(CHOSIC_URL)))
        }.isSuccess
        if (!ok) { Toast.makeText(this, "No browser found to open Chosic", Toast.LENGTH_LONG).show(); return }
        Toast.makeText(
            this,
            "In Chosic: paste the Spotify playlist link, then copy or download the song list and paste it here or use Import",
            Toast.LENGTH_LONG,
        ).show()
    }

    private fun render(s: RunState) {
        val logAtBottom = b.logScroll.getChildAt(0)?.let { b.logScroll.scrollY + b.logScroll.height >= it.height - BOTTOM_SLACK_PX } ?: true
        // New run: clear the old log.
        if (s.runId != shownRun) {
            shownRun = s.runId
            songText.clear()
            shownSongs = s.songsAdded - s.songs.size
            shownDetails = s.added - s.lines.size
            b.details.text = ""
            if (detailsOpen) b.details.text = s.lines.joinToString("\n").also { shownDetails = s.added }
        }

        // Tidy song list: add only the new lines.
        var grew = false
        val freshSongs = (s.songsAdded - shownSongs).toInt().coerceIn(0, s.songs.size)
        if (freshSongs > 0) {
            s.songs.takeLast(freshSongs).forEach { appendSong(it) }
            shownSongs = s.songsAdded
            grew = true
        }
        if (songText.isEmpty()) {
            b.songLog.text = if (s.running) "" else "Downloaded songs show up here."
            b.songLog.setTextColor(color(R.color.muted))
        } else if (grew) {
            b.songLog.setText(songText, TextView.BufferType.SPANNABLE)
        }

        // Technical details (only while open).
        if (detailsOpen) {
            val freshLines = (s.added - shownDetails).toInt().coerceIn(0, s.lines.size)
            if (freshLines > 0) {
                b.details.append(s.lines.takeLast(freshLines).joinToString("\n", prefix = if (b.details.text.isEmpty()) "" else "\n"))
                shownDetails = s.added
                grew = true
            }
        }

        // The song being worked on right now.
        val current = if (s.running && s.song.isNotEmpty()) "⋯ ${s.song}" + (if (s.step.isNotEmpty()) "\n    ${s.step}" else "") else ""
        if (b.currentSong.text.toString() != current) {
            b.currentSong.text = current
            b.currentSong.visibility = if (current.isEmpty()) View.GONE else View.VISIBLE
            grew = true
        }
        b.summary.text = s.summary
        b.summary.visibility = if (!s.running && s.summary.isNotEmpty()) View.VISIBLE else View.GONE
        if (grew && logAtBottom) scrollLogToEnd(force = true)

        // Log header: a short status even when the log is folded away.
        b.logTitle.text = when {
            s.running && s.paused -> "Log · paused"
            s.running && s.total > 0 -> "Log · ${s.index} / ${s.total}" + if (s.itemCount > 1) "  (list ${s.itemIndex}/${s.itemCount})" else ""
            s.summary.isNotEmpty() -> "Log · ${s.summary}"
            else -> "Log"
        }

        // Buttons and progress.
        b.pause.visibility = if (s.running) View.VISIBLE else View.GONE
        b.pause.text = if (s.paused) "Resume" else "Pause"
        b.progressBox.visibility = if (s.running) View.VISIBLE else View.GONE
        for (v in listOf(b.songs, b.loadTxt, b.chosic, b.appleMusic, b.clearSongs, b.chooseFolder, b.srcBoth, b.srcBandcamp, b.srcYoutube)) {
            v.isEnabled = !s.running
        }
        for (v in listOf(b.loadTxt, b.chosic, b.appleMusic)) v.alpha = if (s.running) 0.45f else 1f   // dimmed while downloading
        b.start.backgroundTintList = ColorStateList.valueOf(color(if (s.running) R.color.stop_red else R.color.accent))
        b.start.setTextColor(if (s.running) Color.WHITE else color(R.color.on_accent))
        if (s.running) {
            b.start.text = "Stop"
            b.progressList.visibility = if (s.itemCount > 1) View.VISIBLE else View.GONE
            b.progressList.text = "List ${s.itemIndex} of ${s.itemCount} · ${s.itemName}"
            if (s.paused) {
                b.progressText.text = "Paused · ${s.index} / ${s.total} · ${s.song}"
                b.progressStep.text = "Tap Resume to carry on"
            } else if (s.total > 0 && s.index > 0) {
                val pct = s.percent
                b.progressText.text = "${s.index} / ${s.total} · ${s.song}" + if (pct != null && pct > 0) "  ${pct.toInt()}%" else ""
                b.progressStep.text = s.step
                val within = ((pct ?: 0f).coerceIn(0f, 100f)) / 100f
                b.progressBar.setProgressCompat((((s.index - 1) + within) / s.total * 1000).toInt(), true)
            } else {
                b.progressText.text = "Starting…"
                b.progressStep.text = ""
                b.progressBar.setProgressCompat(0, false)
            }
        }
        if (s.itemIndex != lastItemIndex) {
            lastItemIndex = s.itemIndex
            refreshQueue()
        }
        if (wasRunning != s.running) {
            wasRunning = s.running
            refreshQueue()
            refreshRetry()
            if (!s.running) refreshEngineStatus()      // a run may have updated yt-dlp
        }
    }

    companion object {
        private const val DAY_MS = 24 * 60 * 60 * 1000L
        private const val BOTTOM_SLACK_PX = 120
        private const val CHOSIC_URL = "https://www.chosic.com/spotify-playlist-exporter/"
        private val ERROR_RED = Color.parseColor("#FF6B6B")
    }
}
