package com.savesync.android.storage

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
interface SyncStateDao {

    @Query("SELECT * FROM sync_state ORDER BY lastSyncedAt DESC")
    fun getAll(): Flow<List<SyncStateEntity>>

    @Query("SELECT * FROM sync_state WHERE titleId = :titleId LIMIT 1")
    suspend fun getById(titleId: String): SyncStateEntity?

    @Upsert
    suspend fun upsert(entity: SyncStateEntity)

    @Query("DELETE FROM sync_state WHERE titleId = :titleId")
    suspend fun deleteById(titleId: String)
}
