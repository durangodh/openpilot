package ai.comma.remotehud;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

/** 기상청 초단기실황·단기예보를 HUD가 쓰는 정규화 JSON으로 바꾼다. */
final class KmaWeather {
    private static final String HOST = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/";
    private static final TimeZone KST = TimeZone.getTimeZone("Asia/Seoul");

    private KmaWeather() { }

    static JSONObject fetch(String apiKey, double lat, double lon) throws Exception {
        int[] grid = grid(lat, lon);
        Calendar now = Calendar.getInstance(KST);
        Calendar observationBase = (Calendar) now.clone();
        if (observationBase.get(Calendar.MINUTE) < 10) observationBase.add(Calendar.HOUR_OF_DAY, -1);
        observationBase.set(Calendar.MINUTE, 0);

        Calendar forecastBase = latestForecastBase(now);
        JSONObject observation = request("getUltraSrtNcst", apiKey, observationBase,
                grid[0], grid[1]);
        JSONObject forecast = request("getVilageFcst", apiKey, forecastBase,
                grid[0], grid[1]);
        return normalize(observation, forecast, now);
    }

    private static JSONObject request(String operation, String apiKey, Calendar base,
                                      int nx, int ny) throws Exception {
        String key = apiKey.indexOf('%') >= 0 ? apiKey : URLEncoder.encode(apiKey, "UTF-8");
        String date = format(base, "yyyyMMdd");
        String time = format(base, "HHmm");
        String url = HOST + operation + "?serviceKey=" + key
                + "&pageNo=1&numOfRows=1000&dataType=JSON"
                + "&base_date=" + date + "&base_time=" + time
                + "&nx=" + nx + "&ny=" + ny;
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
            if (!("00".equals(code) || "0".equals(code))) {
                throw new IllegalStateException("kma" + code);
            }
            JSONObject body = response.optJSONObject("body");
            JSONObject items = body == null ? null : body.optJSONObject("items");
            JSONArray item = items == null ? null : items.optJSONArray("item");
            if (item == null || item.length() == 0) throw new IllegalStateException("empty");
            JSONObject out = new JSONObject();
            out.put("item", item);
            return out;
        } finally {
            conn.disconnect();
        }
    }

    private static JSONObject normalize(JSONObject observation, JSONObject forecast,
                                        Calendar now) throws Exception {
        Map<String, Double> obs = new HashMap<>();
        JSONArray observations = observation.getJSONArray("item");
        for (int i = 0; i < observations.length(); i++) {
            JSONObject item = observations.optJSONObject(i);
            if (item != null) obs.put(item.optString("category"), number(item.opt("obsrValue")));
        }

        Map<String, Map<String, String>> slots = new HashMap<>();
        JSONArray forecasts = forecast.getJSONArray("item");
        for (int i = 0; i < forecasts.length(); i++) {
            JSONObject item = forecasts.optJSONObject(i);
            if (item == null) continue;
            String date = item.optString("fcstDate");
            String time = item.optString("fcstTime");
            String category = item.optString("category");
            if (date.length() != 8 || time.length() != 4 || category.isEmpty()) continue;
            String slot = date + time;
            Map<String, String> values = slots.get(slot);
            if (values == null) {
                values = new HashMap<>();
                slots.put(slot, values);
            }
            values.put(category, item.optString("fcstValue"));
        }
        if (slots.isEmpty()) throw new IllegalStateException("forecast-empty");

        List<String> slotKeys = new ArrayList<>(slots.keySet());
        Collections.sort(slotKeys);
        String nowKey = format(now, "yyyyMMddHHmm");
        String currentKey = slotKeys.get(slotKeys.size() - 1);
        for (String slot : slotKeys) {
            if (slot.compareTo(nowKey) >= 0) {
                currentKey = slot;
                break;
            }
        }
        Map<String, String> currentSlot = slots.get(currentKey);
        double temp = finite(obs.get("T1H")) ? obs.get("T1H") : value(currentSlot, "TMP");
        double humidity = finite(obs.get("REH")) ? obs.get("REH") : value(currentSlot, "REH");
        double wind = finite(obs.get("WSD")) ? obs.get("WSD") : value(currentSlot, "WSD");
        int wmo = wmo(currentSlot);

        JSONObject current = new JSONObject();
        current.put("temperature_2m", jsonNumber(temp));
        current.put("apparent_temperature", jsonNumber(apparent(temp, humidity, wind)));
        current.put("weather_code", wmo);
        current.put("is_day", now.get(Calendar.HOUR_OF_DAY) >= 6 && now.get(Calendar.HOUR_OF_DAY) < 18 ? 1 : 0);
        current.put("cloud_cover", cloudPercent(currentSlot));

        LinkedHashMap<String, Day> days = new LinkedHashMap<>();
        Calendar dayCursor = (Calendar) now.clone();
        for (int i = 0; i < 4; i++) {
            days.put(format(dayCursor, "yyyyMMdd"), new Day());
            dayCursor.add(Calendar.DAY_OF_MONTH, 1);
        }
        for (String slot : slotKeys) {
            String date = slot.substring(0, 8);
            Day day = days.get(date);
            if (day == null) continue;
            Map<String, String> values = slots.get(slot);
            double hourly = value(values, "TMP");
            if (finite(hourly)) {
                day.min = finite(day.min) ? Math.min(day.min, hourly) : hourly;
                day.max = finite(day.max) ? Math.max(day.max, hourly) : hourly;
            }
            double tmn = value(values, "TMN");
            double tmx = value(values, "TMX");
            if (finite(tmn)) day.min = tmn;
            if (finite(tmx)) day.max = tmx;
            int hour = Integer.parseInt(slot.substring(8, 10));
            int distance = Math.abs(hour - 12);
            if (distance < day.iconDistance) {
                day.iconDistance = distance;
                day.wmo = wmo(values);
            }
        }

        JSONArray dates = new JSONArray();
        JSONArray codes = new JSONArray();
        JSONArray maxs = new JSONArray();
        JSONArray mins = new JSONArray();
        for (Map.Entry<String, Day> entry : days.entrySet()) {
            String date = entry.getKey();
            Day day = entry.getValue();
            dates.put(date.substring(0, 4) + "-" + date.substring(4, 6) + "-" + date.substring(6));
            codes.put(day.wmo);
            maxs.put(jsonNumber(day.max));
            mins.put(jsonNumber(day.min));
        }
        JSONObject daily = new JSONObject();
        daily.put("time", dates);
        daily.put("weather_code", codes);
        daily.put("temperature_2m_max", maxs);
        daily.put("temperature_2m_min", mins);
        JSONObject root = new JSONObject();
        root.put("current", current);
        root.put("daily", daily);
        return root;
    }

    static int[] grid(double lat, double lon) {
        final double re = 6371.00877 / 5.0;
        final double slat1 = Math.toRadians(30.0), slat2 = Math.toRadians(60.0);
        final double olon = Math.toRadians(126.0), olat = Math.toRadians(38.0);
        double sn = Math.log(Math.cos(slat1) / Math.cos(slat2))
                / Math.log(Math.tan(Math.PI * 0.25 + slat2 * 0.5)
                / Math.tan(Math.PI * 0.25 + slat1 * 0.5));
        double sf = Math.pow(Math.tan(Math.PI * 0.25 + slat1 * 0.5), sn)
                * Math.cos(slat1) / sn;
        double ro = re * sf / Math.pow(Math.tan(Math.PI * 0.25 + olat * 0.5), sn);
        double ra = re * sf / Math.pow(Math.tan(Math.PI * 0.25 + Math.toRadians(lat) * 0.5), sn);
        double theta = Math.toRadians(lon) - olon;
        if (theta > Math.PI) theta -= 2.0 * Math.PI;
        if (theta < -Math.PI) theta += 2.0 * Math.PI;
        theta *= sn;
        return new int[]{(int) Math.floor(ra * Math.sin(theta) + 43.0 + 0.5),
                (int) Math.floor(ro - ra * Math.cos(theta) + 136.0 + 0.5)};
    }

    static double apparent(double temp, double humidity, double wind) {
        if (!finite(temp) || !finite(humidity) || !finite(wind)) return Double.NaN;
        double vapour = humidity * 0.01 * 6.105 * Math.exp(17.27 * temp / (237.7 + temp));
        return temp + 0.33 * vapour - 0.70 * wind - 4.0;
    }

    private static Calendar latestForecastBase(Calendar now) {
        Calendar eligible = (Calendar) now.clone();
        eligible.add(Calendar.MINUTE, -15);
        int hour = eligible.get(Calendar.HOUR_OF_DAY);
        int[] bases = {2, 5, 8, 11, 14, 17, 20, 23};
        int selected = -1;
        for (int base : bases) if (base <= hour) selected = base;
        if (selected < 0) {
            eligible.add(Calendar.DAY_OF_MONTH, -1);
            selected = 23;
        }
        eligible.set(Calendar.HOUR_OF_DAY, selected);
        eligible.set(Calendar.MINUTE, 0);
        eligible.set(Calendar.SECOND, 0);
        eligible.set(Calendar.MILLISECOND, 0);
        return eligible;
    }

    private static int wmo(Map<String, String> values) {
        int pty = (int) value(values, "PTY");
        if (pty == 3 || pty == 7) return 71;
        if (pty == 2 || pty == 6) return 67;
        if (pty == 1 || pty == 4 || pty == 5) return 61;
        int sky = (int) value(values, "SKY");
        return sky == 1 ? 0 : sky == 3 ? 2 : 3;
    }

    private static int cloudPercent(Map<String, String> values) {
        int sky = (int) value(values, "SKY");
        return sky == 1 ? 10 : sky == 3 ? 50 : 90;
    }

    private static double value(Map<String, String> values, String key) {
        if (values == null) return Double.NaN;
        try { return Double.parseDouble(values.get(key)); } catch (Throwable ignored) { return Double.NaN; }
    }

    private static double number(Object value) {
        try { return Double.parseDouble(String.valueOf(value)); } catch (Throwable ignored) { return Double.NaN; }
    }

    private static boolean finite(Double value) {
        return value != null && !Double.isNaN(value) && !Double.isInfinite(value);
    }

    private static Object jsonNumber(double value) {
        return finite(value) ? value : JSONObject.NULL;
    }

    private static String format(Calendar value, String pattern) {
        SimpleDateFormat f = new SimpleDateFormat(pattern, Locale.US);
        f.setTimeZone(KST);
        return f.format(value.getTime());
    }

    private static String readAll(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int read, total = 0;
        while ((read = in.read(buffer)) > 0 && total < 262144) {
            out.write(buffer, 0, read);
            total += read;
        }
        in.close();
        return out.toString("UTF-8");
    }

    private static final class Day {
        double min = Double.NaN, max = Double.NaN;
        int wmo = 3, iconDistance = Integer.MAX_VALUE;
    }
}
