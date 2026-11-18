package io.github.obamna1234.sevby

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdateDataTest {

    private fun release(tag: String, vararg apks: String, draft: Boolean = false) =
        """{"tag_name":"$tag","draft":$draft,"html_url":"https://github.com/x/y/releases/tag/$tag","assets":[""" +
            apks.joinToString(",") { """{"name":"$it","browser_download_url":"https://github.com/Obamna1234/sevby/releases/download/$tag/$it"}""" } + "]}"

    private fun list(vararg r: String) = "[" + r.joinToString(",") + "]"

    @Test fun allOsReleaseWithApkIsFound() {
        val json = list(
            release("v1.0.4", "SEVBY-Windows.zip", "SEVBY-1.0.4-arm64-v8a.apk", "SEVBY-1.0.4-universal.apk"),
            release("android-v1.0.0-beta.5", "SEVBY-1.0.0-beta.5-arm64-v8a.apk"),
        )
        val r = AppUpdateData.newest(json, listOf("arm64-v8a"))!!
        assertEquals("1.0.4", r.version)
        assertEquals("https://github.com/Obamna1234/sevby/releases/download/v1.0.4/SEVBY-1.0.4-arm64-v8a.apk", r.apkUrl)
    }

    @Test fun desktopOnlyReleasesAreSkipped() {
        val json = list(
            release("v1.0.2", "SEVBY-Windows.zip", "SEVBY-Linux.zip"),
            release("android-v1.0.0-beta.5", "SEVBY-1.0.0-beta.5-arm64-v8a.apk"),
        )
        assertEquals("1.0.0-beta.5", AppUpdateData.newest(json, listOf("arm64-v8a"))!!.version)
    }

    @Test fun mirrorCopyAndAllOsReleaseAreTheSameVersion() {
        val json = list(
            release("android-v1.0.4", "SEVBY-1.0.4-arm64-v8a.apk"),
            release("v1.0.4", "SEVBY-1.0.4-arm64-v8a.apk"),
        )
        assertEquals("1.0.4", AppUpdateData.newest(json, listOf("arm64-v8a"))!!.version)
    }

    @Test fun hqStyleTagsWork() {
        val json = list(release("v0.10.3-beta", "SEVBY-HQ-0.10.3-beta-arm64-v8a.apk", "SEVBY-HQ-0.10.3-beta-universal.apk"))
        val r = AppUpdateData.newest(json, listOf("x86_64"))!!
        assertEquals("0.10.3-beta", r.version)
        assertTrue(r.apkUrl.endsWith("-universal.apk"))                        // no x86_64 APK: universal
        assertTrue(AppUpdateData.isNewer("0.10.3-beta", "0.10.2-beta"))
    }

    @Test fun otherTagsAndDraftsAreIgnored() {
        assertNull(AppUpdateData.versionOf("nightly"))
        assertNull(AppUpdateData.versionOf("v"))
        assertNull(AppUpdateData.versionOf("version-2"))
        assertEquals("1.0.4", AppUpdateData.versionOf("v1.0.4"))
        assertEquals("1.0.0-beta.5", AppUpdateData.versionOf("android-v1.0.0-beta.5"))
        assertNull(AppUpdateData.newest(list(release("v9.0.0", "a-arm64-v8a.apk", draft = true)), listOf("arm64-v8a")))
    }

    @Test fun newVersionsBeatEveryOldOne() {
        assertTrue(AppUpdateData.isNewer("1.0.4", "1.0.0-beta.5"))
        assertTrue(AppUpdateData.isNewer("1.0.4", "1.0.3"))
        assertTrue(AppUpdateData.isNewer("1.0.4", "1.0.4-dev.12"))           // release beats its test builds
        assertTrue(AppUpdateData.isNewer("1.0.5-dev.3", "1.0.4"))
    }
}
