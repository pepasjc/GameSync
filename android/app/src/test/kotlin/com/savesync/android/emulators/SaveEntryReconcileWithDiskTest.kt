package com.savesync.android.emulators

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * A server-only row is only a prediction of where a save would land.  Once the
 * file is really on disk it must behave as a local save — otherwise a smart
 * sync sees "nothing local" and downloads over newer play (the Snatcher /
 * DuckStation regression: card downloaded, game played, list still stale).
 */
class SaveEntryReconcileWithDiskTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun serverOnlyCard(card: File) = SaveEntry(
        titleId = "SLPS00154",
        displayName = "Snatcher (Japan)",
        systemName = "PS1",
        saveFile = card,
        saveDir = null,
        isServerOnly = true
    )

    @Test
    fun `predicted card that now exists becomes a local save`() {
        val card = File(tmp.newFolder("memcards"), "Snatcher (Japan)_1.mcd")
        card.writeBytes(ByteArray(128) { 0x4D })

        val entry = serverOnlyCard(card).reconcileWithDisk()

        assertFalse(entry.isServerOnly)
        assertTrue(entry.exists())
        assertTrue(entry.computeHash().isNotBlank())
    }

    @Test
    fun `predicted card still missing stays server-only`() {
        val card = File(tmp.newFolder("memcards"), "Snatcher (Japan)_1.mcd")

        val entry = serverOnlyCard(card).reconcileWithDisk()

        assertTrue(entry.isServerOnly)
        assertFalse(entry.exists())
        assertEquals("", entry.computeHash())
    }

    @Test
    fun `predicted directory counts only once it holds a file`() {
        val slot = tmp.newFolder("SAVEDATA", "ULUS10041")
        val entry = SaveEntry(
            titleId = "ULUS10041",
            displayName = "ULUS10041",
            systemName = "PSP",
            saveFile = null,
            saveDir = slot,
            isMultiFile = false,
            isServerOnly = true
        )

        assertTrue(entry.reconcileWithDisk().isServerOnly)

        File(slot, "DATA.BIN").writeBytes(byteArrayOf(1, 2, 3))
        assertFalse(entry.reconcileWithDisk().isServerOnly)
    }

    @Test
    fun `shared yabasanshiro container is left server-only`() {
        val backup = File(tmp.newFolder("yaba"), "backup.bin")
        backup.writeBytes(ByteArray(64))
        val entry = SaveEntry(
            titleId = "SAT_T-1234G",
            displayName = "Some Saturn Game",
            systemName = "SAT",
            saveFile = backup,
            saveDir = null,
            isServerOnly = true
        )

        assertTrue(entry.reconcileWithDisk().isServerOnly)
    }
}
