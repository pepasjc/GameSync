package com.savesync.android.emulators

/**
 * Which Dreamcast emulator a server save downloads to when the game has no
 * save on this device yet.  Scanning always reads both — a per-game VMU
 * from standalone Flycast or from RetroArch's Flycast core — so existing
 * saves show up either way; this only picks where a new one lands (see
 * [com.savesync.android.emulators.impl.FlycastEmulator] and [FlycastVmu]).
 */
enum class DcEmulatorChoice(val wireValue: String, val label: String) {
    RETROARCH("retroarch", "RetroArch (Flycast core)"),
    FLYCAST("flycast", "Flycast (standalone)");

    companion object {
        fun fromWireValue(value: String?): DcEmulatorChoice =
            values().firstOrNull { it.wireValue.equals(value, ignoreCase = true) }
                ?: RETROARCH
    }
}
