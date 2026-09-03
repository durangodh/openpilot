package ai.comma.remotehud;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.os.StatFs;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** Asynchronous reader for the optional external hud_map.sqlite file. */
final class HudMapStore {
    private static final String TAG = "HudMapStore";
    private static final int ZOOM = 16;
    private static final int MAX_BUILDINGS = 400;
    private static final int MAX_ROADS = 120;
    private static final int MAX_GREEN_AREAS = 80;
    private static final int MAX_WATER_AREAS = 80;
    private static final double RELOAD_METERS = 60.0;
    private static final String LEGACY_DATABASE_DOWNLOAD_URL =
            "https://github.com/durangodh/openpilot/releases/download/"
                    + "hud-map-v1/hud_map.sqlite";
    private static final String LEGACY_DATABASE_SHA256 =
            "c60ec8ae516acd163cfcbc6f3179079d7fd479f305ddf69c466124ed8b279d51";
    private static final long LEGACY_DATABASE_BYTES = 175_321_088L;
    private static final String REGIONAL_RELEASE_BASE =
            "https://github.com/durangodh/openpilot/releases/download/"
                    + "hud-map-gyeonggi-v1/";
    private static final String REGIONAL_MANIFEST_URL =
            REGIONAL_RELEASE_BASE + "manifest.json";
    private static final String REGIONAL_MANIFEST_FORMAT =
            "remote-hud-region-manifest-v1";
    private static final String DOWNLOAD_SUFFIX = ".download";
    private static final long DOWNLOAD_RETRY_MS = 60_000L;
    /** Retry much later when the storage is full: a 60 s loop only heats the phone. */
    private static final long SPACE_RETRY_MS = 30L * 60L * 1000L;
    private static final long MANIFEST_RETRY_MS = 6L * 60L * 60L * 1000L;
    private static final int MAX_MANIFEST_CHARACTERS = 256 * 1024;
    /** Free space that must remain after a download completes. */
    private static final long SPACE_MARGIN_BYTES = 256L * 1024L * 1024L;
    /** Complete regional databases kept on disk: the active one plus this many recent. */
    private static final int MAX_KEPT_REGIONS = 2;
    /** Partial downloads older than this are not worth resuming. */
    private static final long STALE_PARTIAL_MS = 7L * 24L * 60L * 60L * 1000L;

    /** Raised when the download cannot proceed because the storage is (nearly) full. */
    private static final class InsufficientSpaceException extends IOException {
        InsufficientSpaceException(String message) {
            super(message);
        }
    }

    /** Raised when the downloaded bytes are unusable and the partial file must be dropped. */
    private static final class CorruptDownloadException extends IOException {
        CorruptDownloadException(String message) {
            super(message);
        }
    }

    static final class Building {
        final double[] lat;
        final double[] lon;
        final float height;
        final double distanceSq;

        Building(double[] lat, double[] lon, float height, double distanceSq) {
            this.lat = lat;
            this.lon = lon;
            this.height = height;
            this.distanceSq = distanceSq;
        }
    }

    static final class Road {
        final double[] lat;
        final double[] lon;
        final float width;
        final double distanceSq;

        Road(double[] lat, double[] lon, float width, double distanceSq) {
            this.lat = lat;
            this.lon = lon;
            this.width = width;
            this.distanceSq = distanceSq;
        }
    }

    static final class Area {
        final double[] lat;
        final double[] lon;
        final double distanceSq;

        Area(double[] lat, double[] lon, double distanceSq) {
            this.lat = lat;
            this.lon = lon;
            this.distanceSq = distanceSq;
        }
    }

    static final class Snapshot {
        static final Snapshot EMPTY = new Snapshot(new Building[0], new Road[0],
                new Area[0], new Area[0]);
        final Building[] buildings;
        final Road[] roads;
        final Area[] greens;
        final Area[] waters;

        Snapshot(Building[] buildings, Road[] roads, Area[] greens, Area[] waters) {
            this.buildings = buildings;
            this.roads = roads;
            this.greens = greens;
            this.waters = waters;
        }
    }

    private static final class RegionSpec {
        final String id;
        final String fileName;
        final String sha256;
        final long bytes;
        final double south;
        final double west;
        final double north;
        final double east;

