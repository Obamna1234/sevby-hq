package io.github.obamna1234.sevby

import android.annotation.SuppressLint
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.webkit.WebView
import android.webkit.WebViewClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import org.json.JSONTokener
import kotlin.coroutines.resume

/**
 * A hidden copy of Android's own browser engine (WebView). Bandcamp sometimes refuses
 * requests that don't come from a real browser (common on mobile data and hotspots);
 * pages opened here look exactly like a normal phone browser visiting Bandcamp.
 */
class BandcampWeb(private val context: Context) {

    private var web: WebView? = null
    private val main = Handler(Looper.getMainLooper())

    @SuppressLint("SetJavaScriptEnabled")
    private fun view(): WebView = web ?: WebView(context.applicationContext).also { w ->
        w.settings.javaScriptEnabled = true
        w.settings.domStorageEnabled = true
        w.settings.blockNetworkImage = true        // pictures aren't needed, only the page data
        web = w
    }

    /**
     * Open [url] and keep running [script] (JavaScript that returns a string, or null while the
     * page isn't ready) until it gives an answer or [timeoutMs] passes. Bot-check pages reload
     * themselves into the real page after a few seconds, so this simply waits for that.
     */
    suspend fun read(url: String, script: String, timeoutMs: Long = 30_000): String? = withContext(Dispatchers.Main) {
        suspendCancellableCoroutine { cont ->
            val w = view()
            val started = System.currentTimeMillis()
            var finished = false

            fun done(result: String?) {
                if (finished) return
                finished = true
                main.removeCallbacksAndMessages(null)
                runCatching { w.stopLoading() }
                if (cont.isActive) cont.resume(result)
            }

            lateinit var poll: Runnable
            poll = Runnable {
                if (finished) return@Runnable
                w.evaluateJavascript(script) { raw ->
                    // evaluateJavascript hands back the result encoded as JSON (a quoted string, or null).
                    val value = runCatching { JSONTokener(raw ?: "null").nextValue() as? String }.getOrNull()
                    when {
                        !value.isNullOrEmpty() -> done(value)
                        System.currentTimeMillis() - started > timeoutMs -> done(null)
                        else -> main.postDelayed(poll, 700)
                    }
                }
            }

            w.webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, u: String?) {
                    main.removeCallbacks(poll)
                    main.postDelayed(poll, 300)
                }
            }
            main.postDelayed({ done(null) }, timeoutMs + 2_000)   // hard limit
            cont.invokeOnCancellation { main.post { done(null) } }
            w.loadUrl(url)
        }
    }

    fun close() {
        main.post {
            web?.run { stopLoading(); destroy() }
            web = null
        }
    }

    companion object {
        /** Returns the track data as JSON text once the real page (not a bot check) has loaded. */
        const val TRACK_SCRIPT = """
            (function() {
              var t = document.querySelector('[data-tralbum]');
              if (!t) return null;
              var e = document.querySelector('[data-embed]');
              var og = document.querySelector('meta[property="og:image"]');
              return JSON.stringify({
                tralbum: t.getAttribute('data-tralbum'),
                embed: e ? e.getAttribute('data-embed') : null,
                og: og ? og.getAttribute('content') : null
              });
            })()"""

        /** Returns the search results page's tracks as JSON text (same shape as the search API). */
        const val SEARCH_SCRIPT = """
            (function() {
              if (!document.querySelector('.result-items, .searchresult, #search-results-container, .search-results')) return null;
              var out = [];
              document.querySelectorAll('li.searchresult').forEach(function(li) {
                var type = (li.querySelector('.itemtype') || {}).textContent || '';
                if (type.trim().toUpperCase() !== 'TRACK') return;
                var a = li.querySelector('.heading a');
                var sub = ((li.querySelector('.subhead') || {}).textContent || '').replace(/\s+/g, ' ').trim();
                var m = sub.match(/^(?:from (.+?) )?by (.+)$/);
                if (!a) return;
                out.push({type: 't', name: a.textContent.trim(), url: a.href.split('?')[0],
                          band_name: m ? m[2] : '', album_name: m && m[1] ? m[1] : ''});
              });
              return JSON.stringify({results: out});
            })()"""
    }
}
