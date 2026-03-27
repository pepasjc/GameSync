package com.savesync.android.storage

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Stores a user-chosen system override for a specific save file path.
 * When the auto-detection returns "RETRO" (unknown), the user can pick the real
 * system (e.g. GBA) from the detail screen. This override is applied on every
 * subsequent scan so the correct title ID is used for sync.
 */
@Entity(tableName = "save_path_overrides")
data class SavePathOverrideEntity(
    @PrimaryKey val filePath: String,  // absolute path to the save file or directory
    val system: String                 // e.g. "GBA", "SNES", "SAT"
)
