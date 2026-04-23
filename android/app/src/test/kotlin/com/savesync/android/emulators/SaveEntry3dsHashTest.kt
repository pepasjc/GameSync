package com.savesync.android.emulators

import com.savesync.android.sync.HashUtils
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.MessageDigest

class SaveEntry3dsHashTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun `3ds save directories hash recursive contents without path bytes`() {
        val saveDir = tmp.newFolder("3ds-save")
        File(saveDir, "root.bin").writeText("root")
        File(saveDir, "nested").mkdirs()
        File(saveDir, "nested/inner.bin").writeText("inner")

        val entry = SaveEntry(
            titleId = "0004000000030800",
            displayName = "0004000000030800",
            systemName = "3DS",
            saveFile = null,
            saveDir = saveDir,
            isMultiFile = true
        )

        val digest = MessageDigest.getInstance("SHA-256")
        digest.update("inner".toByteArray())
        digest.update("root".toByteArray())
        val expected = digest.digest().joinToString("") { "%02x".format(it) }

        assertEquals(expected, entry.computeHash())
        assertEquals(expected, HashUtils.sha256DirTreeFiles(saveDir))
    }
}