        RegionSpec(String id, String fileName, String sha256, long bytes,
                   double south, double west, double north, double east) {
            this.id = id;
            this.fileName = fileName;
            this.sha256 = sha256;
            this.bytes = bytes;
            this.south = south;
            this.west = west;
            this.north = north;
            this.east = east;
        }

        boolean contains(double lat, double lon) {
            return lat >= south && lat <= north && lon >= west && lon <= east;
        }
    }

    private final File databaseDirectory;
    private final File legacyDatabaseFile;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean loading = new AtomicBoolean(false);
    private final AtomicBoolean downloading = new AtomicBoolean(false);
    private final AtomicBoolean loadingManifest = new AtomicBoolean(false);
    private final AtomicBoolean verifying = new AtomicBoolean(false);
    /** "path:length" of files whose SQLite content was checked once (either result). */
    private final Set<String> verifiedFiles =
            Collections.synchronizedSet(new HashSet<String>());
    private final Set<String> rejectedFiles =
            Collections.synchronizedSet(new HashSet<String>());
    private volatile Snapshot snapshot = Snapshot.EMPTY;
    private volatile File activeDatabaseFile;
    private volatile List<RegionSpec> regions = Collections.emptyList();
    private volatile String selectedRegionId;
    private volatile boolean closed;
    private volatile long nextDownloadAtMs;
    private volatile long nextManifestAtMs;
    private volatile double loadedLat = Double.NaN;
    private volatile double loadedLon = Double.NaN;
    private volatile int loadedTileX = Integer.MIN_VALUE;
    private volatile int loadedTileY = Integer.MIN_VALUE;

    HudMapStore(Context context) {
        File directory = context.getExternalFilesDir(null);
        databaseDirectory = directory == null ? context.getFilesDir() : directory;
        legacyDatabaseFile = new File(databaseDirectory, "hud_map.sqlite");
        // Fix 4: a hand-built database whose size differs from the release asset is
        // still accepted once its SQLite content validates (done off-thread).
        acceptDatabase(legacyDatabaseFile, LEGACY_DATABASE_BYTES, null);
        requestManifest();
    }

    Snapshot snapshot() {
        return snapshot;
    }

    void update(double lat, double lon) {
        if (closed || !validPosition(lat, lon)) {
            return;
        }
        List<RegionSpec> availableRegions = regions;
        if (availableRegions.isEmpty()) {
            requestManifest();
        } else {
            RegionSpec region = findRegion(availableRegions, lat, lon);
            selectedRegionId = region == null ? null : region.id;
            if (region != null) {
                File regionalFile = new File(databaseDirectory, region.fileName);
                if (!acceptDatabase(regionalFile, region.bytes, region)) {
                    requestRegionDownload(region);
                }
            }
        }

        File mapFile = activeDatabaseFile;
        if (mapFile == null || !mapFile.isFile()) {
            if (availableRegions.isEmpty()) requestLegacyDownload();
            return;
        }
        int tileX = tileX(lon);
        int tileY = tileY(lat);
        boolean tileChanged = tileX != loadedTileX || tileY != loadedTileY;
        boolean moved = !Double.isFinite(loadedLat)
                || distanceSqMeters(lat, lon, loadedLat, loadedLon) >= RELOAD_METERS * RELOAD_METERS;
        if ((!tileChanged && !moved) || !loading.compareAndSet(false, true)) {
            return;
        }
        executor.execute(() -> {
            try {
                Snapshot loaded = load(mapFile, lat, lon, tileX, tileY);
                if (!closed && mapFile.equals(activeDatabaseFile)) {
                    snapshot = loaded;
                    loadedLat = lat;
                    loadedLon = lon;
                    loadedTileX = tileX;
                    loadedTileY = tileY;
                }
            } catch (Throwable error) {
                Log.w(TAG, "Local HUD map load failed", error);
            } finally {
                loading.set(false);
            }
        });
    }

