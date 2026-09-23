package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

/**
 * Cemu (Wii U) save scanner.
 *
 * Cemu mirrors the console's own layout, so a save lives at
 * ``<mlc01>/usr/save/00050000/<tidlo>/user/<persistentId>/…`` — the exact tree
 * the Wii U homebrew client walks on NAND.  Keeping the same shape means both
 * sides produce identical bundles:
 *
 *  * title id  = the native 16-hex id, uppercase (``00050000`` + ``tidlo``)
 *  * members   = paths relative to ``user/``
 *  * save hash = concatenated file contents sorted by relative path
 *                ([com.savesync.android.sync.HashUtils.sha256DirTreeFiles]),
 *                which is what the server computes for a multi-file bundle.
 *
 * Names come from the title's own ``meta.xml`` (Cemu keeps installed content
 * under the same mlc01 tree the console uses).  The server cannot name a Wii U
 * save from its title id — the low word is not the product code — so the
 * scanner also lifts ``<product_code>`` and reports it as ``WIIU_<code>``,
 * which the server's Wii U DAT *is* keyed by.
 */
class CemuEmulator(
    /** EmuDeck-style Cemu root, if the user configured an EmuDeck folder. */
    private val storageBaseDir: File? = null,
    /**
     * Optional explicit folder override from the Emulator Configuration
     * screen.  Accepts either the ``mlc01`` directory itself or its parent
     * (the Cemu install folder), since users reasonably point at both.
     */
    private val saveDirOverride: String? = null,
    /**
     * Where the user keeps Wii U dumps.  Most Cemu libraries are *loose*
     * folders (``<Game>/code``, ``content``, ``meta``) that were never
     * installed to the MLC, so their meta.xml — the only place a Wii U title
     * id maps to a name — lives here and nowhere else.
     */
    private val romScanDir: String = "",
    private val emudeckDir: String = ""
) : EmulatorBase() {

    override val name: String = "Cemu"
    override val systemPrefix: String = "WIIU"

    override fun discoverSaves(): List<SaveEntry> {
        val mlc = resolveMlcRoot() ?: return emptyList()
        return discoverSaves(mlc, gameDirs())
    }

    private fun gameDirs(): List<File> = gameDirCandidates(
        storageBaseDir = storageBaseDir,
        romScanDir = romScanDir,
        emudeckDir = emudeckDir,
        externalRoot = baseDir
    )

    private fun resolveMlcRoot(): File? = resolveMlcRoot(
        storageBaseDir = storageBaseDir,
        saveDirOverride = saveDirOverride,
        externalRoot = baseDir
    )

    /** meta.xml fields we care about; either may be absent. */
    data class TitleMeta(val name: String?, val gameCode: String?)

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "Cemu"

        /** Wii U application titles; updates/DLC share the low word. */
        private const val TITLE_HIGH = "00050000"

        private val HEX8_REGEX = Regex("^[0-9A-Fa-f]{8}$")
        private val TITLE_ID_REGEX = Regex("^0005[0-9A-Fa-f]{12}$")

        /**
         * Content roots searched for meta.xml, in the order the Wii U client
         * uses: the application itself, then its update — a disc game has no
         * 00050000 content on disk, so the update's meta.xml is what names it
         * — then DLC, then pre-installed system apps under ``sys``.
         */
        private val META_HIGH_IDS = listOf("00050000", "0005000E", "0005000C")

        private val LONGNAME_EN_REGEX =
            Regex("<longname_en[^>]*>(.*?)</longname_en>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
        private val LONGNAME_ANY_REGEX =
            Regex("<longname_[a-z]+[^>]*>(.*?)</longname_", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
        private val PRODUCT_CODE_REGEX =
            Regex("<product_code[^>]*>(.*?)</product_code>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
        private val TITLE_ID_META_REGEX =
            Regex("<title_id[^>]*>(.*?)</title_id>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))

        /**
         * meta.xml routinely runs past 8 KB and ``<longname_en>`` sits after
         * the header fields, so a short read silently loses the name for some
         * titles.  Read the whole thing (capped).
         */
        private const val META_XML_MAX = 128 * 1024

        /**
         * Candidate mlc01 locations, relative to external storage.  Cemu's
         * Android build keeps its MLC in the app's external files dir; users
         * who imported an existing PC install usually drop it at the storage
         * root instead.
         */
        private fun candidateRelativePaths(): List<String> = listOf(
            "Android/data/info.cemu.Cemu/files/mlc01",
            "Cemu/mlc01",
            "cemu/mlc01",
            "Emulation/storage/cemu/mlc01",
            "mlc01"
        )

        /**
         * Resolve Cemu's ``mlc01``.  Override wins, then the EmuDeck folder,
         * then the well-known on-device locations.
         *
         * An override may name either ``mlc01`` or its parent — accepting both
         * avoids a silent empty scan when the user picks the Cemu folder in
         * the directory picker.
         */
        fun resolveMlcRoot(
            storageBaseDir: File?,
            saveDirOverride: String?,
            externalRoot: File
        ): File? {
            if (!saveDirOverride.isNullOrBlank()) {
                val dir = File(saveDirOverride)
                val resolved = normalizeMlcCandidate(dir)
                if (resolved != null) return resolved
            }

            storageBaseDir?.let { root ->
                normalizeMlcCandidate(File(root, "mlc01"))?.let { return it }
                normalizeMlcCandidate(root)?.let { return it }
            }

            return candidateRelativePaths()
                .asSequence()
                .map { File(externalRoot, it) }
                .mapNotNull { normalizeMlcCandidate(it) }
                .firstOrNull()
        }

        /**
         * Accept [dir] as an mlc01 root if it holds ``usr/save``; otherwise try
         * its ``mlc01`` child.  Returns null when neither looks like an MLC.
         */
        private fun normalizeMlcCandidate(dir: File): File? {
            if (!dir.isDirectory) return null
            if (File(File(dir, "usr"), "save").isDirectory) return dir
            val nested = File(dir, "mlc01")
            if (File(File(nested, "usr"), "save").isDirectory) return nested
            return null
        }

        /** Where a Wii U save for [titleId] belongs under Cemu's mlc01. */
        fun defaultSaveDir(
            storageBaseDir: File?,
            saveDirOverride: String?,
            externalRoot: File,
            titleId: String
        ): File? {
            val upper = titleId.uppercase()
            if (!TITLE_ID_REGEX.matches(upper)) return null
            val mlc = resolveMlcRoot(storageBaseDir, saveDirOverride, externalRoot)
                ?: return null
            return File(
                File(
                    File(File(File(mlc, "usr"), "save"), upper.substring(0, 8)),
                    upper.substring(8).lowercase()
                ),
                "user"
            )
        }

        /**
         * Enumerate every Wii U save under [mlcRoot].  Split out from the
         * instance method so it can be exercised without Android's
         * Environment, which the path resolution needs.
         */
        fun discoverSaves(mlcRoot: File, gameDirs: List<File> = emptyList()): List<SaveEntry> {
            val saveRoot = File(File(File(mlcRoot, "usr"), "save"), TITLE_HIGH)
            if (!saveRoot.isDirectory) return emptyList()

            val results = mutableListOf<SaveEntry>()
            val metaIndex = buildMetaIndex(mlcRoot, gameDirs)

            saveRoot.listFiles()
                ?.filter { it.isDirectory && HEX8_REGEX.matches(it.name) }
                ?.sortedBy { it.name }
                ?.forEach { titleDir ->
                    val saveDir = File(titleDir, "user")
                    if (!saveDir.isDirectory) return@forEach
                    if (saveDir.walkTopDown().none { it.isFile }) return@forEach

                    val titleId = (TITLE_HIGH + titleDir.name).uppercase()
                    val meta = metaIndex[titleId]

                    results.add(
                        SaveEntry(
                            titleId = titleId,
                            displayName = meta?.name ?: titleId,
                            systemName = "WIIU",
                            saveFile = null,
                            saveDir = saveDir,
                            isMultiFile = true,
                            gameCode = meta?.gameCode
                        )
                    )
                }

            return results
        }

        /** Candidate roots holding loose Wii U game folders. */
        fun gameDirCandidates(
            storageBaseDir: File?,
            romScanDir: String,
            emudeckDir: String,
            externalRoot: File
        ): List<File> {
            val dirs = mutableListOf<File>()
            if (romScanDir.isNotBlank()) {
                val root = File(romScanDir)
                listOf("wiiu", "WiiU", "Wii U", "WIIU").forEach { dirs.add(File(root, it)) }
                dirs.add(root)
            }
            if (emudeckDir.isNotBlank()) {
                dirs.add(File(File(emudeckDir, "roms"), "wiiu"))
            }
            storageBaseDir?.let { dirs.add(File(it, "games")) }
            listOf("wiiu", "WiiU", "Cemu/games", "Emulation/roms/wiiu").forEach {
                dirs.add(File(externalRoot, it))
            }
            return dirs.distinct().filter { it.isDirectory }
        }

        /**
         * Map ``title_id -> meta`` from every meta.xml on this device.
         *
         * Installed MLC content first, then loose game folders — most Cemu
         * libraries are loose dumps that never touch the MLC, which is why an
         * MLC-only lookup left every one of those saves showing raw hex.
         */
        fun buildMetaIndex(mlcRoot: File?, gameDirs: List<File>): Map<String, TitleMeta> {
            val index = mutableMapOf<String, TitleMeta>()

            fun add(text: String, fallbackTitleId: String? = null) {
                val (parsedId, parsedMeta) = parseMetaXml(text)
                val titleId = parsedId ?: fallbackTitleId ?: return
                val prev = index[titleId]
                val merged = TitleMeta(
                    name = parsedMeta?.name ?: prev?.name,
                    gameCode = parsedMeta?.gameCode ?: prev?.gameCode
                )
                if (merged.name != null || merged.gameCode != null) index[titleId] = merged
            }

            if (mlcRoot != null) {
                for (base in listOf("usr", "sys")) {
                    for (high in META_HIGH_IDS) {
                        val highDir = File(File(File(mlcRoot, base), "title"), high)
                        highDir.listFiles()
                            ?.filter { it.isDirectory && HEX8_REGEX.matches(it.name) }
                            ?.forEach { titleDir ->
                                readMetaFile(File(titleDir, "meta/meta.xml"))?.let {
                                    add(it, (TITLE_HIGH + titleDir.name).uppercase())
                                }
                            }
                    }
                }
            }

            // <root>/<Game>/meta/meta.xml, plus one extra level for the
            // "<root>/<Game>/<Game>/meta" nesting some dump tools produce.
            for (root in gameDirs) {
                readMetaFile(File(root, "meta/meta.xml"))?.let { add(it) }
                root.listFiles()?.filter { it.isDirectory }?.forEach { child ->
                    readMetaFile(File(child, "meta/meta.xml"))?.let { add(it) }
                    child.listFiles()?.filter { it.isDirectory }?.forEach { grandchild ->
                        readMetaFile(File(grandchild, "meta/meta.xml"))?.let { add(it) }
                    }
                }
            }

            return index
        }

        /**
         * Parse a meta.xml body into ``(title_id, meta)``.
         *
         * The title id is folded onto the application id: an update
         * (0005000E) or DLC (0005000C) meta.xml carries its own high word but
         * names the base game, whose save lives under the 00050000 id.
         */
        fun parseMetaXml(text: String): Pair<String?, TitleMeta?> {
            val titleId = TITLE_ID_META_REGEX.find(text)
                ?.groupValues?.getOrNull(1)
                ?.let { cleanName(it).replace("-", "").uppercase() }
                ?.takeIf { it.length == 16 && it.all { ch -> ch in "0123456789ABCDEF" } }
                ?.let { TITLE_HIGH + it.substring(8) }

            val name = (LONGNAME_EN_REGEX.find(text) ?: LONGNAME_ANY_REGEX.find(text))
                ?.groupValues?.getOrNull(1)
                ?.let { cleanName(it) }
                ?.takeIf { it.isNotBlank() }

            // WUP-P-ARDE -> WUPPARDE; the DAT is keyed by the trailing 4 chars.
            val gameCode = PRODUCT_CODE_REGEX.find(text)
                ?.groupValues?.getOrNull(1)
                ?.let { cleanName(it).uppercase().replace("-", "") }
                ?.takeIf { it.length >= 4 && it.takeLast(4).all { ch -> ch.isLetterOrDigit() } }
                ?.let { "WIIU_${it.takeLast(4)}" }

            val meta = if (name == null && gameCode == null) null else TitleMeta(name, gameCode)
            return titleId to meta
        }

        private fun readMetaFile(meta: File): String? {
            if (!meta.isFile) return null
            val text = try {
                meta.inputStream().use { stream ->
                    val buffer = ByteArray(META_XML_MAX)
                    var read = 0
                    while (read < buffer.size) {
                        val n = stream.read(buffer, read, buffer.size - read)
                        if (n <= 0) break
                        read += n
                    }
                    String(buffer, 0, read, Charsets.UTF_8)
                }
            } catch (_: Exception) {
                return null
            }
            if (!text.contains("<longname") && !text.contains("<product_code")) return null
            return text
        }

        /**
         * meta.xml is XML, so names arrive escaped and often span lines —
         * without this the user reads "Dracula&apos;s Curse".
         */
        private fun cleanName(raw: String): String {
            val unescaped = raw
                .replace("&apos;", "'")
                .replace("&quot;", "\"")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&amp;", "&")
            return unescaped.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
        }
    }
}
