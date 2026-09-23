package com.savesync.android.sync

/**
 * Enhanced-audio ROM packs — SNES MSU-1, Mega Drive MSU-MD and MD+.
 *
 * The server catalogs one as a bundle stamped with a `bundle_kind`, served
 * as the very zip the operator dropped on the NAS.  That zip usually wraps a
 * single folder and carries the author's own leftovers (a save, a patch
 * source), so it is not unpacked verbatim: the wrapping folder is hoisted
 * away and the junk dropped, exactly as `shared/msu.py` does for the Python
 * clients.  RetroArch loads the pack as a plain ROM from the folder, so
 * nothing is renamed here.
 *
 * Kotlin port of `plan_extraction` / `strip_common_root` / `is_junk` in
 * `shared/msu.py` — change one and change the other.
 */
object MsuPack {
    const val MSU1 = "msu1"
    const val MSU_MD = "msu-md"
    const val MD_PLUS = "mdplus"

    val KINDS: Set<String> = setOf(MSU1, MSU_MD, MD_PLUS)

    /** What a prompt calls the pack. */
    fun label(kind: String?): String = when (kind?.trim()?.lowercase()) {
        MSU1 -> "MSU-1 pack"
        MSU_MD -> "MSU-MD pack"
        MD_PLUS -> "MD+ pack"
        else -> "bundle"
    }

    private val JUNK_EXTS = setOf("srm", "asm", "txt", "nfo", "ips", "bps", "url")
    private val JUNK_NAMES = setOf("thumbs.db", ".ds_store", "desktop.ini")

    fun isJunk(name: String): Boolean {
        val base = name.substringAfterLast('/').lowercase()
        if (base in JUNK_NAMES || base.startsWith(".")) return true
        return base.substringAfterLast('.', "") in JUNK_EXTS
    }

    /**
     * The single top-level folder every member shares, or `""`.
     * Directory entries (trailing `/`) are ignored.
     */
    fun commonRoot(members: List<String>): String {
        val files = members.map { it.replace('\\', '/').trimStart('/') }
            .filter { it.isNotEmpty() && !it.endsWith("/") }
        if (files.isEmpty()) return ""
        val heads = files.map { it.substringBefore('/') }.toSet()
        if (heads.size != 1 || files.any { '/' !in it }) return ""
        return heads.first()
    }

    /**
     * `(zip member, relative destination)` for every file worth writing.
     * Members that would escape the target are left out.
     */
    fun planExtraction(members: List<String>): List<Pair<String, String>> {
        val files = members.filter { it.isNotEmpty() && !it.endsWith("/") }
        val root = commonRoot(files)
        return files.mapNotNull { member ->
            val normalized = member.replace('\\', '/').trimStart('/')
            val rel = if (root.isNotEmpty()) normalized.substringAfter("$root/") else normalized
            val parts = rel.split('/')
            if (rel.startsWith("/") || ".." in parts || isJunk(rel)) null else member to rel
        }
    }
}