    /**
     * Fix 4. Returns true when {@code file} is usable (already active or activated now,
     * or a validation is pending), false when it is missing/rejected and must be
     * downloaded. Exact-size files are accepted immediately; other sizes are checked
     * once as SQLite on the worker thread so a hand-copied database still counts.
     */
    private boolean acceptDatabase(File file, long expectedBytes, RegionSpec region) {
        if (!file.isFile()) return false;
        long length = file.length();
        if (length == expectedBytes) {
            activate(file, region);
            return true;
        }
        String key = file.getAbsolutePath() + ":" + length;
        if (rejectedFiles.contains(key)) return false;
        if (verifiedFiles.contains(key)) {
            activate(file, region);
            return true;
        }
        if (length <= 0L || closed || !verifying.compareAndSet(false, true)) {
            // Pending: report usable so no download starts while the check runs.
            return length > 0L;
        }
        try {
            executor.execute(() -> {
                try {
                    validateDatabase(file);
                    verifiedFiles.add(key);
                    Log.i(TAG, "Accepted HUD map with non-release size: " + file.getName()
                            + " (" + length + " bytes)");
                    if (!closed) activate(file, region);
                } catch (Throwable error) {
                    rejectedFiles.add(key);
                    Log.w(TAG, "Ignoring unusable HUD map: " + file.getName(), error);
                } finally {
                    verifying.set(false);
                }
            });
        } catch (RuntimeException rejected) {
            verifying.set(false);
        }
        return true;
    }

    private void activate(File file, RegionSpec region) {
        if (region == null) {
            if (regions.isEmpty()) setActiveDatabase(file);
        } else if (region.id.equals(selectedRegionId)) {
            setActiveDatabase(file);
        }
    }

    private void requestManifest() {
        if (closed || !regions.isEmpty()
                || System.currentTimeMillis() < nextManifestAtMs
                || !loadingManifest.compareAndSet(false, true)) {
            return;
        }
        try {
            executor.execute(() -> {
                try {
                    regions = downloadManifest();
                    Log.i(TAG, "Regional HUD map manifest loaded");
                } catch (Throwable error) {
                    nextManifestAtMs = System.currentTimeMillis() + MANIFEST_RETRY_MS;
                    Log.i(TAG, "Regional HUD map is not published yet; using legacy map");
                    requestLegacyDownload();
                } finally {
                    loadingManifest.set(false);
                }
            });
        } catch (RuntimeException rejected) {
            loadingManifest.set(false);
            if (!closed) nextManifestAtMs = System.currentTimeMillis() + MANIFEST_RETRY_MS;
        }
    }

    private List<RegionSpec> downloadManifest() throws Exception {
        HttpURLConnection connection = null;
        try {
            connection = openConnection(REGIONAL_MANIFEST_URL);
            int response = connection.getResponseCode();
            if (response != HttpURLConnection.HTTP_OK) {
                throw new IllegalStateException("Map manifest HTTP " + response);
            }
            StringBuilder text = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(connection.getInputStream(), "UTF-8"))) {
                char[] buffer = new char[8192];
                int count;
                while ((count = reader.read(buffer)) >= 0) {
                    text.append(buffer, 0, count);
                    if (text.length() > MAX_MANIFEST_CHARACTERS) {
                        throw new IllegalStateException("Map manifest is too large");
                    }
                }
            }
            JSONObject manifest = new JSONObject(text.toString());
            if (!REGIONAL_MANIFEST_FORMAT.equals(manifest.optString("format"))) {
                throw new IllegalStateException("Map manifest format is incompatible");
            }
            JSONArray values = manifest.optJSONArray("regions");
            if (values == null || values.length() != 4) {
                throw new IllegalStateException("Map manifest must contain four regions");
            }
            List<RegionSpec> result = new ArrayList<>();
            Set<String> ids = new HashSet<>();
            for (int i = 0; i < values.length(); i++) {
                JSONObject value = values.optJSONObject(i);
                JSONObject bounds = value == null ? null : value.optJSONObject("selection");
                if (value == null || bounds == null) {
                    throw new IllegalStateException("Map region is incomplete");
                }
                String id = value.optString("id");
                String fileName = value.optString("name");
                String sha256 = value.optString("sha256").toLowerCase();
                long bytes = value.optLong("bytes", -1L);
                double south = bounds.optDouble("south", Double.NaN);
                double west = bounds.optDouble("west", Double.NaN);
                double north = bounds.optDouble("north", Double.NaN);
                double east = bounds.optDouble("east", Double.NaN);
                if (!(id.equals("south") || id.equals("north")
                        || id.equals("west") || id.equals("east"))
                        || !ids.add(id)
                        || !fileName.matches("hud_map_gyeonggi_[a-z]+\\.sqlite")
                        || !sha256.matches("[0-9a-f]{64}") || bytes <= 0L
                        || !validPosition(south, west) || !validPosition(north, east)
                        || south >= north || west >= east) {
                    throw new IllegalStateException("Map region is invalid: " + id);
                }
                result.add(new RegionSpec(id, fileName, sha256, bytes,
                        south, west, north, east));
            }
            return Collections.unmodifiableList(result);
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Restore the old single database until all four regional assets exist. */
    private void requestLegacyDownload() {
        if (closed || !regions.isEmpty()) return;
        if (acceptDatabase(legacyDatabaseFile, LEGACY_DATABASE_BYTES, null)) return;
        requestDatabaseDownload(null, legacyDatabaseFile, LEGACY_DATABASE_DOWNLOAD_URL,
                LEGACY_DATABASE_SHA256, LEGACY_DATABASE_BYTES);
    }

