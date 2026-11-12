package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.HAKUX_PACKAGE
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.emulators.XboxSaves
import java.io.File

/**
 * hakuX (Xbox) save scanner.
 *
 * hakuX does not run on a disk image: its setup unpacks the Xbox hard disk
 * into host folders, one per partition, and E: is ``Partition1``.  A game's
 * saves are ``Partition1/UDATA/<title id>/`` — the same tree the Xbox client
 * reads off FATX, so each title folder is one save bundled with '\' paths and
 * keyed by its 8-hex title id.
 *
 * Stock hakuX keeps that tree in its private app storage
 * (``files/native/hdd``), out of reach without root.  ``tools/patch_xbox_emu.py``
 * patches the APK so ``files/native`` is a link into the app's external
 * folder, where this scanner reads it.  [problem] tells the cases apart.
 */
class HakuXEmulator(
    /** Optional override: the UDATA folder itself, or the hdd root holding Partition1. */
    private val saveDirOverride: String? = null
) : EmulatorBase() {

    override val name: String = "hakuX"
    override val systemPrefix: String = "XBOX"

    override fun discoverSaves(): List<SaveEntry> {
        val udata = udataDir(baseDir, saveDirOverride) ?: return emptyList()
        return udata.listFiles()
            ?.filter { it.isDirectory && XboxSaves.isGameTitleId(it.name) }
            ?.filter { dir -> dir.walkTopDown().any { it.isFile } }
            ?.map { dir ->
                val titleId = dir.name.uppercase()
                SaveEntry(
                    titleId = titleId,
                    displayName = XboxSaves.titleName(dir) ?: titleId,
                    systemName = "XBOX",
                    saveFile = null,
                    saveDir = dir,
                    isMultiFile = true
                )
            }
            ?.sortedBy { it.displayName.lowercase() }
            .orEmpty()
    }

    companion object {
        const val EMULATOR_KEY = "hakux"

        /** hakuX's hdd root once patched: ``files/native`` links here. */
        const val HDD_PATH = "Android/data/$HAKUX_PACKAGE/files/native/hdd"

        /** E:\UDATA, or null when hakuX has no reachable disk (not patched, or not set up). */
        fun udataDir(externalRoot: File, saveDirOverride: String? = null): File? {
            val root = saveDirOverride?.takeIf { it.isNotBlank() }?.let(::File) ?: File(externalRoot, HDD_PATH)
            if (root.name.equals("UDATA", ignoreCase = true)) return root.takeIf { it.isDirectory }
            val e = XboxSaves.child(root, "Partition1") ?: return null
            return XboxSaves.child(e, "UDATA") ?: File(e, "UDATA")
        }

        /** Where a title's saves go: its folder in any case, else a new one named by the id. */
        fun titleDir(udata: File, titleId: String): File =
            XboxSaves.child(udata, titleId)?.takeIf { it.isDirectory } ?: File(udata, titleId.uppercase())

        /**
         * Why hakuX's saves can't be reached, or null when they can.  The
         * patch makes ``files/native`` on its first start; the disk's
         * folders come with hakuX's setup.
         */
        fun problem(externalRoot: File, saveDirOverride: String? = null): String? {
            if (saveDirOverride?.isNotBlank() == true) {
                return if (udataDir(externalRoot, saveDirOverride)?.parentFile?.isDirectory == true) null
                else "The hakuX folder set in Emulator Configuration has no Partition1/UDATA."
            }
            val hdd = File(externalRoot, HDD_PATH)
            return when {
                hdd.parentFile?.isDirectory != true ->
                    "hakuX keeps its saves in private storage. Patch it with tools/patch_xbox_emu.py " +
                        "and open it once."
                XboxSaves.child(hdd, "Partition1") == null ->
                    "hakuX has no hard disk yet. Finish its setup (Hard Disk: xbox_hdd.qcow2)."
                else -> null
            }
        }
    }
}
