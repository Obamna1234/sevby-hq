package io.github.obamna1234.sevby

import android.content.Context
import android.content.Intent
import android.os.Build
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/** How a song ended up, for the tidy song list. HEADER marks the start of a queued list. */
enum class Mark { OK, SKIPPED, FAILED, HEADER }

/** One finished song (or a list heading) in the song list. */
data class SongLine(val text: String, val mark: Mark, val note: String = "")

/** One song list to download. [subfolder] null = straight into the chosen folder. */
data class QueueItem(val name: String, val songs: List<String>, val subfolder: String?)

/** One run: one or more lists, one after another. */
data class Job(val items: List<QueueItem>, val folderUri: String, val source: Source, val fromQueue: Boolean = false)

/** What the screen and the notification show. */
data class RunState(
    val running: Boolean = false,
    val paused: Boolean = false,
    /** Which list of the queue is running (1-based) and how many there are. */
    val itemIndex: Int = 0,
    val itemCount: Int = 0,
    val itemName: String = "",
    val index: Int = 0,
    val total: Int = 0,
    val song: String = "",
    val percent: Float? = null,
    /** What SEVBY is doing for the current song ("Searching Bandcamp…"). */
    val step: String = "",
    /** Finished songs (the tidy list). */
    val songs: List<SongLine> = emptyList(),
    /** Total song lines ever added in this run (keeps counting after old ones are dropped). */
    val songsAdded: Long = 0,
    /** Full technical log (shown under "Details"). */
    val lines: List<String> = emptyList(),
    val added: Long = 0,
    /** Final one-line result, e.g. "Done · 31 downloaded · 2 already there". */
    val summary: String = "",
    /** Changes on every Start, so the screen knows to clear the old log. */
    val runId: Int = 0,
)

/**
 * Shared between the screen and the background service. The service does the work;
 * the screen just shows [state] (so it can be closed and reopened at any time).
 */
object Runner {
    private const val MAX_LINES = 3000
    private const val MAX_SONGS = 5000

    private val _state = MutableStateFlow(RunState())
    val state: StateFlow<RunState> = _state

    @Volatile private var pending: Job? = null
    @Volatile internal var downloader: Downloader? = null
    /** Set by Stop; the service also stops the rest of the queue. */
    @Volatile var stopRequested = false
        private set

    /** Start a run in the background. Returns false if one is already running. */
    fun start(context: Context, job: Job): Boolean {
        if (_state.value.running) return false
        pending = job
        stopRequested = false
        _state.value = RunState(
            running = true,
            itemCount = job.items.size,
            total = job.items.firstOrNull()?.songs?.size ?: 0,
            runId = _state.value.runId + 1,
        )
        val intent = Intent(context, DownloadService::class.java)
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
        return true
    }

    fun stop() {
        stopRequested = true
        downloader?.stop()
        _state.update { it.copy(paused = false) }
    }

    fun pause() {
        val d = downloader ?: return
        d.pause()
        _state.update { it.copy(paused = true, percent = null) }
        log("Paused")
        onChange?.invoke()
    }

    fun resume() {
        val d = downloader ?: return
        d.resume()
        _state.update { it.copy(paused = false) }
        log("Resumed")
        onChange?.invoke()
    }

    /** The service listens here to refresh its notification when Pause/Resume is pressed in the app. */
    @Volatile internal var onChange: (() -> Unit)? = null

    internal fun takePending(): Job? = pending.also { pending = null }

    /** A line for the technical log ("Details"). */
    fun log(line: String) = _state.update {
        val lines = if (it.lines.size >= MAX_LINES) it.lines.drop(it.lines.size - MAX_LINES + 1) else it.lines
        it.copy(lines = lines + line, added = it.added + 1)
    }

    /** A finished song (or a list heading) for the tidy song list. */
    fun song(line: SongLine) = _state.update {
        val songs = if (it.songs.size >= MAX_SONGS) it.songs.drop(it.songs.size - MAX_SONGS + 1) else it.songs
        it.copy(songs = songs + line, songsAdded = it.songsAdded + 1, step = "")
    }

    fun step(text: String) = _state.update { it.copy(step = text) }

    fun item(index: Int, count: Int, name: String, total: Int) =
        _state.update { it.copy(itemIndex = index, itemCount = count, itemName = name, total = total, index = 0, song = "") }

    fun progress(index: Int, total: Int, song: String, percent: Float?) =
        _state.update { it.copy(index = index, total = total, song = song, percent = percent) }

    internal fun finished(summary: String) =
        _state.update { it.copy(running = false, paused = false, percent = null, step = "", summary = summary) }
}
