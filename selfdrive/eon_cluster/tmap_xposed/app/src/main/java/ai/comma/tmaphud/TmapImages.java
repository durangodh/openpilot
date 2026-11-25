package ai.comma.tmaphud;

import android.content.Context;
import android.content.res.Resources;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 2차: 티맵 자체 그림을 이미지 스트림으로 보낸다.
 *
 *  tbt_current_compact  현재 회전 아이콘만(흰색, 투명 배경). HUD 가 회전 아이콘으로 쓴다.
 *  tbt_current_full     녹색 배너: 아이콘 + 거리 + 안내 문구(티맵 상단 TBT)
 *  tbt_next             어두운 녹색 상자: 다음 회전 아이콘 + 구간 거리
 *  lane_bottom          차로 안내 띠(티맵 차로 화살표, 추천 차로는 주황)
 *  safety_primary/secondary  단속 표지(티맵 c_XX 아이콘 + 제한속도 + 거리)
 *  safety_section       구간단속(제한속도·평균속도·남은 거리)
 *  crossroad_expanded   분기 실사 이미지(티맵이 띄우는 것과 같은 URL)
 *  crossroad_minimized  같은 이미지의 절반 크기
 *
 * 아이콘은 티맵 리소스를 이름으로 꺼내 티맵 스타일(NavigationTbtIcon.Top,
 * NavigationLaneBubbleMarkerIcon.Night*)을 입힌 테마로 그린다. 그림은 값이 바뀔 때만,
 * 이름마다 최대 2fps 로 보낸다. 표시할 것이 없어지면 CNV2 clear 를 한 번 보낸다.
 */
final class TmapImages {
    private static final long MIN_INTERVAL_MS = 500;
    private static final int MAX_IMAGE_BYTES = 480 * 1024;  // 서버 MAX_LANE_FRAME_BYTES 512KB
    private static final int TBT_GREEN = 0xFF358A62;         // NavigationTbtIcon.Top baseColor
    private static final int TBT_GREEN_DARK = 0xFF25644A;
    private static final int PANEL = 0xE6101216;

    private final TmapNaviClient client;
    private volatile Context context;
    private Resources res;
    private String pkg;
    private Resources.Theme tbtTheme, laneTheme, laneSuggestedTheme;
    private boolean resourcesLogged;

