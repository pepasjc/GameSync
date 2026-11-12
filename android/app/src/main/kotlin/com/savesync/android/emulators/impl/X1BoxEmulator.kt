package com.savesync.android.emulators.impl

import android.util.Log
import com.savesync.android.SaveSyncApp
import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.emulators.X1BOX_PACKAGE
import com.savesync.android.emulators.XboxHddImage
import com.savesync.android.emulators.XboxSaves
import java.io.File

/**
 * X1 BOX (Xbox, xemu for Android) save scanner.
 *
 * X1 BOX runs xemu on the Xbox hard disk image its setup copied into
 * ``Android/data/com.izzy2lost.x1box/files/x1box/`` (``hdd.img``, raw or
 * qcow2 whatever the name).  Its own hakuX core works on a folder copy and
 * writes back into that image after each run, so the image is the one place
 * the saves live.
 *
 * The saves are read out of the image's FATX E:\UDATA ([XboxHddImage], the
 * port of the PS5 client's XPSemu code) into a staging folder per title, so
 * the rest of the sync treats them like any other save folder.  A download
 * lands in the staging folder and is written back into the image
 * ([writeBack]); X1 BOX must not be running then.
 *
 * X1 BOX writes its files owner-only (600), so GameSync can only read the
 * image after ``tools/patch_xbox_emu.py --app x1box`` widens them to 660.
 */
class X1BoxEmulator(
    /** Optional override: the disk image itself, or the folder holding it. */
    private val saveDirOverride: String? = null
) : EmulatorBase() {

    override val name: String = "X1 BOX"
    override val systemPrefix: String = "XBOX"

    override fun discoverSaves(): List<SaveEntry> {
        val image = imageFile(baseDir, saveDirOverride) ?: return emptyList()
        if (!image.canRead()) return emptyList()
        val staging = stagingRoot()
        staging.deleteRecursively()
        val out = mutableListOf<SaveEntry>()
        try {
            XboxHddImage.open(image).use { hdd ->
                for (titleId in hdd.titles()) {
                    val files = try {
                        hdd.readTitle(titleId)
                    } catch (e: Exception) {
                        Log.w(TAG, "$titleId skipped: ${e.message}")
                        continue
                    }
                    if (files.isEmpty()) continue
                    val dir = File(staging, titleId)
                    for ((path, data) in files) {
                        val target = File(dir, path.replace('\\', '/'))
                        target.parentFile?.mkdirs()
                        target.writeBytes(data)
                    }
                    out += SaveEntry(
                        titleId = titleId,
                        displayName = XboxSaves.titleName(dir) ?: titleId,
                        systemName = "XBOX",
                        saveFile = null,
                        saveDir = dir,
                        isMultiFile = true,
                        xboxHddImage = image
                    )
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "$image: ${e.message}")
        }
        return out.sortedBy { it.displayName.lowercase() }
    }

    companion object {
        const val EMULATOR_KEY = "x1box"
        private const val TAG = "X1BoxEmulator"

        /** X1 BOX's own files: the setup copies the disk image here. */
        const val FILES_PATH = "Android/data/$X1BOX_PACKAGE/files/x1box"
        private val IMAGE_NAMES = listOf("hdd.img", "xbox_hdd.qcow2", "hdd.qcow2")

        private var backedUp = false

        /** Where scanned saves are unpacked: GameSync's own storage, one folder per title. */
        fun stagingRoot(): File = File(SaveSyncApp.instance.filesDir, "xbox_x1box")

        fun stagingDir(titleId: String): File = File(stagingRoot(), titleId.uppercase())

        /**
         * The disk image: the override (a file, or a folder holding one), else
         * xemu.toml's hdd_path when it is under X1 BOX's folder, else the
         * names the setup uses.
         */
        fun imageFile(externalRoot: File, saveDirOverride: String? = null): File? {
            val override = saveDirOverride?.takeIf { it.isNotBlank() }?.let(::File)
            if (override != null) {
                if (override.isFile) return override
                return IMAGE_NAMES.map { File(override, it) }.firstOrNull { it.isFile }
            }
            val dir = File(externalRoot, FILES_PATH)
            tomlHddPath(File(dir, "xemu.toml"))?.let { File(it) }?.takeIf { it.isFile }?.let { return it }
            return IMAGE_NAMES.map { File(dir, it) }.firstOrNull { it.isFile }
        }

        /** [sys.files] hdd_path from an xemu.toml, if it has one. */
        private fun tomlHddPath(toml: File): String? {
            if (!toml.isFile || !toml.canRead()) return null
            var inSection = false
            for (raw in toml.readLines()) {
                val line = raw.trim()
                if (line.startsWith("[")) {
                    inSection = line == "[sys.files]"
                    continue
                }
                if (!inSection) continue
                val key = line.substringBefore('=').trim()
                if (key == "hdd_path") {
                    return line.substringAfter('=').trim().trim('\'', '"').takeIf { it.isNotEmpty() }
                }
            }
            return null
        }

        /** Why X1 BOX's saves can't be reached, or null when they can. */
        fun problem(externalRoot: File, saveDirOverride: String? = null): String? {
            val image = imageFile(externalRoot, saveDirOverride)
                ?: return if (saveDirOverride?.isNotBlank() == true) {
                    "No disk image at the X1 BOX path set in Emulator Configuration."
                } else {
                    "No X1 BOX hard disk image found. Finish its setup (Hard Disk)."
                }
            return if (image.canRead()) null
            else "X1 BOX writes its disk image owner-only. Patch it with " +
                "tools/patch_xbox_emu.py --app x1box and open it once."
        }

        /**
         * A downloaded title's staging folder written into the image, replacing
         * the title's E:\UDATA folder.  The image is copied to ``<image>.bak``
         * first, once per app run.
         */
        fun writeBack(image: File, titleId: String, dir: File) {
            val files = XboxSaves.treeFiles(dir).map { (path, file) -> path to file.readBytes() }
            synchronized(this) {
                if (!backedUp) {
                    XboxHddImage.backup(image, File(image.path + ".bak"))
                    backedUp = true
                }
                XboxHddImage.openWritable(image).use { it.writeTitle(titleId.uppercase(), files) }
            }
        }
    }
}
