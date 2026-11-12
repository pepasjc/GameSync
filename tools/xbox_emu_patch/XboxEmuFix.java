/*
 * GameSync - helper injected into the hakuX and X1 BOX Xbox emulators by
 * tools/patch_xbox_emu.py, so GameSync can reach their saves.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Both apps run as their own Android user and keep files owner-only (600),
 * where another app can't read them even in Android/data. GameSync is in the
 * ext_data_rw group that owns Android/data, so files there with group
 * read/write (660) are within its reach - the DuckStation patch
 * (tools/patch_duckstation.py) rests on the same fact.
 *
 * hakuX: its Xbox hard disk is a folder tree in private storage
 * (files/native/hdd, E: = Partition1). files/native becomes a link to the
 * same folder in Android/data, files created there come out 660/770, and
 * files GameSync wrote into E:\UDATA are taken over (copied to files hakuX
 * owns) at every start.
 *
 * X1 BOX: its disk image (files/x1box/hdd.img in Android/data) is opened in
 * place, so the files there only need to be 660.
 */
package com.savesync.xpatch;

import android.content.Context;
import android.system.Os;
import android.system.StructStat;
import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.nio.channels.FileLock;

public final class XboxEmuFix {
    private static final String TAG = "GameSyncPatch";
    /* rw-rw----, rwxrwx--- */
    private static final int FILE_MODE = 0660;
    private static final int DIR_MODE = 0770;

    private XboxEmuFix() {}

    // ------------------------------------------------------------------ hakuX

    /** Application.onCreate, in every process: before anything opens the disk. */
    public static void hakux(Context context) {
        try {
            Os.umask(0007);
            File ext = context.getExternalFilesDir(null);
            if (ext == null) return;
            File internal = new File(context.getFilesDir(), "native");
            File external = new File(ext, "native");
            File lockFile = new File(context.getFilesDir(), ".gamesync-patch.lock");
            try (RandomAccessFile raf = new RandomAccessFile(lockFile, "rw");
                 FileLock ignored = raf.getChannel().lock()) {
                if (!isLink(internal)) {
                    mkdirs(external);
                    if (internal.isDirectory()) {
                        // An install that ran unpatched: its disk moves out once.
                        copyTree(internal, external);
                        deleteTree(internal);
                    } else if (internal.exists()) {
                        internal.delete();
                    }
                    Os.symlink(external.getAbsolutePath(), internal.getAbsolutePath());
                    Log.i(TAG, "files/native -> " + external);
                }
                hakuxAdopt(context);
            }
        } catch (Throwable t) {
            Log.e(TAG, "hakuX storage setup failed", t);
        }
    }

    /** The library coming back to the front: a sync may have written saves meanwhile. */
    public static void hakuxAdopt(Context context) {
        try {
            File ext = context.getExternalFilesDir(null);
            if (ext == null) return;
            File e = child(new File(ext, "native/hdd"), "Partition1");
            File udata = e == null ? null : child(e, "UDATA");
            if (udata != null) adopt(udata, Os.getuid());
        } catch (Throwable t) {
            Log.e(TAG, "hakuX adopt failed", t);
        }
    }

    // ----------------------------------------------------------------- X1 BOX

    /** Activity starts and stops: new files 660, the image and its folder opened up. */
    public static void x1box(Context context) {
        try {
            Os.umask(0007);
            File ext = context.getExternalFilesDir(null);
            if (ext == null) return;
            File dir = new File(ext, "x1box");
            if (dir.isDirectory()) widen(dir, Os.getuid());
        } catch (Throwable t) {
            Log.e(TAG, "X1 BOX chmod failed", t);
        }
    }

    // ---------------------------------------------------------------- helpers

    private static boolean isLink(File f) {
        try {
            return (Os.lstat(f.getAbsolutePath()).st_mode & 0170000) == 0120000;
        } catch (Throwable t) {
            return false;
        }
    }

    private static File child(File dir, String name) {
        File[] list = dir.listFiles();
        if (list == null) return null;
        for (File f : list) if (f.getName().equalsIgnoreCase(name)) return f;
        return null;
    }

    private static void mkdirs(File dir) throws Exception {
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("can't make " + dir);
    }

    private static void chmod(File f, int mode) {
        try {
            Os.chmod(f.getAbsolutePath(), mode);
        } catch (Throwable ignored) {
        }
    }

    /** Every file and folder under dir that this app owns: 660 / 770. */
    private static void widen(File dir, int uid) {
        chmodOwned(dir, DIR_MODE, uid);
        File[] list = dir.listFiles();
        if (list == null) return;
        for (File f : list) {
            if (f.isDirectory() && !isLink(f)) widen(f, uid);
            else chmodOwned(f, FILE_MODE, uid);
        }
    }

    private static void chmodOwned(File f, int mode, int uid) {
        try {
            StructStat st = Os.lstat(f.getAbsolutePath());
            if (st.st_uid == uid && (st.st_mode & 07777) != mode) Os.chmod(f.getAbsolutePath(), mode);
        } catch (Throwable ignored) {
        }
    }

    /**
     * Files and folders under dir another app (GameSync) made become this
     * app's own: a file is copied over itself, a folder rebuilt beside
     * itself. Its own ones get 660 / 770.
     */
    private static void adopt(File dir, int uid) throws Exception {
        File[] list = dir.listFiles();
        if (list == null) return;
        for (File f : list) {
            StructStat st = Os.lstat(f.getAbsolutePath());
            boolean isDir = (st.st_mode & 0170000) == 0040000;
            if (isDir) {
                File d = f;
                if (st.st_uid != uid) {
                    File fresh = new File(dir, ".gamesync-" + f.getName());
                    deleteTree(fresh);
                    mkdirs(fresh);
                    File[] inner = f.listFiles();
                    if (inner != null) {
                        for (File c : inner) {
                            if (!c.renameTo(new File(fresh, c.getName()))) {
                                throw new IOException("can't move " + c);
                            }
                        }
                    }
                    if (!f.delete() || !fresh.renameTo(f)) throw new IOException("can't replace " + f);
                    d = f;
                }
                chmod(d, DIR_MODE);
                adopt(d, uid);
            } else if (st.st_uid != uid) {
                File tmp = new File(dir, ".gamesync-" + f.getName());
                copyFile(f, tmp);
                tmp.setLastModified(f.lastModified());
                if (!f.delete() || !tmp.renameTo(f)) throw new IOException("can't replace " + f);
                chmod(f, FILE_MODE);
            } else {
                chmodOwned(f, FILE_MODE, uid);
            }
        }
    }

    private static void copyTree(File from, File to) throws Exception {
        mkdirs(to);
        File[] list = from.listFiles();
        if (list == null) return;
        for (File f : list) {
            File dst = new File(to, f.getName());
            if (f.isDirectory()) {
                copyTree(f, dst);
            } else {
                copyFile(f, dst);
                dst.setLastModified(f.lastModified());
            }
        }
    }

    private static void copyFile(File from, File to) throws IOException {
        try (InputStream in = new FileInputStream(from); OutputStream out = new FileOutputStream(to)) {
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
    }

    private static void deleteTree(File f) {
        if (f.isDirectory() && !isLink(f)) {
            File[] list = f.listFiles();
            if (list != null) for (File c : list) deleteTree(c);
        }
        f.delete();
    }
}
