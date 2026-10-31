package io.github.obamna1234.sevby

import android.content.Context
import android.provider.DocumentsContract
import androidx.documentfile.provider.DocumentFile

/**
 * Answers "is this song already in the folder?" before anything is downloaded.
 *
 * It first asks for the file directly by its exact name (fast, and works even when listing a
 * folder fails), then falls back to one listing of the whole folder.
 */
class FolderIndex(context: Context, private val folder: DocumentFile, private val log: (String) -> Unit) {

    private val resolver = context.contentResolver
    private val folderId: String? = runCatching { DocumentsContract.getDocumentId(folder.uri) }.getOrNull()
    private val listing: Map<String, Long>? by lazy { listOnce() }

    /** Size in bytes of [name] in the folder, or null if it isn't there. */
    fun sizeOf(name: String): Long? = direct(name) ?: listing?.get(name.lowercase())

    private fun direct(name: String): Long? {
        val id = folderId ?: return null
        return runCatching {
            val uri = DocumentsContract.buildDocumentUriUsingTree(folder.uri, "$id/$name")
            resolver.query(uri, arrayOf(DocumentsContract.Document.COLUMN_SIZE), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getLong(0) else null
            }
        }.getOrNull()
    }

    private fun listOnce(): Map<String, Long>? {
        val id = folderId ?: return null
        return try {
            val uri = DocumentsContract.buildChildDocumentsUriUsingTree(folder.uri, id)
            val cols = arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME, DocumentsContract.Document.COLUMN_SIZE)
            resolver.query(uri, cols, null, null, null)?.use { c ->
                buildMap {
                    while (c.moveToNext()) {
                        val n = c.getString(0) ?: continue
                        put(n.lowercase(), if (c.isNull(1)) 0L else c.getLong(1))
                    }
                }
            }
        } catch (e: Exception) {
            log("(Couldn't read the folder's file list: ${e.message} – checking songs one by one instead)")
            null
        }
    }

    /** How many MP3s the folder holds (for the log), or null if it couldn't be read. */
    fun mp3Count(): Int? = listing?.keys?.count { it.endsWith(".mp3") }
}
