package com.savesync.android.catalog

import com.google.gson.Gson
import com.google.gson.JsonParseException
import com.savesync.android.api.RomEntry
import java.io.File
import java.io.IOException
import kotlin.coroutines.cancellation.CancellationException

/**
 * The server's ROM catalogue, kept on disk between runs.
 *
 * Kotlin port of `shared/catalog_cache.py` (used by the MiSTer and Steam
 * Deck clients; the console clients carry C ports of the same strategy).
 * The server publishes one fingerprint per system (`GET
 * /api/v1/roms/fingerprints`), so the catalogue is stored per system next to
 * the fingerprint it was fetched under, and on the next open only the systems
 * whose fingerprint moved are fetched again.  There is no TTL: the
 * fingerprint says whether the copy is current, and a server that cannot be
 * reached leaves the last copy usable.
 *
 * File format (one JSON document):
 * ```
 * {"version": 1, "systems": {"SNES": {"fingerprint": "...", "rows": [RomEntry...]}}}
 * ```
 * A file with another [version] is ignored (refetched), so bump [VERSION]
 * whenever the meaning of a stored row changes.
 *
 * Plain Kotlin + Gson — no Android dependencies — so it is unit-tested on
 * the JVM (CatalogCacheTest).
 */
class CatalogCache(
    private val file: File,
    val version: Int = VERSION,
    private val gson: Gson = Gson(),
) {
    /** Result of [plan]: systems whose cached copy is current, and those to refetch. */
    data class Plan(val fresh: List<String>, val stale: List<String>)

    private class StoredSystem(val fingerprint: String?, val rows: List<RomEntry>?)
    private class StoredFile(val version: Int?, val systems: Map<String, StoredSystem>?)

    private val systems = LinkedHashMap<String, StoredSystem>()
    private var dirty = false

    init {
        load()
    }

    /** (Re)read the file; a missing, unreadable or other-version file leaves the cache empty. */
    fun load() {
        systems.clear()
        dirty = false
        if (!file.isFile) return
        val parsed: StoredFile? = try {
            file.bufferedReader(Charsets.UTF_8).use { gson.fromJson(it, StoredFile::class.java) }
        } catch (_: IOException) {
            null
        } catch (_: JsonParseException) {
            null
        } catch (_: RuntimeException) {
            // Gson can surface malformed input as IllegalStateException etc.
            null
        }
        if (parsed == null || parsed.version != version) return
        parsed.systems?.forEach { (system, parsedSystem) ->
            // Typed nullable on purpose: Gson can store a JSON null here.
            val stored: StoredSystem? = parsedSystem
            // Gson bypasses Kotlin's null checks, so a hand-edited or
            // truncated row could carry nulls in non-null fields; keep
            // only rows that have what the UI needs.
            @Suppress("SENSELESS_COMPARISON")
            val rows = stored?.rows.orEmpty().filter { row ->
                row != null && row.system != null && row.title_id != null &&
                    row.name != null && row.filename != null && row.path != null
            }
            systems[system] = StoredSystem(stored?.fingerprint, rows)
        }
    }

    /** Cached system codes, sorted. */
    fun systems(): List<String> = systems.keys.sorted()

    fun fingerprint(system: String): String? = systems[system]?.fingerprint

    fun rows(system: String): List<RomEntry> = systems[system]?.rows.orEmpty()

    /** Every cached row, system by system (sorted), each system in server order. */
    fun allRows(): List<RomEntry> = systems().flatMap { rows(it) }

    /** Total number of cached rows. */
    val rowCount: Int get() = systems.values.sumOf { it.rows?.size ?: 0 }

    fun isEmpty(): Boolean = rowCount == 0

    fun put(system: String, fingerprint: String, rows: List<RomEntry>) {
        systems[system] = StoredSystem(fingerprint, rows.toList())
        dirty = true
    }

    fun drop(system: String) {
        if (systems.remove(system) != null) dirty = true
    }

    fun clear() {
        systems.clear()
        // An explicit clear must reach disk even when already empty in memory.
        dirty = true
    }

    /**
     * Split [wanted] systems into fresh / stale against the server's
     * fingerprints ([server] = system → fingerprint).  Systems the server no
     * longer lists are dropped from the cache.
     */
    fun plan(server: Map<String, String>, wanted: Collection<String> = server.keys.sorted()): Plan {
        val fresh = mutableListOf<String>()
        val stale = mutableListOf<String>()
        for (system in wanted) {
            val fp = server[system]
            if (fp == null) {
                drop(system)
                continue
            }
            if (fingerprint(system) == fp) fresh += system else stale += system
        }
        for (system in systems.keys.toList()) {
            if (system !in server) drop(system)
        }
        return Plan(fresh, stale)
    }

    /**
     * Write the cache if anything changed: to a temp file first, then
     * renamed over the real one, so a crash mid-write never leaves a
     * half-written catalogue behind.  Returns false when the write failed
     * (an unwritable cache is a slow start, never an error).
     */
    fun save(): Boolean {
        if (!dirty) return true
        val payload = StoredFile(version, LinkedHashMap(systems))
        val temp = File(file.path + ".part")
        return try {
            file.absoluteFile.parentFile?.mkdirs()
            temp.bufferedWriter(Charsets.UTF_8).use { gson.toJson(payload, it) }
            if (!temp.renameTo(file)) {
                // renameTo can refuse to replace on some filesystems.
                file.delete()
                if (!temp.renameTo(file)) throw IOException("rename failed")
            }
            dirty = false
            true
        } catch (_: Exception) {
            temp.delete()
            false
        }
    }

    companion object {
        /** Bumped when the meaning of a stored row changes. */
        const val VERSION = 1
        const val FILE_NAME = "catalog_cache.json"
    }
}

