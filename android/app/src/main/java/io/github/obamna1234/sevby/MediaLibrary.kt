package io.github.obamna1234.sevby

import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Environment
import android.provider.DocumentsContract
import java.io.File

/**
 * Tells Android's media library about a new song so music apps show it straight away
 * (otherwise they may only notice it after the next automatic scan).
 */
object MediaLibrary {

    /**
     * [onResult] gets true when Android's library accepted the song, false when it didn't,
     * and is not called when the folder's location can't be worked out (e.g. some SD cards).
     */
    fun announce(context: Context, treeUri: Uri, fileName: String, onResult: (Boolean) -> Unit = {}) {
        runCatching {
            val dir = pathOf(treeUri) ?: return
            val path = File(dir, fileName).absolutePath
            MediaScannerConnection.scanFile(context, arrayOf(path), arrayOf("audio/mpeg")) { _, uri ->
                onResult(uri != null)
            }
        }
    }

    /** "primary:Music/SEVBY" → /storage/emulated/0/Music/SEVBY ; "1A2B-3C4D:Music" → /storage/1A2B-3C4D/Music */
    private fun pathOf(treeUri: Uri): File? {
        val id = DocumentsContract.getTreeDocumentId(treeUri) ?: return null
        val volume = id.substringBefore(':')
        val rel = id.substringAfter(':', "")
        val root = if (volume.equals("primary", true)) Environment.getExternalStorageDirectory() else File("/storage/$volume")
        return if (rel.isEmpty()) root else File(root, rel)
    }
}
