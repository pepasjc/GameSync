package com.savesync.android.emulators

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Environment
import java.io.File

/**
 * Which Xbox emulator GameSync syncs saves for.
 *
 * Both keep the console's E:\UDATA\<title id> save folders, but in different
 * forms: hakuX unpacks the hard disk into host folders
 * ([com.savesync.android.emulators.impl.HakuXEmulator]), X1 BOX keeps xemu's
 * disk image ([com.savesync.android.emulators.impl.X1BoxEmulator]).  Only one
 * is scanned, so a device with both doesn't list every save twice and a
 * server-only save downloads into the app that will play it.
 *
 * [AUTO] picks whichever is installed (hakuX first); a fixed choice whose app
 * is not installed falls back the same way, so a stale setting left from
 * another device still finds the emulator this one has.
 */
enum class XboxEmulatorChoice(val wireValue: String, val label: String, val packageName: String?) {
    AUTO("auto", "Automatic (installed emulator)", null),
    HAKUX("hakux", "hakuX", HAKUX_PACKAGE),
    X1BOX("x1box", "X1 BOX", X1BOX_PACKAGE);

    /** The emulator to scan: this one when installed, else the installed one, else this one. */
    fun resolve(isInstalled: (String) -> Boolean): XboxEmulatorChoice {
        if (this != AUTO && isInstalled(packageName!!)) return this
        val installed = listOf(HAKUX, X1BOX).firstOrNull { isInstalled(it.packageName!!) }
        return installed ?: if (this == AUTO) HAKUX else this
    }

    fun resolve(context: Context): XboxEmulatorChoice = resolve { isInstalled(context, it) }

    companion object {
        fun fromWireValue(value: String?): XboxEmulatorChoice =
            values().firstOrNull { it.wireValue.equals(value, ignoreCase = true) } ?: AUTO

        /**
         * An app is installed when the package manager knows it (the manifest's
         * <queries> makes both visible) or, failing that, when its
         * Android/data folder exists.
         */
        fun isInstalled(context: Context, packageName: String): Boolean {
            val known = try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    context.packageManager.getPackageInfo(packageName, PackageManager.PackageInfoFlags.of(0))
                } else {
                    @Suppress("DEPRECATION")
                    context.packageManager.getPackageInfo(packageName, 0)
                }
                true
            } catch (_: PackageManager.NameNotFoundException) {
                false
            }
            return known || File(Environment.getExternalStorageDirectory(), "Android/data/$packageName").isDirectory
        }
    }
}

const val HAKUX_PACKAGE = "com.rfandango.haku_x"
const val X1BOX_PACKAGE = "com.izzy2lost.x1box"
