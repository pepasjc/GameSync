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

    /**
     * Fold gamecode-form rows (``GC_grse``) onto their canonical uppercase
     * form (``GC_GRSE``).
     *
     * Builds before the GC title-id canonicalisation stored lowercase ids.
     * Without this, every GameCube game loses its lastSyncedHash on upgrade
     * and the next sync reports a spurious conflict.  ``UPDATE OR REPLACE``
     * handles the case where both casings already have a row — the lowercase
     * one is the newer scan, so it wins.
     */
    @Query(
        """
        UPDATE OR REPLACE sync_state
        SET titleId = UPPER(titleId)
        WHERE titleId LIKE 'GC\_%' ESCAPE '\'
          AND titleId <> UPPER(titleId)
        """
    )
    suspend fun canonicalizeGamecodeTitleIds(): Int
}
