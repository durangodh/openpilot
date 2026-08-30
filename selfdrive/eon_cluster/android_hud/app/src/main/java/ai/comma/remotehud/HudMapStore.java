package ai.comma.remotehud;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
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

    private final File databaseFile;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean loading = new AtomicBoolean(false);
    private volatile Snapshot snapshot = Snapshot.EMPTY;
    private volatile boolean closed;
    private volatile double loadedLat = Double.NaN;
    private volatile double loadedLon = Double.NaN;
    private volatile int loadedTileX = Integer.MIN_VALUE;
    private volatile int loadedTileY = Integer.MIN_VALUE;

    HudMapStore(Context context) {
        File directory = context.getExternalFilesDir(null);
        databaseFile = new File(directory == null ? context.getFilesDir() : directory,
                "hud_map.sqlite");
    }

    Snapshot snapshot() {
        return snapshot;
    }

    void update(double lat, double lon) {
        if (closed || !validPosition(lat, lon) || !databaseFile.isFile()) {
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
                Snapshot loaded = load(lat, lon, tileX, tileY);
                if (!closed) {
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

    private Snapshot load(double lat, double lon, int tileX, int tileY) throws Exception {
        List<Building> buildings = new ArrayList<>();
        List<Road> roads = new ArrayList<>();
        List<Area> greens = new ArrayList<>();
        List<Area> waters = new ArrayList<>();
        Set<String> buildingIds = new HashSet<>();
        Set<String> roadIds = new HashSet<>();
        Set<String> greenIds = new HashSet<>();
        Set<String> waterIds = new HashSet<>();
        SQLiteDatabase database = SQLiteDatabase.openDatabase(databaseFile.getAbsolutePath(),
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
