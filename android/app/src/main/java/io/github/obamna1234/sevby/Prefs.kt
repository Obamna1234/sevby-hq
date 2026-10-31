package io.github.obamna1234.sevby

import android.content.Context
import androidx.core.content.edit
import org.json.JSONArray
import org.json.JSONObject

enum class Source { BOTH, BANDCAMP, YOUTUBE }

/** Small remembered settings: save folder, source, and the last song list. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("sevby", Context.MODE_PRIVATE)

    var folderUri: String?
        get() = sp.getString("folder_uri", null)
        set(v) = sp.edit { putString("folder_uri", v) }

    var source: Source
        get() = runCatching { Source.valueOf(sp.getString("source", null)!!) }.getOrDefault(Source.BOTH)
        set(v) = sp.edit { putString("source", v.name) }

    var draft: String
        get() = sp.getString("draft", "") ?: ""
        set(v) = sp.edit { putString("draft", v) }

    /** When SEVBY last asked GitHub for the newest yt-dlp (ms), and what it said. */
    var lastUpdateCheck: Long
        get() = sp.getLong("last_update_check", 0L)
        set(v) = sp.edit { putLong("last_update_check", v) }

    /** Newest SEVBY for Android release seen on GitHub (checked once a day). */
    var lastAppCheck: Long
        get() = sp.getLong("last_app_check", 0L)
        set(v) = sp.edit { putLong("last_app_check", v) }
    var latestApp: String
        get() = sp.getString("latest_app", "") ?: ""
        set(v) = sp.edit { putString("latest_app", v) }
    var latestAppApk: String
        get() = sp.getString("latest_app_apk", "") ?: ""
        set(v) = sp.edit { putString("latest_app_apk", v) }
    var latestAppPage: String
        get() = sp.getString("latest_app_page", "") ?: ""
        set(v) = sp.edit { putString("latest_app_page", v) }

    var latestYtDlp: String
        get() = sp.getString("latest_ytdlp", "") ?: ""
        set(v) = sp.edit { putString("latest_ytdlp", v) }

    var askedNotifications: Boolean
        get() = sp.getBoolean("asked_notifications", false)
        set(v) = sp.edit { putBoolean("asked_notifications", v) }

    /** Testing only: act as if Bandcamp blocked every request (checks the YouTube fallback). */
    var pretendBandcampBlocked: Boolean
        get() = sp.getBoolean("pretend_bc_blocked", false)
        set(v) = sp.edit { putBoolean("pretend_bc_blocked", v) }

    /** Name of the loaded .txt (without extension); used later as the queue item's sub-folder. */
    var listName: String
        get() = sp.getString("list_name", "") ?: ""
        set(v) = sp.edit { putString("list_name", v) }

    /** Lists waiting to be downloaded (Add to queue). */
    var queue: List<QueueItem>
        get() = runCatching { itemsFromJson(sp.getString("queue", "[]") ?: "[]") }.getOrDefault(emptyList())
        set(v) = sp.edit { putString("queue", itemsToJson(v)) }

    /** What was left when the last run was stopped (the Resume button runs it again; finished songs are skipped). */
    var resume: List<QueueItem>
        get() = runCatching { itemsFromJson(sp.getString("resume", "[]") ?: "[]") }.getOrDefault(emptyList())
        set(v) = sp.edit { putString("resume", itemsToJson(v)) }

    /** Songs that failed last time, grouped by the folder they belong in (for Retry failed). */
    var failed: List<QueueItem>
        get() = runCatching { itemsFromJson(sp.getString("failed", "[]") ?: "[]") }.getOrDefault(emptyList())
        set(v) = sp.edit { putString("failed", itemsToJson(v)) }

    /** Whether the log box is folded away. */
    var logCollapsed: Boolean
        get() = sp.getBoolean("log_collapsed", false)
        set(v) = sp.edit { putBoolean("log_collapsed", v) }

    companion object {
        fun itemsToJson(items: List<QueueItem>): String = JSONArray().apply {
            items.forEach { q ->
                put(JSONObject().put("name", q.name).put("sub", q.subfolder ?: JSONObject.NULL)
                    .put("songs", JSONArray().apply { q.songs.forEach { put(it) } }))
            }
        }.toString()

        fun itemsFromJson(s: String): List<QueueItem> {
            val arr = JSONArray(s)
            return (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                val songs = o.getJSONArray("songs")
                QueueItem(
                    name = o.optString("name"),
                    songs = (0 until songs.length()).map { songs.getString(it) },
                    subfolder = if (o.isNull("sub")) null else o.optString("sub"),
                )
            }
        }
    }
}
