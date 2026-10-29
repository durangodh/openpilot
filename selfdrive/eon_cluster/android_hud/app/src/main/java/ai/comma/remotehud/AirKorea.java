package ai.comma.remotehud;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.Locale;

/** 에어코리아 측정소 목록에서 GPS와 가장 가까운 측정소의 PM10/PM2.5를 읽는다. */
final class AirKorea {
    private static final String STATION_URL =
            "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getMsrstnList";
    private static final String AIR_URL =
            "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty";
    private static final double REUSE_STATION_M = 30000d;

    private AirKorea() { }

    static Result fetch(String apiKey, double lat, double lon,
                        String cachedName, double cachedLat, double cachedLon) throws Exception {
        Station station;
        if (!cachedName.isEmpty() && finite(cachedLat) && finite(cachedLon)
                && distanceM(lat, lon, cachedLat, cachedLon) < REUSE_STATION_M) {
            station = new Station(cachedName, cachedLat, cachedLon);
        } else {
            station = nearestStation(apiKey, lat, lon);
        }
        JSONObject measurement = request(AIR_URL + "?serviceKey=" + encodedKey(apiKey)
                + "&returnType=json&numOfRows=1&pageNo=1&dataTerm=DAILY&ver=1.3"
                + "&stationName=" + URLEncoder.encode(station.name, "UTF-8"));
        JSONArray items = items(measurement);
        if (items.length() == 0) throw new IllegalStateException("air-empty");
        JSONObject item = items.optJSONObject(0);
        double pm10 = number(item == null ? null : item.opt("pm10Value"));
        double pm25 = number(item == null ? null : item.opt("pm25Value"));
        if (!finite(pm10) && !finite(pm25)) throw new IllegalStateException("air-values");
        JSONObject current = new JSONObject();
        current.put("pm10", finite(pm10) ? pm10 : JSONObject.NULL);
        current.put("pm2_5", finite(pm25) ? pm25 : JSONObject.NULL);
        JSONObject normalized = new JSONObject();
        normalized.put("current", current);
        return new Result(normalized, station.name, station.lat, station.lon);
    }

    private static Station nearestStation(String apiKey, double lat, double lon) throws Exception {
        JSONObject root = request(STATION_URL + "?serviceKey=" + encodedKey(apiKey)
                + "&returnType=json&numOfRows=1000&pageNo=1");
        JSONArray stations = items(root);
        Station best = null;
        double bestDistance = Double.POSITIVE_INFINITY;
        for (int i = 0; i < stations.length(); i++) {
            JSONObject item = stations.optJSONObject(i);
            if (item == null) continue;
            String name = item.optString("stationName", "").trim();
            double stationLat = number(item.opt("dmX"));
            double stationLon = number(item.opt("dmY"));
            if (name.isEmpty() || !finite(stationLat) || !finite(stationLon)) continue;
            String measured = item.optString("item", "").toUpperCase(Locale.US);
            if (!measured.isEmpty() && !measured.contains("PM10") && !measured.contains("PM2.5")) continue;
            double distance = distanceM(lat, lon, stationLat, stationLon);
            if (distance < bestDistance) {
                bestDistance = distance;
                best = new Station(name, stationLat, stationLon);
            }
        }
        if (best == null) throw new IllegalStateException("station-empty");
        return best;
    }

    private static JSONObject request(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(8000);
            conn.setRequestProperty("Accept", "application/json");
            if (conn.getResponseCode() != 200) throw new IllegalStateException("http" + conn.getResponseCode());
            JSONObject root = new JSONObject(readAll(conn.getInputStream()));
            JSONObject response = root.optJSONObject("response");
            JSONObject header = response == null ? null : response.optJSONObject("header");
            String code = header == null ? "" : header.optString("resultCode", "");
            if (!("00".equals(code) || "0".equals(code))) throw new IllegalStateException("air" + code);
            return root;
        } finally {
            conn.disconnect();
        }
    }

    private static JSONArray items(JSONObject root) {
        JSONObject response = root.optJSONObject("response");
        JSONObject body = response == null ? null : response.optJSONObject("body");
        Object raw = body == null ? null : body.opt("items");
        if (raw instanceof JSONArray) return (JSONArray) raw;
        if (raw instanceof JSONObject) {
            JSONArray nested = ((JSONObject) raw).optJSONArray("item");
            if (nested != null) return nested;
        }
        return new JSONArray();
    }

    private static String encodedKey(String key) throws Exception {
        return key.indexOf('%') >= 0 ? key : URLEncoder.encode(key, "UTF-8");
    }

    private static double number(Object value) {
        try {
            String text = String.valueOf(value).trim();
            if (text.isEmpty() || "-".equals(text) || "null".equalsIgnoreCase(text)) return Double.NaN;
            return Double.parseDouble(text);
        } catch (Throwable ignored) {
            return Double.NaN;
        }
    }

    private static double distanceM(double lat1, double lon1, double lat2, double lon2) {
        double dLat = Math.toRadians(lat2 - lat1);
        double dLon = Math.toRadians(lon2 - lon1);
        double a = Math.sin(dLat * 0.5) * Math.sin(dLat * 0.5)
                + Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2))
                * Math.sin(dLon * 0.5) * Math.sin(dLon * 0.5);
        return 6371000d * 2d * Math.atan2(Math.sqrt(a), Math.sqrt(1d - a));
    }

    private static boolean finite(double value) {
        return !Double.isNaN(value) && !Double.isInfinite(value);
    }

    private static String readAll(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int read, total = 0;
        while ((read = in.read(buffer)) > 0 && total < 524288) {
            out.write(buffer, 0, read);
            total += read;
        }
        in.close();
        return out.toString("UTF-8");
    }

    static final class Result {
        final JSONObject data;
        final String stationName;
        final double stationLat, stationLon;

        Result(JSONObject data, String stationName, double stationLat, double stationLon) {
            this.data = data;
            this.stationName = stationName;
            this.stationLat = stationLat;
            this.stationLon = stationLon;
        }
    }

    private static final class Station {
        final String name;
        final double lat, lon;

        Station(String name, double lat, double lon) {
            this.name = name;
            this.lat = lat;
            this.lon = lon;
        }
    }
}
