package com.savesync.android.storage

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
interface DownloadDao {

    /**
     * Oldest first, i.e. queue order.  Never order by ``updatedAt``: every
     * progress write bumps it, so parallel downloads swapped places on
     * each tick.  The Downloads screen groups and orders rows itself.
     */
    @Query("SELECT * FROM downloads ORDER BY createdAt ASC")
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
