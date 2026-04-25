package com.savesync.android.storage

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
interface DownloadDao {

    /** Most-recent download first — drives the Downloads screen list order. */
    @Query("SELECT * FROM downloads ORDER BY updatedAt DESC")
    fun observeAll(): Flow<List<DownloadEntity>>

    @Query("SELECT * FROM downloads WHERE id = :id LIMIT 1")
    suspend fun getById(id: String): DownloadEntity?

    @Query("SELECT * FROM downloads WHERE status IN ('queued', 'downloading', 'paused')")
    suspend fun getActive(): List<DownloadEntity>

    @Upsert
    suspend fun upsert(entity: DownloadEntity)

    @Query("DELETE FROM downloads WHERE id = :id")
    suspend fun deleteById(id: String)

    /** Used by the "Clear finished" button on the Downloads tab. */
    @Query("DELETE FROM downloads WHERE status IN ('completed', 'failed', 'cancelled')")
    suspend fun deleteFinished()
}