    private void requestRegionDownload(RegionSpec region) {
        File target = new File(databaseDirectory, region.fileName);
        requestDatabaseDownload(region, target, REGIONAL_RELEASE_BASE + region.fileName,
                region.sha256, region.bytes);
    }

    private void requestDatabaseDownload(RegionSpec region, File target, String url,
                                         String sha256, long expectedBytes) {
        if (closed || System.currentTimeMillis() < nextDownloadAtMs
                || !downloading.compareAndSet(false, true)) {
            return;
        }
        try {
            executor.execute(() -> {
                try {
                    if (region == null && !regions.isEmpty()) return;
                    downloadDatabase(target, url, sha256, expectedBytes);
                    if (region == null) {
                        if (regions.isEmpty()) setActiveDatabase(target);
                    } else if (region.id.equals(selectedRegionId)) {
                        setActiveDatabase(target);
                    }
                } catch (InsufficientSpaceException error) {
                    // Fix 3: back off for a long time instead of a 60 s write-fail loop.
                    nextDownloadAtMs = System.currentTimeMillis() + SPACE_RETRY_MS;
                    Log.w(TAG, "Local HUD map download postponed: " + error.getMessage());
                } catch (Throwable error) {
                    nextDownloadAtMs = System.currentTimeMillis() + DOWNLOAD_RETRY_MS;
                    Log.w(TAG, "Local HUD map download failed: " + target.getName(), error);
                } finally {
                    downloading.set(false);
                }
            });
        } catch (RuntimeException rejected) {
            downloading.set(false);
            if (!closed) nextDownloadAtMs = System.currentTimeMillis() + DOWNLOAD_RETRY_MS;
        }
    }

