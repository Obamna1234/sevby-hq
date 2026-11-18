package io.github.obamna1234.sevby

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SafetyTest {

    @Test fun longNonLatinNamesFitInAFileName() {
        val line = "アーティスト - " + "とても長い曲のタイトル".repeat(12)        // ~400 bytes in UTF-8
        val safe = SongList.safeFileName(line)
        assertTrue(safe.toByteArray(Charsets.UTF_8).size <= SongList.MAX_NAME_BYTES)
        assertTrue(safe.startsWith("アーティスト - "))
        assertEquals(safe, SongList.safeFileName(safe))                       // stable: same name next run
    }

    @Test fun controlCharactersAndBadCharactersAreRemoved() {
        assertEquals("Kataklysm - The Black Sheep", SongList.safeFileName("Kataklysm\u0001 - The Black\u007F Sheep"))
        assertEquals("ACDC - Back In Black", SongList.safeFileName("AC/DC - Back In Black"))
        assertEquals("Song", SongList.safeFileName("Song..."))
        assertEquals("Kataklysm - The Black Sheep", SongList.safeFileName("Kataklysm - The Black Sheep"))  // normal names unchanged
    }

    @Test fun brokenHtmlEntitiesDontCrash() {
        assertEquals("a &#x110000; b", BandcampData.unescape("a &#x110000; b"))
        assertEquals("&#99999999999;", BandcampData.unescape("&#99999999999;"))
        assertEquals("Tom & Jerry é", BandcampData.unescape("Tom &amp; Jerry &#233;"))
    }

    private fun release(tag: String, vararg assets: Pair<String, String>) =
        """[{"tag_name":"$tag","draft":false,"html_url":"https://github.com/Obamna1234/sevby/releases/tag/$tag","assets":[""" +
            assets.joinToString(",") { (n, u) -> """{"name":"$n","browser_download_url":"$u"}""" } + "]}]"

    @Test fun updaterPrefersTheApkForThisVersion() {
        val dl = "https://github.com/Obamna1234/sevby/releases/download/android-v1.0.4/"
        val json = release("android-v1.0.4",
            "SEVBY-1.0.5-dev.3-arm64-v8a.apk" to dl + "SEVBY-1.0.5-dev.3-arm64-v8a.apk",
            "SEVBY-1.0.4-arm64-v8a.apk" to dl + "SEVBY-1.0.4-arm64-v8a.apk")
        assertEquals(dl + "SEVBY-1.0.4-arm64-v8a.apk", AppUpdateData.newest(json, listOf("arm64-v8a"))!!.apkUrl)
    }

    @Test fun updaterOnlyOffersGitHubDownloads() {
        assertNull(AppUpdateData.newest(release("v9.9.9", "x-arm64-v8a.apk" to "http://github.com/a/b/releases/download/x.apk"), listOf("arm64-v8a")))
        assertNull(AppUpdateData.newest(release("v9.9.9", "x-arm64-v8a.apk" to "https://evil.example/x-arm64-v8a.apk"), listOf("arm64-v8a")))
        assertTrue(AppUpdateData.isGitHubDownload("https://github.com/Obamna1234/sevby-hq/releases/download/v0.10.3-beta/a.apk"))
        assertFalse(AppUpdateData.isGitHubDownload("javascript:alert(1)"))
    }
}
