package com.savesync.android.storage

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [
        SyncStateEntity::class,
        SavePathOverrideEntity::class,
        DownloadEntity::class,
    ],
    // v3 adds the `downloads` table that backs the Downloads tab and the
    // pause / resume manager.  fallbackToDestructiveMigration() below means
    // upgraders lose their sync state once on first launch — acceptable
    // since sync state can be rebuilt from servers on the next sync.
    version = 3,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun syncStateDao(): SyncStateDao
    abstract fun savePathOverrideDao(): SavePathOverrideDao
    abstract fun downloadDao(): DownloadDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getInstance(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "save_sync_db"
                )
                    .fallbackToDestructiveMigration()
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
