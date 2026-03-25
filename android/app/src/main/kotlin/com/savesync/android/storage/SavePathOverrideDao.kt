package com.savesync.android.storage

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert

@Dao
interface SavePathOverrideDao {

    @Query("SELECT * FROM save_path_overrides")
    suspend fun getAll(): List<SavePathOverrideEntity>

    @Query("SELECT * FROM save_path_overrides WHERE filePath = :filePath")
    suspend fun getByPath(filePath: String): SavePathOverrideEntity?

    @Upsert
    suspend fun upsert(entity: SavePathOverrideEntity)

    @Query("DELETE FROM save_path_overrides WHERE filePath = :filePath")
    suspend fun deleteByPath(filePath: String)
}