    /**
     * Fix 2 + 3. Downloads with HTTP Range resume: a partial {@code .download} file is
     * kept across failures and continued on the next attempt; only a checksum or
     * format mismatch discards it. Free space is checked (after pruning) before any
     * bytes are written.
     */
    private void downloadDatabase(File target, String url, String expectedSha256,
                                  long expectedBytes) throws Exception {
        if (!databaseDirectory.isDirectory() && !databaseDirectory.mkdirs()) {
            throw new IllegalStateException("Map directory is unavailable");
        }
        File temporary = new File(databaseDirectory, target.getName() + DOWNLOAD_SUFFIX);
        long resumeFrom = temporary.isFile() ? temporary.length() : 0L;
        if (resumeFrom >= expectedBytes
                || (resumeFrom > 0L && System.currentTimeMillis() - temporary.lastModified()
                        > STALE_PARTIAL_MS)) {
            deleteQuietly(temporary);
            resumeFrom = 0L;
        }

        ensureFreeSpace(target, expectedBytes - resumeFrom);

        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        if (resumeFrom > 0L) {
            hashExisting(temporary, digest);
            Log.i(TAG, "Resuming HUD map download: " + target.getName()
                    + " from " + resumeFrom + "/" + expectedBytes);
        }

        HttpURLConnection connection = null;
        long total = resumeFrom;
        boolean corrupt = false;
        try {
            connection = openConnection(url);
            if (resumeFrom > 0L) {
                connection.setRequestProperty("Range", "bytes=" + resumeFrom + "-");
            }
            int response = connection.getResponseCode();
            boolean append;
            if (response == HttpURLConnection.HTTP_PARTIAL && resumeFrom > 0L) {
                append = true;
            } else if (response == HttpURLConnection.HTTP_OK) {
                // Server ignored the range (or nothing to resume): start over.
                append = false;
                if (resumeFrom > 0L) {
                    digest.reset();
                    total = 0L;
                    Log.i(TAG, "Range not honoured; restarting HUD map download");
                }
            } else {
                throw new IllegalStateException("Map download HTTP " + response);
            }
            long contentLength = connection.getContentLengthLong();
            long expectedBody = expectedBytes - total;
            if (contentLength > 0L && contentLength != expectedBody) {
                // Asset changed size on the server: a partial from the old asset is useless.
                corrupt = total > 0L;
                throw new IllegalStateException("Unexpected map size " + contentLength
                        + " (expected " + expectedBody + ")");
            }
            try (BufferedInputStream input = new BufferedInputStream(
                    connection.getInputStream(), 256 * 1024);
                 FileOutputStream fileOutput = new FileOutputStream(temporary, append);
                 BufferedOutputStream output = new BufferedOutputStream(
                         fileOutput, 256 * 1024)) {
                byte[] buffer = new byte[256 * 1024];
                int count;
                while (!closed && (count = input.read(buffer)) >= 0) {
                    if (count == 0) continue;
                    try {
                        output.write(buffer, 0, count);
                    } catch (IOException writeError) {
                        if (isNoSpace(writeError)) {
                            output.flush();
                            throw new InsufficientSpaceException(
                                    "storage full while writing " + target.getName());
                        }
                        throw writeError;
                    }
                    digest.update(buffer, 0, count);
                    total += count;
                    if (total > expectedBytes) {
                        corrupt = true;
                        throw new IllegalStateException("Map download exceeded "
                                + expectedBytes + " bytes");
                    }
                }
                output.flush();
                fileOutput.getFD().sync();
            }
            if (closed) throw new InterruptedException("HUD map store closed");
            if (total != expectedBytes) {
                // Connection dropped: keep the partial file, resume next time.
                throw new IllegalStateException("Incomplete map download: " + total
                        + "/" + expectedBytes + " (will resume)");
            }
            if (!expectedSha256.equals(hex(digest.digest()))) {
                corrupt = true;
                throw new CorruptDownloadException("Downloaded map checksum mismatch");
            }
            try {
                validateDatabase(temporary);
            } catch (Exception error) {
                corrupt = true;
                throw new CorruptDownloadException(error.getMessage());
            }
            if (target.isFile() && !target.delete()) {
                throw new IllegalStateException("Old map database cannot be replaced");
            }
            if (!temporary.renameTo(target)) {
                throw new IllegalStateException("Downloaded map cannot be installed");
            }
            Log.i(TAG, "Local HUD map installed: " + target.getName()
                    + " (" + total + " bytes)");
        } finally {
            if (connection != null) connection.disconnect();
            if (corrupt && temporary.isFile()) {
                // Only an unusable partial is thrown away; a truncated one is resumed.
                deleteQuietly(temporary);
            }
        }
    }

    private static void hashExisting(File file, MessageDigest digest) throws IOException {
        try (BufferedInputStream input = new BufferedInputStream(
                new FileInputStream(file), 256 * 1024)) {
            byte[] buffer = new byte[256 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) {
                if (count > 0) digest.update(buffer, 0, count);
            }
        }
    }

    private static boolean isNoSpace(IOException error) {
        String message = error.getMessage();
        return message != null && (message.contains("ENOSPC")
                || message.contains("No space left"));
    }

    /**
     * Fix 3. Ensures {@code neededBytes} + margin are available, pruning unused
     * databases first. Throws {@link InsufficientSpaceException} otherwise.
     */
    private void ensureFreeSpace(File target, long neededBytes) throws IOException {
        long required = Math.max(0L, neededBytes) + SPACE_MARGIN_BYTES;
        if (availableBytes() >= required) {
            pruneDatabases(target, false);
            return;
        }
        pruneDatabases(target, true);
        long available = availableBytes();
        if (available < required) {
            throw new InsufficientSpaceException("need " + (required >> 20) + " MB, have "
                    + (available >> 20) + " MB for " + target.getName());
        }
    }

    private long availableBytes() {
        try {
            return new StatFs(databaseDirectory.getAbsolutePath()).getAvailableBytes();
        } catch (RuntimeException error) {
            return Long.MAX_VALUE;
        }
    }