    private final Map<String, String> lastKey = new HashMap<>();
    private final Map<String, Long> lastAt = new HashMap<>();
    private long sequence;

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);

    // 교차로 실사: 내려받기는 별도 스레드. 마지막 URL 의 결과만 쓴다.
    private final ExecutorService fetcher = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "tmap-hud-crossroad");
        t.setDaemon(true);
        return t;
    });
    private volatile String crossroadUrl;
    private volatile byte[] crossroadBytes;
    private volatile String crossroadBytesUrl;
    private volatile boolean fetching;
    private long lastFetchFailAt;

    TmapImages(TmapNaviClient client) {
        this.client = client;
        text.setTypeface(Typeface.DEFAULT_BOLD);
    }

    void setContext(Context ctx) {
        context = ctx;
    }

    /** 상태 스레드(TmapBridge)에서 250ms 마다 호출. rg 가 null 이면 모두 지운다. */
    void update(Object rg, boolean guiding, boolean night) {
        if (!client.ready() || !prepare()) return;
        Object cur = guiding && rg != null ? TmapBridge.field(rg, "stGuidePoint") : null;
        Object next = guiding && rg != null ? TmapBridge.field(rg, "stGuidePointNext") : null;
        int curType = TmapBridge.intField(cur, "nTBTTurnType");
        int curDist = TmapBridge.intField(cur, "nTBTDist");
        String curText = TmapJson.text(TmapBridge.stringField(cur, "szTBTMainText"));
        if (curText.isEmpty()) curText = TmapJson.text(TmapBridge.stringField(cur, "szRoadName"));
        final String curIcon = curType > 0 ? TmapAssets.tbtIcon(curType) : null;

        publish("tbt_current_compact", curIcon, () -> renderIcon(curIcon, 120));
        final String fullText = curText;
        final String fullDist = TmapAssets.distanceText(curDist);
        publish("tbt_current_full", curType > 0 ? curType + "|" + fullDist + "|" + fullText : null,
                () -> renderFull(curIcon, fullDist, fullText));

        int nextType = curType > 0 ? TmapBridge.intField(next, "nTBTTurnType") : 0;
        final String nextIcon = nextType > 0 ? TmapAssets.tbtIcon(nextType) : null;
        final String nextDist = TmapAssets.distanceText(TmapJson.nextSegmentDistance(
                TmapBridge.intField(next, "nSvcLinkDist"), TmapBridge.intField(next, "nTBTDist"), curDist));
        publish("tbt_next", nextType > 0 ? nextType + "|" + nextDist : null,
                () -> renderNext(nextIcon, nextDist));

        publishLane(rg, guiding ? hiPassLanes(rg, curType, curDist) : null, curDist);
        publishSafety(rg);
        // The junction popup slot: TMAP's junction picture first, then the direction sign picture,
        // then a road incident notice (2026-11-25).
        boolean live = guiding && rg != null;
        if (live && TmapBridge.booleanField(rg, "bExtcImage")) {
            publishCrossroad(rg, night);
        } else if (live && (publishDirSign(rg, curDist) || publishIncident(rg))) {
            crossroadUrl = null;
            publish("crossroad_minimized", null, null);
        } else {
            publishCrossroad(null, night);
        }
    }

    // ---- 하이패스 차로 ----

    /**
     * 톨게이트 하이패스 차로 번호(없으면 null). 티맵 ObservableLaneData.showHiPassLane 과 같은 조건:
     * nHiPassLaneCount > 0, 지금 안내가 톨게이트(회전 종류 249·153·154), 2km 이내.
     * nHiPassArry 는 하이패스 차로 번호 목록(티맵은 ic_hipass_번호 아이콘으로 그린다).
     */
    static int[] hiPassLanes(Object rg, int turnType, int turnDist) {
        if (rg == null || !(turnType == 249 || turnType == 153 || turnType == 154)) return null;
        if (turnDist <= 0 || turnDist > 2000) return null;
        int count = TmapBridge.intField(rg, "nHiPassLaneCount");
        int[] lanes = TmapBridge.intArrayField(rg, "nHiPassArry");
        if (count <= 0 || lanes == null) return null;
        int n = 0;
        int[] out = new int[Math.min(count, Math.min(lanes.length, 10))];
        for (int i = 0; i < out.length; i++) if (lanes[i] >= 1 && lanes[i] <= 20) out[n++] = lanes[i];
        return n == 0 ? null : java.util.Arrays.copyOf(out, n);
    }

    private boolean hiPassLogged;

    /** 하이패스 줄: 파란 "하이패스" 글자와 차로 번호 동그라미. 높이 rowH. */
    private void drawHiPassRow(Canvas c, int[] lanes, float left, float top, int rowH) {
        text.setStyle(Paint.Style.FILL);
        text.setTextAlign(Paint.Align.LEFT);
        text.setTextSize(rowH * 0.46f);
        text.setColor(0xFF4DA3FF);
        Paint.FontMetrics fm = text.getFontMetrics();
        float baseline = top + rowH / 2f - (fm.ascent + fm.descent) / 2f;
        c.drawText("하이패스", left, baseline, text);
        float x = left + text.measureText("하이패스") + rowH * 0.25f;
        float r = rowH * 0.36f;
        for (int lane : lanes) {
            paint.setColor(0xFF1E6FE0);
            c.drawCircle(x + r, top + rowH / 2f, r, paint);
            text.setColor(Color.WHITE);
            text.setTextAlign(Paint.Align.CENTER);
            text.setTextSize(lane >= 10 ? r * 1.05f : r * 1.3f);
            Paint.FontMetrics m = text.getFontMetrics();
            c.drawText(String.valueOf(lane), x + r, top + rowH / 2f - (m.ascent + m.descent) / 2f, text);
            x += 2 * r + rowH * 0.14f;
        }
    }

    private float hiPassRowWidth(int[] lanes, int rowH) {
        text.setTextSize(rowH * 0.46f);
        return text.measureText("하이패스") + rowH * 0.25f + lanes.length * (rowH * 0.72f + rowH * 0.14f);
    }

    // ---- 방면 표지판 그림 ----

    private volatile Object navigationManager;
    private volatile int dirCode;            // 받아 둔 그림의 코드
    private volatile byte[] dirBytes;        // PNG
    private volatile int dirFetching;        // 받고 있는 코드
    private int dirLoggedCode;

    void setNavigationManager(Object nav) {
        navigationManager = nav;
    }

    /**
     * 방면 표지판 그림(RGData.bDirImage / nDirImageCode): 엔진의 getDirImageBuffer(code, w[], h[]) 가
     * 그림 바이트를 준다(티맵 연동 서비스 getTmapDirImage 와 같은 호출). 형식은 실차에서 확인되지
     * 않아 PNG/JPEG 서명과 크기(픽셀당 2·3·4 바이트)로 가려 읽고, 처음 받은 값을 기록한다.
     */
    private boolean publishDirSign(Object rg, int turnDist) {
        if (!TmapBridge.booleanField(rg, "bDirImage")) return false;
        final int code = TmapBridge.intField(rg, "nDirImageCode");
        int showDist = TmapBridge.intField(rg, "nDirImageShowDist");
        if (code == 0) return false;
        if (code != dirLoggedCode) {
            dirLoggedCode = code;
            TmapHudLog.line("dir sign: code=" + code + " showDist=" + showDist + " turnDist=" + turnDist);
        }
        if (showDist > 0 && turnDist > showDist) return false;
        byte[] image = dirCode == code ? dirBytes : null;
        if (image == null) {
            final Object nav = navigationManager;
            if (nav != null && dirFetching != code) {
                dirFetching = code;
                fetcher.execute(() -> fetchDirSign(nav, code));
            }
            return false;
        }
        publishBytes("crossroad_expanded", "dir:" + code, image);
        return true;
    }

    private void fetchDirSign(Object nav, int code) {
        try {
            Object engine = TmapBridge.call(nav, "getTmapNavigationEngineInterface");
            if (engine == null) return;
            java.lang.reflect.Method m = engine.getClass().getMethod("getDirImageBuffer",
                    short.class, int[].class, int[].class);
            int[] w = new int[1], h = new int[1];
            byte[] raw = (byte[]) m.invoke(engine, (short) code, w, h);
            StringBuilder head = new StringBuilder();
            for (int i = 0; raw != null && i < Math.min(8, raw.length); i++) head.append(String.format("%02x", raw[i]));
            TmapHudLog.line("dir sign image: code=" + code + " " + w[0] + "x" + h[0] + " bytes="
                    + (raw == null ? -1 : raw.length) + " head=" + head);
            Bitmap bmp = decodeRaw(raw, w[0], h[0]);
            if (bmp == null) return;
            ByteArrayOutputStream png = new ByteArrayOutputStream(65536);
            bmp.compress(Bitmap.CompressFormat.PNG, 100, png);
            bmp.recycle();
            dirBytes = png.toByteArray();
            dirCode = code;
        } catch (Throwable error) {
            TmapHudLog.ex("dir sign image", error);
        }
    }

    /** PNG/JPEG 이면 그대로, 아니면 w×h 와 길이로 RGB565 / RGB888 / RGBA8888 을 가려 읽는다. */
    static Bitmap decodeRaw(byte[] raw, int w, int h) {
        if (raw == null || raw.length < 16) return null;
        Bitmap coded = BitmapFactory.decodeByteArray(raw, 0, raw.length);
        if (coded != null) return coded;
        if (w <= 0 || h <= 0 || w > 2048 || h > 2048) return null;
        long px = (long) w * h;
        int bpp = raw.length >= px * 4 ? 4 : raw.length >= px * 3 ? 3 : raw.length >= px * 2 ? 2 : 0;
        if (bpp == 0) return null;
        int[] out = new int[(int) px];
        for (int i = 0, o = 0; i < px; i++, o += bpp) {
            if (bpp == 2) {
                int v = (raw[o] & 255) | ((raw[o + 1] & 255) << 8);
                out[i] = 0xFF000000 | ((v >> 11) * 255 / 31 << 16) | (((v >> 5) & 63) * 255 / 63 << 8) | ((v & 31) * 255 / 31);
            } else {
                int a = bpp == 4 ? raw[o + 3] & 255 : 255;
                out[i] = (a << 24) | ((raw[o] & 255) << 16) | ((raw[o + 1] & 255) << 8) | (raw[o + 2] & 255);
            }
        }
        return Bitmap.createBitmap(out, w, h, Bitmap.Config.ARGB_8888);
    }

    // ---- 돌발 정보 ----

    private String incidentKey;
    private long incidentSince;

    /**
     * 돌발 정보(RGData.bRTM / nRtmCode / nRtmDist): 종류 코드의 뜻은 확인되지 않아 "돌발 상황"과 남은
     * 거리만 보여 주고 코드를 기록한다. EON 박스를 오래 가리지 않게, 처음 뜰 때 8초와 300m 안에서만.
     */
    private boolean publishIncident(Object rg) {
        if (!TmapBridge.booleanField(rg, "bRTM")) {
            incidentKey = null;
            return false;
        }
        int code = TmapBridge.intField(rg, "nRtmCode");
        int dist = TmapBridge.intField(rg, "nRtmDist");
        if (dist <= 0) return false;
        String key = code + "@" + Math.round(TmapBridge.doubleField(rg, "wpRtmPosLat") * 1e4) + ","
                + Math.round(TmapBridge.doubleField(rg, "wpRtmPosLon") * 1e4);
        long now = SystemClock.elapsedRealtime();
        if (!key.equals(incidentKey)) {
            incidentKey = key;
            incidentSince = now;
            TmapHudLog.line("incident: code=" + code + " dist=" + dist + " key=" + key);
        }
        if (now - incidentSince > 8000 && dist > 300) return false;
        final String distance = TmapAssets.distanceText(dist >= 1000 ? dist : dist / 50 * 50);
        publish("crossroad_expanded", "rtm:" + key + "|" + distance, () -> renderIncident(distance));
        return true;
    }

    private Bitmap renderIncident(String distance) {
        int w = 420, h = 280;
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        paint.setColor(0xFF15181D);
        c.drawRoundRect(new RectF(0, 0, w, h), 18, 18, paint);
        // 경고 삼각형
        android.graphics.Path tri = new android.graphics.Path();
        tri.moveTo(w / 2f, 28);
        tri.lineTo(w / 2f + 66, 140);
        tri.lineTo(w / 2f - 66, 140);
        tri.close();
        paint.setColor(0xFFFFB020);
        c.drawPath(tri, paint);
        text.setStyle(Paint.Style.FILL);
        text.setTextAlign(Paint.Align.CENTER);
        text.setColor(0xFF15181D);
        text.setTextSize(78);
        c.drawText("!", w / 2f, 128, text);
        text.setColor(Color.WHITE);
        text.setTextSize(44);
        c.drawText("전방 돌발 상황", w / 2f, 198, text);
        text.setColor(0xFFFFB020);
        text.setTextSize(52);
        c.drawText(distance, w / 2f, 256, text);
        return bmp;
    }

    void clearAll() {
        for (String name : lastKey.keySet().toArray(new String[0])) publish(name, null, null);
        crossroadUrl = null;
    }

    // ---- 차로 ----

    private void publishLane(Object rg, final int[] hiPass, int tollDist) {
        int count = Math.min(8, TmapBridge.intField(rg, "nLaneCount"));
        int[] turns = TmapBridge.intArrayField(rg, "nLaneTurnInfo");
        int[] avail = TmapBridge.intArrayField(rg, "nLaneAvailable");
        int[] etc = TmapBridge.intArrayField(rg, "nLaneEtcInfo");
        boolean show = rg != null && count > 0 && TmapBridge.booleanField(rg, "bLane");
        if (hiPass != null && !hiPassLogged) {
            hiPassLogged = true;
            TmapHudLog.line("hipass lanes " + java.util.Arrays.toString(hiPass) + " at " + tollDist + " m, lanes shown=" + show);
        }
        if (!show) {
            if (hiPass == null) {
                publish("lane_bottom", null, null);
            } else {
                // 차로 화살표가 없는 톨게이트: 하이패스 줄만, 위에 톨게이트까지 거리.
                final int d = tollDist >= 1000 ? tollDist : tollDist / 10 * 10;
                publish("lane_bottom", "hp" + java.util.Arrays.toString(hiPass) + "|" + d,
                        () -> renderLanes(new int[0], new int[0], new int[0], d, hiPass));
            }
            return;
        }
        final int n = count;
        final int[] t = new int[n], a = new int[n], e = new int[n];
        for (int i = 0; i < n; i++) {
            t[i] = turns != null && i < turns.length ? turns[i] : 0;
            a[i] = avail != null && i < avail.length ? avail[i] : 0;
            e[i] = etc != null && i < etc.length ? etc[i] : 0;
        }
        // 폰 주행 화면처럼 차로 상자 위에 남은 거리(nLaneDist)를 붙인다. 이름마다 최대 2fps.
        final int dist = Math.max(0, TmapBridge.intField(rg, "nLaneDist"));
        String key = n + "|" + TmapJson.ints(t, n) + TmapJson.ints(a, n) + TmapJson.ints(e, n) + "|" + dist
                + (hiPass == null ? "" : "|hp" + java.util.Arrays.toString(hiPass));
        publish("lane_bottom", key, () -> renderLanes(t, a, e, dist, hiPass));
    }

    private Bitmap renderLanes(int[] turns, int[] avail, int[] etc, int distanceM, int[] hiPass) {
        int n = turns.length, cell = 72, pad = 8;
        int hpH = hiPass == null ? 0 : (n == 0 ? cell : 52);          // 하이패스 줄 높이
        int hpW = hiPass == null ? 0 : Math.round(hiPassRowWidth(hiPass, hpH)) + pad * 2;
        boolean anySuggested = false;
        for (int v : etc) anySuggested |= TmapAssets.laneSuggested(v);
        int boxW = Math.max(n * cell + pad * 2, hpW), boxH = (n > 0 ? cell : 0) + hpH + pad * 2;
        // 위쪽 거리 글자: 티맵처럼 흰 테두리의 검은 굵은 글씨.
        String label = distanceM > 0 ? TmapAssets.distanceText(distanceM) : "";
        float textSize = 34f;
        text.setTextSize(textSize);
        text.setTextAlign(Paint.Align.CENTER);
        int labelH = label.isEmpty() ? 0 : Math.round(textSize * 1.2f);
        int w = Math.max(boxW, Math.round(text.measureText(label) + 12));
        Bitmap bmp = Bitmap.createBitmap(w, labelH + boxH, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        if (!label.isEmpty()) {
            float baseline = textSize;
            text.setStyle(Paint.Style.STROKE);
            text.setStrokeWidth(6f);
            text.setColor(Color.WHITE);
            c.drawText(label, w / 2f, baseline, text);
            text.setStyle(Paint.Style.FILL);
            text.setColor(0xFF111111);
            c.drawText(label, w / 2f, baseline, text);
        }
        c.translate((w - boxW) / 2f, labelH);
        paint.setColor(PANEL);
        c.drawRoundRect(new RectF(0, 0, boxW, boxH), 14, 14, paint);
        if (hiPass != null) {
            drawHiPassRow(c, hiPass, (boxW - (hpW - pad * 2)) / 2f, pad, hpH);
            c.translate((boxW - (n * cell + pad * 2)) / 2f, hpH);
        }
        for (int i = 0; i < n; i++) {
            Resources.Theme theme = anySuggested && TmapAssets.laneSuggested(etc[i]) ? laneSuggestedTheme : laneTheme;
            int left = pad + i * cell;
            draw(c, TmapAssets.laneArrow(turns[i], avail[i], etc[i]), theme, left, pad, cell, cell);
            String pocket = TmapAssets.lanePocket(avail[i], etc[i]);
            if (pocket != null) draw(c, pocket, theme, left, pad, cell, cell);
        }
        return bmp;
    }

    // ---- TBT ----

    private Bitmap renderIcon(String icon, int size) {
        Bitmap bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        if (!draw(new Canvas(bmp), icon, tbtTheme, 0, 0, size, size)) {
            bmp.recycle();
            return null;
        }
        return bmp;
    }

    private Bitmap renderFull(String icon, String distance, String label) {
        int w = 420, h = 110;
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        paint.setColor(TBT_GREEN);
        c.drawRoundRect(new RectF(0, 0, w, h), 12, 12, paint);
        draw(c, icon, tbtTheme, 10, 10, 90, 90);
        text.setColor(Color.WHITE);
        text.setTextAlign(Paint.Align.LEFT);
        text.setTextSize(46);
        c.drawText(distance, 112, 72, text);
        float x = 112 + text.measureText(distance) + 14;
        text.setTextSize(30);
        c.drawText(ellipsize(label, w - x - 10), x, 70, text);
        return bmp;
    }

    private Bitmap renderNext(String icon, String distance) {
        int w = 200, h = 76;
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        paint.setColor(TBT_GREEN_DARK);
        c.drawRoundRect(new RectF(0, 0, w, h), 10, 10, paint);
        draw(c, icon, tbtTheme, 8, 8, 60, 60);
        text.setColor(Color.WHITE);
        text.setTextAlign(Paint.Align.LEFT);
        text.setTextSize(34);
        c.drawText(distance, 78, 50, text);
        return bmp;
    }

    // ---- 안전 ----

    private void publishSafety(Object rg) {
        Object[] sdis = TmapBridge.objectArrayField(rg, "sdiInfo");
        int count = Math.min(sdis == null ? 0 : sdis.length, Math.max(0, TmapBridge.intField(rg, "sdiCount")));
        String[] names = {"safety_primary", "safety_secondary"};
        int slot = 0;
        for (int i = 0; i < count && slot < names.length; i++) {
            Object s = sdis[i];
            int type = TmapBridge.intField(s, "nSdiType");
            int dist = TmapBridge.intField(s, "nSdiDist");
            boolean changed = TmapBridge.booleanField(s, "bIsLimitSpeedSignChanged");
            final String[] icons = TmapAssets.safetyIcons(type, changed);
            if (icons == null || dist <= 0) continue;
            final int limit = TmapAssets.safetyLimit(type, changed,
                    TmapBridge.intField(s, "nSdiSpeedLimit"), TmapBridge.intField(s, "nSdiBlockSpeed"));
            final String distance = TmapAssets.distanceText(dist);
            publish(names[slot++], type + "|" + limit + "|" + distance, () -> renderSafety(icons, limit, distance));
        }
        while (slot < names.length) publish(names[slot++], null, null);

        Object sec = TmapBridge.field(rg, "sectionSpeedInfo");
        if (sec != null && TmapBridge.booleanField(sec, "isInSection")
                && !TmapBridge.booleanField(sec, "isSuspended")) {
            final int limit = TmapBridge.intField(sec, "speedLimit");
            final int average = (int) Math.round(TmapBridge.doubleField(sec, "averageSpeed"));
            final String remain = TmapAssets.distanceText((int) Math.round(TmapBridge.doubleField(sec, "remainingDistance")));
            publish("safety_section", limit + "|" + average + "|" + remain, () -> renderSection(limit, average, remain));
        } else {
            publish("safety_section", null, null);
        }
    }

    private Bitmap renderSafety(String[] icons, int limit, String distance) {
        int w = 220, h = 130;
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        paint.setColor(PANEL);
        c.drawRoundRect(new RectF(0, 0, w, h), 14, 14, paint);
        draw(c, icons[0], null, 8, 8, 93, 114);              // c_XX (86×106dp 비율)
        if (limit > 0) speedSign(c, 160, 52, 40, limit, "tmap_speed_sign_blue".equals(icons[1]));
        text.setColor(Color.WHITE);
        text.setTextAlign(Paint.Align.CENTER);
        text.setTextSize(28);
        c.drawText(distance, 160, 122, text);
        return bmp;
    }

    private Bitmap renderSection(int limit, int average, String remain) {
        int w = 260, h = 130;
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        paint.setColor(PANEL);
        c.drawRoundRect(new RectF(0, 0, w, h), 14, 14, paint);
        speedSign(c, 58, 58, 46, limit, false);
        text.setColor(Color.WHITE);
        text.setTextAlign(Paint.Align.LEFT);
        text.setTextSize(24);
        c.drawText("구간단속", 118, 34, text);
        text.setTextSize(30);
        c.drawText("평균 " + Math.max(0, average), 118, 74, text);
        text.setTextSize(26);
        c.drawText(remain, 118, 112, text);
        return bmp;
    }

    /** 제한속도 원형 표지(빨간 테두리·흰 바탕·검은 숫자). 버스전용은 파란 테두리. */
    private void speedSign(Canvas c, float cx, float cy, float r, int limit, boolean blue) {
        paint.setColor(blue ? 0xFF1E5BD8 : 0xFFE63926);
        c.drawCircle(cx, cy, r, paint);
        paint.setColor(Color.WHITE);
        c.drawCircle(cx, cy, r * 0.78f, paint);
        text.setColor(Color.BLACK);
        text.setTextAlign(Paint.Align.CENTER);
        text.setTextSize(limit >= 100 ? r * 0.82f : r);
        Paint.FontMetrics fm = text.getFontMetrics();
        c.drawText(String.valueOf(limit), cx, cy - (fm.ascent + fm.descent) / 2f, text);
    }

    // ---- 교차로 실사 ----

    private void publishCrossroad(Object rg, boolean night) {
        if (rg == null) {
            crossroadUrl = null;
            publish("crossroad_expanded", null, null);
            publish("crossroad_minimized", null, null);
            return;
        }
        String base = TmapBridge.stringField(rg, "szImageBaseUrl");
        String uri = TmapBridge.stringField(rg, night ? "szImageNightUri" : "szImageDayUri");
        if (uri.isEmpty()) uri = TmapBridge.stringField(rg, "szImageDayUri");
        final String url = base + uri;
        if (!url.startsWith("http") || uri.isEmpty()) {
            publish("crossroad_expanded", null, null);
            publish("crossroad_minimized", null, null);
            return;
        }
        crossroadUrl = url;
        byte[] bytes = crossroadBytes;
        if (bytes == null || !url.equals(crossroadBytesUrl)) {
            fetchCrossroad(url);
            return;   // 받는 동안 이전 그림을 유지(같은 분기면 그대로, 새 분기면 곧 바뀜)
        }
        final byte[] image = bytes;
        publishBytes("crossroad_expanded", url, image);
        publish("crossroad_minimized", url, () -> scaled(image, 0.5f));
    }

    private void fetchCrossroad(final String url) {
        long now = SystemClock.elapsedRealtime();
        if (fetching || now - lastFetchFailAt < 10000) return;
        fetching = true;
        fetcher.execute(() -> {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                try (InputStream in = conn.getInputStream()) {
                    ByteArrayOutputStream out = new ByteArrayOutputStream(200000);
                    byte[] buf = new byte[16384];
                    int r;
                    while ((r = in.read(buf)) > 0) {
                        out.write(buf, 0, r);
                        if (out.size() > 4 * 1024 * 1024) throw new IllegalStateException("too large");
                    }
                    byte[] data = out.toByteArray();
                    if (data.length > MAX_IMAGE_BYTES) data = shrink(data);
                    if (data != null && url.equals(crossroadUrl)) {
                        crossroadBytes = data;
                        crossroadBytesUrl = url;
                        TmapHudLog.line("crossroad image " + data.length + " bytes");
                    }
                } finally {
                    conn.disconnect();
                }
            } catch (Throwable error) {
                lastFetchFailAt = SystemClock.elapsedRealtime();
                TmapHudLog.ex("crossroad fetch", error);
            } finally {
                fetching = false;
            }
        });
    }

    private static byte[] shrink(byte[] data) {
        Bitmap src = BitmapFactory.decodeByteArray(data, 0, data.length);
        if (src == null) return null;
        float scale = Math.min(1f, 800f / src.getWidth());
        Bitmap out = Bitmap.createScaledBitmap(src, Math.max(1, Math.round(src.getWidth() * scale)),
                Math.max(1, Math.round(src.getHeight() * scale)), true);
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        out.compress(Bitmap.CompressFormat.JPEG, 80, bytes);
        if (out != src) out.recycle();
        src.recycle();
        return bytes.toByteArray();
    }

    private static Bitmap scaled(byte[] data, float scale) {
        Bitmap src = BitmapFactory.decodeByteArray(data, 0, data.length);
        if (src == null) return null;
        Bitmap out = Bitmap.createScaledBitmap(src, Math.max(1, Math.round(src.getWidth() * scale)),
                Math.max(1, Math.round(src.getHeight() * scale)), true);
        if (out != src) src.recycle();
        return out;
    }

    // ---- 공통 ----

    private interface Renderer {
        Bitmap render();
    }

    /** key 가 바뀌었을 때만 그려 보낸다. key 가 null 이면 이전에 보낸 그림을 지운다. */
    private void publish(String name, String key, Renderer renderer) {
        String prev = lastKey.get(name);
        if (key == null) {
            if (prev != null || !lastKey.containsKey(name)) {
                lastKey.put(name, null);
                if (prev != null) client.sendImage(name, Cnv2.frame(Cnv2.TYPE_CLEAR, Cnv2.FORMAT_PNG, sequence++, null, 0, 0));
            }
            return;
        }
        if (key.equals(prev)) return;
        long now = SystemClock.elapsedRealtime();
        Long at = lastAt.get(name);
        if (at != null && now - at < MIN_INTERVAL_MS) return;   // 다음 틱에 다시 본다
        Bitmap bmp = null;
        try {
            bmp = renderer == null ? null : renderer.render();
            if (bmp == null) {
                publish(name, null, null);
                return;
            }
            ByteArrayOutputStream png = new ByteArrayOutputStream(16384);
            bmp.compress(Bitmap.CompressFormat.PNG, 100, png);
            client.sendImage(name, Cnv2.frame(Cnv2.TYPE_IMAGE, Cnv2.FORMAT_PNG, sequence++,
                    png.toByteArray(), bmp.getWidth(), bmp.getHeight()));
            lastKey.put(name, key);
            lastAt.put(name, now);
        } catch (Throwable error) {
            TmapHudLog.ex("render " + name, error);
            lastKey.put(name, key);   // 같은 값으로 매 틱 실패하지 않게
            lastAt.put(name, now);
        } finally {
            if (bmp != null) bmp.recycle();
        }
    }

    /** 이미 PNG/JPEG 인 바이트를 그대로 보낸다(서버가 서명으로 형식을 고른다). */
    private void publishBytes(String name, String key, byte[] image) {
        if (key.equals(lastKey.get(name))) return;
        boolean png = image.length > 4 && image[0] == (byte) 0x89 && image[1] == 'P';
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(image, 0, image.length, bounds);
        client.sendImage(name, Cnv2.frame(Cnv2.TYPE_IMAGE, png ? Cnv2.FORMAT_PNG : Cnv2.FORMAT_JPEG,
                sequence++, image, Math.max(0, bounds.outWidth), Math.max(0, bounds.outHeight)));
        lastKey.put(name, key);
        lastAt.put(name, SystemClock.elapsedRealtime());
    }

    private boolean prepare() {
        if (res != null) return true;
        Context ctx = context;
        if (ctx == null) return false;
        res = ctx.getResources();
        pkg = ctx.getPackageName();
        tbtTheme = theme("NavigationTbtIcon.Top");
        laneTheme = theme("NavigationLaneBubbleMarkerIcon.Night");
        laneSuggestedTheme = theme("NavigationLaneBubbleMarkerIcon.Night.Suggested");
        if (!resourcesLogged) {
            resourcesLogged = true;
            TmapHudLog.line("image resources: tbt=" + id("navigation_tbt_arrow_01_icon", "drawable")
                    + " lane=" + id("navigation_lane_laneguid_1_1", "drawable")
                    + " safety=" + id("c_01", "drawable")
                    + " styles=" + id("NavigationTbtIcon.Top", "style") + "/"
                    + id("NavigationLaneBubbleMarkerIcon.Night", "style"));
        }
        return true;
    }

    private int id(String name, String type) {
        int id = res.getIdentifier(name, type, pkg);
        if (id == 0 && name.indexOf('.') >= 0) id = res.getIdentifier(name.replace('.', '_'), type, pkg);
        return id;
    }

    private Resources.Theme theme(String style) {
        Resources.Theme theme = res.newTheme();
        int id = id(style, "style");
        if (id != 0) theme.applyStyle(id, true);
        else TmapHudLog.line("style missing: " + style);
        return theme;
    }

    private boolean draw(Canvas c, String name, Resources.Theme theme, int left, int top, int w, int h) {
        if (name == null) return false;
        int id = id(name, "drawable");
        if (id == 0) {
            TmapHudLog.status("drawable missing: " + name);
            return false;
        }
        try {
            Drawable d = res.getDrawable(id, theme);
            d.setBounds(left, top, left + w, top + h);
            d.draw(c);
            return true;
        } catch (Throwable error) {
            TmapHudLog.ex("drawable " + name, error);
            return false;
        }
    }

    private String ellipsize(String value, float width) {
        String s = value == null ? "" : value;
        if (text.measureText(s) <= width) return s;
        while (s.length() > 1 && text.measureText(s + "…") > width) s = s.substring(0, s.length() - 1);
        return s + "…";
    }
}
