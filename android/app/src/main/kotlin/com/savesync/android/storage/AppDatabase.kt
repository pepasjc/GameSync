package com.savesync.android.storage

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

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
    // v4 adds `downloads.bundleKind` (MSU packs) with a real migration so
    // that bump costs nobody their sync state.
    version = 4,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun syncStateDao(): SyncStateDao
    abstract fun savePathOverrideDao(): SavePathOverrideDao
    abstract fun downloadDao(): DownloadDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        private val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE downloads ADD COLUMN bundleKind TEXT")
            }
        }

        fun getInstance(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "save_sync_db"
                )
                    .addMigrations(MIGRATION_3_4)
                    .fallbackToDestructiveMigration()
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