    /**
     * Fix 1. Removes databases that are no longer needed:
     * regional files not in the manifest, the legacy file once regional maps are in
     * use, stray partial downloads for unknown files, and complete regional files
     * beyond the active one + {@link #MAX_KEPT_REGIONS} most recently used.
     * With {@code aggressive} everything except the active/target database goes.
     */
    private void pruneDatabases(File keep, boolean aggressive) {
        File[] files = databaseDirectory.listFiles();
        if (files == null) return;
        List<RegionSpec> known = regions;
        File active = activeDatabaseFile;
        Set<String> knownNames = new HashSet<>();
        for (RegionSpec region : known) knownNames.add(region.fileName);
        boolean regional = !known.isEmpty();
        List<File> candidates = new ArrayList<>();
        for (File file : files) {
            if (!file.isFile()) continue;
            String name = file.getName();
            if (file.equals(keep) || file.equals(active)) continue;
            if (name.endsWith(DOWNLOAD_SUFFIX)) {
                String base = name.substring(0, name.length() - DOWNLOAD_SUFFIX.length());
                boolean resumable = base.equals(keep == null ? "" : keep.getName());
                boolean known2 = base.equals(legacyDatabaseFile.getName())
                        || knownNames.contains(base);
                if (!resumable && (aggressive || !known2)) deleteLogged(file, "partial");
                continue;
            }
            if (name.equals(legacyDatabaseFile.getName())) {
                if (regional && (aggressive || active != null)) {
                    deleteLogged(file, "legacy map superseded by regional maps");
                }
                continue;
            }
            if (name.matches("hud_map_gyeonggi_[a-z]+\\.sqlite")) {
                if (!regional) continue;
                if (!knownNames.contains(name)) {
                    deleteLogged(file, "region not in manifest");
                } else if (aggressive) {
                    deleteLogged(file, "freeing space");
                } else {
                    candidates.add(file);
                }
            }
        }
        if (candidates.size() > MAX_KEPT_REGIONS) {
            Collections.sort(candidates, new Comparator<File>() {
                @Override
                public int compare(File a, File b) {
                    return Long.compare(b.lastModified(), a.lastModified());
                }
            });
            for (int i = MAX_KEPT_REGIONS; i < candidates.size(); i++) {
                deleteLogged(candidates.get(i), "least recently used region");
            }
        }
    }

    private static void deleteLogged(File file, String reason) {
        long bytes = file.length();
        if (file.delete()) {
            Log.i(TAG, "Removed " + file.getName() + " (" + (bytes >> 20) + " MB): " + reason);
        } else {
            Log.w(TAG, "Could not remove " + file.getName());
        }
    }

    private static void deleteQuietly(File file) {
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }

