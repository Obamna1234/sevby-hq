package io.github.obamna1234.sevby

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException

/** Result of reading an Apple Music link. */
sealed class AmResult {
    data class Ok(val list: AmList) : AmResult()
    data class Failed(val why: String) : AmResult()
}

/**
 * Gets the song list of a public Apple Music playlist or album share link.
 * One request with a normal browser User-Agent; no sign-in and no Apple API (same as the desktop app).
 */
object AppleMusic {

    private const val UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"

    suspend fun read(link: String): AmResult = withContext(Dispatchers.IO) {
        val url = AppleMusicData.normalise(link)
        val html = try {
            val con = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 15_000
                readTimeout = 25_000
                instanceFollowRedirects = true
                setRequestProperty("User-Agent", UA)
                setRequestProperty("Accept", "text/html,application/xhtml+xml")
                setRequestProperty("Accept-Language", "en-US,en;q=0.9")
            }
            try {
                val code = con.responseCode
                if (code != 200) {
                    return@withContext AmResult.Failed(
                        "Apple Music said no (error $code). Is the playlist public? In Apple Music use Share → Copy Link."
                    )
                }
                con.inputStream.bufferedReader().readText()
            } finally {
                con.disconnect()
            }
        } catch (e: SocketTimeoutException) {
            return@withContext AmResult.Failed("Apple Music took too long to answer. Check your internet connection and try again.")
        } catch (e: UnknownHostException) {
            return@withContext AmResult.Failed("Couldn't reach Apple Music. Check your internet connection.")
        } catch (e: Exception) {
            return@withContext AmResult.Failed("Couldn't reach Apple Music (${e.message ?: e.javaClass.simpleName}).")
        }
        val list = AppleMusicData.parse(html)
        if (list.songs.isEmpty()) {
            AmResult.Failed(
                "Couldn't find any songs on that page. The playlist may be private, or Apple changed its page. " +
                    "You can also export it to text with another service and paste it here or use Import."
            )
        } else {
            AmResult.Ok(list)
        }
    }
}