/** How [loadCatalog] produced its rows. */
enum class CatalogSource {
    /** Fingerprints answered; stale systems refetched, the rest from disk. */
    CACHE,
    /** Server too old for fingerprints: fetched whole, nothing cached. */
    LIVE,
    /** Server unreachable: the last cached copy. */
    OFFLINE,
}

data class CatalogLoadResult(
    val rows: List<RomEntry>,
    val source: CatalogSource,
    val refreshed: Int = 0,
    val unchanged: Int = 0,
)

/**
 * The MiSTer client's `load_catalog()` strategy, minus the UI.
 *
 * - [fetchFingerprints] returns system → fingerprint, `null` for a server
 *   that predates the endpoint (404/405), and throws when unreachable.
 * - [fetchSystem] returns every row of one system (following pagination);
 *   `null` means the whole catalogue (only used for a pre-fingerprint server).
 *
 * Unreachable + non-empty cache → [CatalogSource.OFFLINE] with the cached
 * rows; unreachable + empty cache → the error propagates.
 */
suspend fun loadCatalog(
    cache: CatalogCache,
    fetchFingerprints: suspend () -> Map<String, String>?,
    fetchSystem: suspend (system: String?) -> List<RomEntry>,
): CatalogLoadResult {
    val server = try {
        fetchFingerprints()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        if (!cache.isEmpty()) {
            return CatalogLoadResult(cache.allRows(), CatalogSource.OFFLINE)
        }
        throw e
    }
    if (server == null) {
        return CatalogLoadResult(fetchSystem(null), CatalogSource.LIVE)
    }
    val plan = cache.plan(server)
    for (system in plan.stale) {
        val rows = fetchSystem(system).filter { it.system.equals(system, ignoreCase = true) }
        cache.put(system, server[system].orEmpty(), rows)
    }
    cache.save()
    return CatalogLoadResult(
        cache.allRows(),
        CatalogSource.CACHE,
        refreshed = plan.stale.size,
        unchanged = plan.fresh.size,
    )
}
