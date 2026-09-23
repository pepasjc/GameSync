package com.savesync.android.sync

/**
 * Which Sega CD / Mega CD core's backup-RAM layout GameSync should sync.
 *
 * RetroArch's two Sega CD cores disagree on the save filename:
 *   * Genesis Plus GX writes the internal BRAM to ``<content>.brm``
 *   * PicoDrive exposes it as libretro SRAM, so RetroArch writes ``<content>.srm``
 *
 * Both files are the same raw backup-RAM image, so no byte conversion is
 * needed — only the extension we predict, track and download to differs.
 * Mirrors [SaturnSyncFormat], which solves the same problem for Saturn.
 */
enum class SegaCdSyncFormat(
    val wireValue: String,
    val label: String,
    /** Save-file extension this core uses, lowercase and without the dot. */
    val extension: String,
) {
    GENESIS_PLUS_GX("genesis_plus_gx", "Genesis Plus GX (.brm)", "brm"),
    PICODRIVE("picodrive", "PicoDrive (.srm)", "srm");

    companion object {
        fun fromWireValue(value: String?): SegaCdSyncFormat =
            values().firstOrNull { it.wireValue.equals(value, ignoreCase = true) }
                ?: GENESIS_PLUS_GX
    }
}
