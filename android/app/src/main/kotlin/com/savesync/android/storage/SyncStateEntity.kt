package com.savesync.android.storage

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "sync_state")
data class SyncStateEntity(
    @PrimaryKey val titleId: String,
    val lastSyncedHash: String?,
    val lastSyncedAt: Long,
    val displayName: String,
    val systemName: String
)