    private static HttpURLConnection openConnection(String url) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(15_000);
        connection.setReadTimeout(30_000);
        connection.setInstanceFollowRedirects(true);
        connection.setRequestProperty("User-Agent", "EON-Remote-HUD/1.0");
        return connection;
    }

    private static RegionSpec findRegion(List<RegionSpec> values, double lat, double lon) {
        for (RegionSpec value : values) {
            if (value.contains(lat, lon)) return value;
        }
        return null;
    }

    private void setActiveDatabase(File file) {
        File previous = activeDatabaseFile;
        if (file.equals(previous)) return;
        activeDatabaseFile = file;
        snapshot = Snapshot.EMPTY;
        loadedLat = Double.NaN;
        loadedLon = Double.NaN;
        loadedTileX = Integer.MIN_VALUE;
        loadedTileY = Integer.MIN_VALUE;
        // Recency drives the keep-N pruning; a copied file may carry an old mtime.
        //noinspection ResultOfMethodCallIgnored
        file.setLastModified(System.currentTimeMillis());
        Log.i(TAG, "Active HUD map: " + file.getName());
        if (closed) return;
        try {
            executor.execute(() -> pruneDatabases(file, false));
        } catch (RuntimeException ignored) {
            // executor shut down
        }
    }

    private static String hex(byte[] value) {
        char[] result = new char[value.length * 2];
        final char[] digits = "0123456789abcdef".toCharArray();
        for (int i = 0; i < value.length; i++) {
            int number = value[i] & 0xff;
            result[i * 2] = digits[number >>> 4];
            result[i * 2 + 1] = digits[number & 0x0f];
        }
        return new String(result);
    }

    private static void validateDatabase(File file) throws Exception {
        SQLiteDatabase database = SQLiteDatabase.openDatabase(file.getAbsolutePath(),
                null, SQLiteDatabase.OPEN_READONLY | SQLiteDatabase.NO_LOCALIZED_COLLATORS);
        try {
            String format = null;
            try (Cursor cursor = database.rawQuery(
                    "SELECT value FROM metadata WHERE name='format'", null)) {
                if (cursor.moveToFirst()) format = cursor.getString(0);
            }
            long tileCount = 0L;
            try (Cursor cursor = database.rawQuery(
                    "SELECT count(*) FROM tiles WHERE z=?",
                    new String[]{String.valueOf(ZOOM)})) {
                if (cursor.moveToFirst()) tileCount = cursor.getLong(0);
            }
            if (format == null || !format.startsWith("remote-hud-json-")
                    || tileCount <= 0L) {
                throw new IllegalStateException("Downloaded map database is incompatible");
            }
        } finally {
            database.close();
        }
    }

    private Snapshot load(File mapFile, double lat, double lon,
                          int tileX, int tileY) throws Exception {
        List<Building> buildings = new ArrayList<>();
        List<Road> roads = new ArrayList<>();
        List<Area> greens = new ArrayList<>();
        List<Area> waters = new ArrayList<>();
        Set<String> buildingIds = new HashSet<>();
        Set<String> roadIds = new HashSet<>();
        Set<String> greenIds = new HashSet<>();
        Set<String> waterIds = new HashSet<>();
        SQLiteDatabase database = SQLiteDatabase.openDatabase(mapFile.getAbsolutePath(),
                null, SQLiteDatabase.OPEN_READONLY | SQLiteDatabase.NO_LOCALIZED_COLLATORS);
        try (Cursor cursor = database.rawQuery(
                "SELECT payload FROM tiles WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
                new String[]{String.valueOf(ZOOM), String.valueOf(tileX - 1),
                        String.valueOf(tileX + 1), String.valueOf(tileY - 1),
                        String.valueOf(tileY + 1)})) {
            while (cursor.moveToNext()) {
                JSONObject payload = new JSONObject(cursor.getString(0));
                decodeBuildings(payload.optJSONArray("b"), buildingIds, buildings, lat, lon);
                decodeRoads(payload.optJSONArray("r"), roadIds, roads, lat, lon);
                decodeAreas(payload.optJSONArray("g"), greenIds, greens, lat, lon, "g");
                decodeAreas(payload.optJSONArray("w"), waterIds, waters, lat, lon, "w");
            }
        } finally {
            database.close();
        }
        Comparator<Building> buildingDistance = Comparator.comparingDouble(value -> value.distanceSq);
        Comparator<Road> roadDistance = Comparator.comparingDouble(value -> value.distanceSq);
        Comparator<Area> areaDistance = Comparator.comparingDouble(value -> value.distanceSq);
        Collections.sort(buildings, buildingDistance);
        Collections.sort(roads, roadDistance);
        Collections.sort(greens, areaDistance);
        Collections.sort(waters, areaDistance);
        if (buildings.size() > MAX_BUILDINGS) {
            buildings = new ArrayList<>(buildings.subList(0, MAX_BUILDINGS));
        }
        if (roads.size() > MAX_ROADS) {
            roads = new ArrayList<>(roads.subList(0, MAX_ROADS));
        }
        if (greens.size() > MAX_GREEN_AREAS) {
            greens = new ArrayList<>(greens.subList(0, MAX_GREEN_AREAS));
        }
        if (waters.size() > MAX_WATER_AREAS) {
            waters = new ArrayList<>(waters.subList(0, MAX_WATER_AREAS));
        }
        return new Snapshot(buildings.toArray(new Building[0]), roads.toArray(new Road[0]),
                greens.toArray(new Area[0]), waters.toArray(new Area[0]));
    }

    private static void decodeBuildings(JSONArray values, Set<String> ids,
                                        List<Building> output, double lat0, double lon0) {
        if (values == null) return;
        for (int i = 0; i < values.length(); i++) {
            JSONObject value = values.optJSONObject(i);
            if (value == null) continue;
            String id = value.optString("i", "b" + i);
            if (!ids.add(id)) continue;
            PointArrays points = points(value.optJSONArray("p"), 3);
            if (points == null) continue;
            double distance = centroidDistance(points, lat0, lon0);
            float height = (float) Math.max(4.0, Math.min(18.0, value.optDouble("h", 8.0)));
            output.add(new Building(points.lat, points.lon, height, distance));
        }
    }

    private static void decodeRoads(JSONArray values, Set<String> ids,
                                    List<Road> output, double lat0, double lon0) {
        if (values == null) return;
        for (int i = 0; i < values.length(); i++) {
            JSONObject value = values.optJSONObject(i);
            if (value == null) continue;
            String id = value.optString("i", "r" + i);
            if (!ids.add(id)) continue;
            PointArrays points = points(value.optJSONArray("p"), 2);
            if (points == null) continue;
            double distance = minimumDistance(points, lat0, lon0);
            float width = (float) Math.max(2.5, Math.min(18.0, value.optDouble("w", 5.5)));
            output.add(new Road(points.lat, points.lon, width, distance));
        }
    }

    private static void decodeAreas(JSONArray values, Set<String> ids,
                                    List<Area> output, double lat0, double lon0,
                                    String prefix) {
        if (values == null) return;
        for (int i = 0; i < values.length(); i++) {
            JSONObject value = values.optJSONObject(i);
            if (value == null) continue;
            String id = value.optString("i", prefix + i);
            if (!ids.add(id)) continue;
            PointArrays points = points(value.optJSONArray("p"), 3);
            if (points == null) continue;
            output.add(new Area(points.lat, points.lon,
                    centroidDistance(points, lat0, lon0)));
        }
    }

    private static final class PointArrays {
        final double[] lat;
        final double[] lon;

        PointArrays(double[] lat, double[] lon) {
            this.lat = lat;
            this.lon = lon;
        }
    }

    private static PointArrays points(JSONArray values, int minimum) {
        if (values == null) return null;
        int count = Math.min(values.length(), 80);
        double[] lat = new double[count];
        double[] lon = new double[count];
        int used = 0;
        for (int i = 0; i < values.length() && used < count; i++) {
            JSONArray point = values.optJSONArray(i);
            if (point == null || point.length() < 2) continue;
            double pointLat = point.optDouble(0, Double.NaN);
            double pointLon = point.optDouble(1, Double.NaN);
            if (!validPosition(pointLat, pointLon)) continue;
            lat[used] = pointLat;
            lon[used] = pointLon;
            used++;
        }
        if (used < minimum) return null;
        if (used == count) return new PointArrays(lat, lon);
        double[] trimmedLat = new double[used];
        double[] trimmedLon = new double[used];
        System.arraycopy(lat, 0, trimmedLat, 0, used);
        System.arraycopy(lon, 0, trimmedLon, 0, used);
        return new PointArrays(trimmedLat, trimmedLon);
    }

    private static double centroidDistance(PointArrays points, double lat, double lon) {
        double centerLat = 0.0;
        double centerLon = 0.0;
        for (int i = 0; i < points.lat.length; i++) {
            centerLat += points.lat[i];
            centerLon += points.lon[i];
        }
        return distanceSqMeters(lat, lon, centerLat / points.lat.length,
                centerLon / points.lon.length);
    }

    private static double minimumDistance(PointArrays points, double lat, double lon) {
        double result = Double.POSITIVE_INFINITY;
        for (int i = 0; i < points.lat.length; i++) {
            result = Math.min(result, distanceSqMeters(lat, lon, points.lat[i], points.lon[i]));
        }
        return result;
    }

    private static double distanceSqMeters(double lat0, double lon0, double lat1, double lon1) {
        double north = (lat1 - lat0) * 111320.0;
        double east = (lon1 - lon0) * 111320.0 * Math.cos(Math.toRadians(lat0));
        return north * north + east * east;
    }

    private static boolean validPosition(double lat, double lon) {
        return Double.isFinite(lat) && Double.isFinite(lon)
                && lat >= -85.0 && lat <= 85.0 && lon >= -180.0 && lon <= 180.0;
    }

    private static int tileX(double lon) {
        int scale = 1 << ZOOM;
        return Math.max(0, Math.min(scale - 1,
                (int) Math.floor((lon + 180.0) / 360.0 * scale)));
    }

    private static int tileY(double lat) {
        int scale = 1 << ZOOM;
        double radians = Math.toRadians(Math.max(-85.05112878, Math.min(85.05112878, lat)));
        return Math.max(0, Math.min(scale - 1, (int) Math.floor(
                (1.0 - Math.log(Math.tan(radians) + 1.0 / Math.cos(radians))
                        / Math.PI) * 0.5 * scale)));
    }

    void close() {
        closed = true;
        executor.shutdownNow();
        snapshot = Snapshot.EMPTY;
    }
}
