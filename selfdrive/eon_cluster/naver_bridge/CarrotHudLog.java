package com.naver.map.carrot;

import android.content.Context;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * HUD8.1: append-only log file for the bridge, readable with any file manager
 * (no adb): {@code /sdcard/Android/data/com.nhn.android.nmap/files/carrot_hud.log}.
 * Mirrors every line to logcat. Truncates itself at 512 KB.
 */
final class CarrotHudLog {
    private static final long MAX_BYTES = 512 * 1024;
    private static final Object lock = new Object();
    private static File file;
    private static boolean resolved;
    private static SimpleDateFormat format;

    private CarrotHudLog() {
    }

    static void log(String tag, String message) {
        Log.i(tag, message);
        synchronized (lock) {
            try {
                File f = target();
                if (f == null) {
                    return;
                }
                if (f.length() > MAX_BYTES) {
                    //noinspection ResultOfMethodCallIgnored
                    f.delete();
                }
                if (format == null) {
                    format = new SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US);
                }
                Writer w = new OutputStreamWriter(new FileOutputStream(f, true), "UTF-8");
                try {
                    w.write(format.format(new Date()) + " " + tag + ": " + message + "\n");
                } finally {
                    w.close();
                }
            } catch (Throwable ignored) {
            }
        }
    }

    static File target() {
        if (resolved) {
            return file;
        }
        resolved = true;
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            Object app = at.getMethod("currentApplication").invoke(null);
            if (app instanceof Context) {
                Context ctx = (Context) app;
                File dir = ctx.getExternalFilesDir(null);
                if (dir == null) {
                    dir = ctx.getFilesDir();
                }
                if (dir != null) {
                    //noinspection ResultOfMethodCallIgnored
                    dir.mkdirs();
                    file = new File(dir, "carrot_hud.log");
                }
            }
        } catch (Throwable ignored) {
        }
        return file;
    }
}
