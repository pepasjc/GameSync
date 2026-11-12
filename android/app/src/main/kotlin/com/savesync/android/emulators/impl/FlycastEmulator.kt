package com.savesync.android.emulators.impl

import com.savesync.android.emulators.DreamcastSerialDatabase
import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.FlycastVmu
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.installed.InstalledRomsScanner
import java.io.File

/**
 * Standalone Flycast (`com.flycast.emulator`), Dreamcast.
 *
 * Saves sync when "Per Game VMU A1" is on: Flycast then writes
 * `<home>/data/<product no.>_vmu_save_A1.bin` per game, or reads a legacy
 * `<ROM name>_vmu_save_A1.bin` (see [FlycastVmu]).  The save is keyed
 * `DC_<serial>` like every other Dreamcast save: straight from a
 * product-number file name, else from the ROM's IP.BIN (GDI/CDI/ISO in the
 * ROM folders), else by name through the bundled Dreamcast DAT.
 */
class FlycastEmulator(
    private val romScanDir: String = "",
    private val romDirOverrides: Map<String, String> = emptyMap(),
    /** EmuDeck's `storage/` folder, checked for a `Flycast` home. */
    private val emudeckStorageDir: File? = null,
    /**
     * Optional explicit `data` folder, configured in the Emulator
     * Configuration screen.  Wins over the built-in candidates.
     */
    private val saveDirOverride: String? = null,
    /**
     * Whether [discoverRomEntries] offers Flycast's VMU path as the download
     * target for installed games — on when the user picked Flycast as their
     * Dreamcast emulator, so RetroArch's path is used otherwise.
     */
    private val providesDownloadTargets: Boolean = true,
) : EmulatorBase() {

    override val name: String = "Flycast"
    override val systemPrefix: String = "DC"

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "Flycast"

        /** Flycast homes, relative to external storage, in priority order. */
        private val HOME_CANDIDATES = listOf(
            "Flycast",
            "flycast",
            "Android/data/com.flycast.emulator/files",
        )

        /** Disc image extensions Flycast loads; GDI first (it names the tracks). */
        private val ROM_EXTENSIONS = setOf("gdi", "cdi", "chd", "cue", "iso")

        /**
         * The `data` folder Flycast writes VMUs to: the override if it exists,
         * else the first existing candidate.  Null when Flycast isn't set up.
         */
        fun findDataDir(
            externalStorage: File,
            emudeckStorageDir: File? = null,
            saveDirOverride: String? = null,
        ): File? {
            saveDirOverride?.takeIf { it.isNotBlank() }?.let(::File)
                ?.takeIf { it.isDirectory }
                ?.let { return it }
            val homes = buildList {
                emudeckStorageDir?.let { add(File(it, "Flycast")) }
                HOME_CANDIDATES.forEach { add(File(externalStorage, it)) }
            }
            return homes.map { File(it, "data") }.firstOrNull { it.isDirectory }
        }

        /** [findDataDir], or where Flycast's data folder would be when it doesn't exist yet. */
        fun defaultDataDir(
            externalStorage: File,
            emudeckStorageDir: File? = null,
            saveDirOverride: String? = null,
        ): File = findDataDir(externalStorage, emudeckStorageDir, saveDirOverride)
            ?: saveDirOverride?.takeIf { it.isNotBlank() }?.let(::File)
            ?: File(File(externalStorage, HOME_CANDIDATES.first()), "data")
    }

    private fun dataDir(): File? = findDataDir(baseDirOrNull(), emudeckStorageDir, saveDirOverride)

    /** External storage, or a dummy root off-device (unit tests) so only the override counts. */
    private fun baseDirOrNull(): File = try {
        baseDir
    } catch (_: RuntimeException) {
        File("/nonexistent")
    }

    override fun discoverSaves(): List<SaveEntry> {
        val dir = dataDir() ?: return emptyList()
        val roms by lazy { romFilesByStem() }
        val romNameByTitle by lazy {
            roms.values.associate { titleIdFor(it.nameWithoutExtension, it) to it.nameWithoutExtension }
        }
        val entries = dir.listFiles().orEmpty()
            .filter { it.isFile }
            .mapNotNull { file ->
                val stem = FlycastVmu.standaloneGameStem(file.name) ?: return@mapNotNull null
                val fromId = FlycastVmu.titleIdFromGameId(stem)
                val titleId = fromId ?: titleIdFor(stem, roms[stem.lowercase()])
                SaveEntry(
                    titleId = titleId,
                    displayName = if (fromId != null) {
                        romNameByTitle[titleId] ?: DreamcastSerialDatabase.nameForSerial(stem) ?: stem
                    } else stem,
                    systemName = systemPrefix,
                    saveFile = file,
                    saveDir = null,
                )
            }
        return FlycastVmu.loadedPerTitle(
            entries, { it.titleId }, { FlycastVmu.standaloneGameStem(it.saveFile!!.name)!! }
        )
    }

    /**
     * Every Dreamcast ROM in the ROM folders, with the VMU path Flycast would
     * write for it — so a server save for an installed game can be
     * downloaded before the game has ever saved here.  Empty unless Flycast
     * is the chosen Dreamcast emulator; its data folder is predicted when it
     * doesn't exist yet.
     */
    override fun discoverRomEntries(): Map<String, SaveEntry> {
        if (!providesDownloadTargets) return emptyMap()
        val dir = dataDir()
            ?: defaultDataDir(baseDirOrNull(), emudeckStorageDir, saveDirOverride)
        // Product-number cards Flycast already made (it loads those first).
        val byId: Map<String, File> = dir.listFiles().orEmpty()
            .mapNotNull { f ->
                FlycastVmu.standaloneGameStem(f.name)
                    ?.let { FlycastVmu.titleIdFromGameId(it) }
                    ?.let { it to f }
            }.toMap()
        return romFilesByStem().values.associate { rom ->
            val stem = rom.nameWithoutExtension
            val titleId = titleIdFor(stem, rom)
            titleId to SaveEntry(
                titleId = titleId,
                displayName = stem,
                systemName = systemPrefix,
                saveFile = byId[titleId] ?: File(dir, FlycastVmu.standaloneFileName(stem)),
                saveDir = null,
            )
        }
    }

    private fun titleIdFor(stem: String, rom: File?): String =
        rom?.let { readDreamcastProductCode(it) }
            ?: lookupDreamcastSerial(stem)
            ?: toTitleId(stem, systemPrefix)

    /** Lowercase ROM stem → disc image, from the DC ROM folders (flat or one game per folder). */
    private fun romFilesByStem(): Map<String, File> {
        val dirs = buildList {
            romDirOverrides["DC"]?.takeIf { it.isNotBlank() }?.let { add(File(it)) }
            if (romScanDir.isNotBlank()) {
                InstalledRomsScanner.SYSTEM_ROM_DIRS["DC"].orEmpty()
                    .forEach { add(File(romScanDir, it)) }
            }
        }.filter { it.isDirectory }.distinctBy { it.absolutePath.lowercase() }

        val map = mutableMapOf<String, File>()
        fun isRom(f: File) = f.isFile && f.extension.lowercase() in ROM_EXTENSIONS
        for (dir in dirs) {
            dir.listFiles().orEmpty().forEach { f ->
                when {
                    isRom(f) -> map.putIfAbsent(f.nameWithoutExtension.lowercase(), f)
                    f.isDirectory -> {
                        val files = f.listFiles().orEmpty().filter(::isRom)
                        val disc = files.firstOrNull { it.extension.equals("gdi", true) }
                            ?: files.firstOrNull()
                        if (disc != null) map.putIfAbsent(disc.nameWithoutExtension.lowercase(), disc)
                    }
                }
            }
        }
        return map
    }
}
