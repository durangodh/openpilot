package ai.comma.remotehud;

import android.graphics.Bitmap;
import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PorterDuff;
import android.graphics.PorterDuffXfermode;
import android.graphics.RadialGradient;
import android.graphics.Shader;

import java.util.Random;

/**
 * 상단 밴드에 깔 하늘 배경.
 *
 * WeatherService 의 아이콘 분류(7종) × 낮/밤 에 구름량(cloud_cover %)을
 * 곱해 만든다. 상태가 바뀔 때만 비트맵을 새로 그리고, 매 프레임은
 * 그려둔 비트맵을 한 번 얹기만 한다.
 *
 * 속도 숫자가 지나는 구간에는 빗줄기·눈송이를 뿌리지 않는다. 안 그러면
 * 글자 위에 줄이 겹쳐 읽기 나빠진다.
 */
final class SkyBackground {

    /** 완성된 배경과, 그 위에 얹을 글자를 밝게 할지 여부. */
    static final class Sky {
        final Bitmap bitmap;
        final boolean lightInk;

        Sky(Bitmap bitmap, boolean lightInk) {
            this.bitmap = bitmap;
            this.lightInk = lightInk;
        }
    }

    /** 글자를 밝게 뒤집는 평균 휘도 기준. */
    private static final float INK_FLIP_LUMA = 150f;
    /** 빗줄기·눈송이를 비울 속도 숫자 영역(패널 좌표). */
    private static final float CLEAR_LEFT = 356f;
    private static final float CLEAR_RIGHT = 596f;
    private static final float CLEAR_TOP = 18f;
    private static final float CLEAR_BOTTOM = 130f;
    /** 주행 화면 위로 내려오는 곡선형 하늘 페이드 깊이. */
    private static final int CURVE_FADE_DEPTH = 34;

    private static Sky cached;
    private static long cachedKey = Long.MIN_VALUE;

    private SkyBackground() {
    }

    /**
     * @param cloudPct 구름량 0~100. 음수면 아이콘 분류로 대신 정한다.
     * @return 배경. 그릴 수 없으면 null.
     */
    static Sky get(int icon, boolean day, int cloudPct, int width, int height) {
        if (icon == WeatherService.ICON_NONE || width <= 0 || height <= 0) {
            return null;
        }
        int quantized = cloudPct < 0 ? -1 : Math.min(100, cloudPct / 10 * 10);
        int bitmapHeight = height + CURVE_FADE_DEPTH;
        long key = ((long) icon << 56) | ((day ? 1L : 0L) << 55)
                | ((long) (quantized + 1) << 48)
                | ((long) (width & 0xFFFF) << 16) | (height & 0xFFFFL);
        if (cached != null && key == cachedKey
                && cached.bitmap != null && !cached.bitmap.isRecycled()
                && cached.bitmap.getWidth() == width
                && cached.bitmap.getHeight() == bitmapHeight) {
            return cached;
        }
        Sky sky = render(icon, day, quantized, width, height);
        if (sky != null) {
            if (cached != null && cached.bitmap != null && !cached.bitmap.isRecycled()
                    && cached.bitmap != sky.bitmap) {
                cached.bitmap.recycle();
            }
            cached = sky;
            cachedKey = key;
        }
        return sky;
    }

    private static Sky render(int icon, boolean day, int cloudPct,
                              int width, int height) {
        int bitmapHeight = height + CURVE_FADE_DEPTH;
        Bitmap bitmap;
        try {
            bitmap = Bitmap.createBitmap(width, bitmapHeight, Bitmap.Config.ARGB_8888);
        } catch (Throwable t) {
            return null;
        }
        Canvas c = new Canvas(bitmap);
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        int[] band = gradientFor(icon, day);
        p.setShader(new LinearGradient(0f, 0f, 0f, height, band[0], band[1],
                Shader.TileMode.CLAMP));
        c.drawRect(0f, 0f, width, bitmapHeight, p);
        p.setShader(null);

        boolean clearish = icon == WeatherService.ICON_CLEAR
                || icon == WeatherService.ICON_FEW;
        if (clearish && !day) {
            drawStars(c, p, width, height, icon == WeatherService.ICON_CLEAR ? 40 : 26);
        }
        if (clearish) {
            // 해·달은 오른쪽 위. 외기온 글자와 겹치지 않게 은은하게만.
            float gx = width * 0.80f;
            float gy = height * 0.14f;
            float gr = clearish && icon == WeatherService.ICON_CLEAR
                    ? height * 0.62f : height * 0.52f;
            int glow = day ? Color.argb(190, 255, 244, 205)
                    : Color.argb(150, 200, 214, 240);
            p.setShader(new RadialGradient(gx, gy, gr, glow, Color.TRANSPARENT,
                    Shader.TileMode.CLAMP));
            c.drawCircle(gx, gy, gr, p);
            p.setShader(null);
        }

        int count = cloudCount(icon, cloudPct);
        // 안개 속에서는 구름 윤곽이 보이지 않는다. 안개층이 대신 질감을 만든다.
        if (count > 0 && icon != WeatherService.ICON_FOG) {
            drawClouds(c, p, width, height, count, cloudColor(icon, day),
                    seedFor(icon));
        }

        if (icon == WeatherService.ICON_FOG) {
            drawFog(c, p, width, height, day ? Color.rgb(226, 229, 231) : Color.rgb(104, 110, 120));
        } else if (icon == WeatherService.ICON_RAIN) {
            drawStreaks(c, p, width, height, 200,
                    day ? Color.argb(160, 232, 240, 248) : Color.argb(140, 170, 196, 226));
        } else if (icon == WeatherService.ICON_SNOW) {
            // 낮 하늘이 밝은 회색이라 눈송이는 거의 흰색으로, 더 크고 많게 해야 보인다.
            drawSnow(c, p, width, height, day ? 110 : 65,
                    day ? Color.argb(240, 252, 253, 255)
                            : Color.argb(210, 236, 242, 248), day ? 1.4f : 1.0f);
        } else if (icon == WeatherService.ICON_THUNDER) {
            drawStreaks(c, p, width, height, 110,
                    day ? Color.argb(90, 200, 214, 232) : Color.argb(110, 176, 196, 222));
            drawBolt(c, p, width * 0.26f, height);
        }

        // 투명 마스크를 씌우기 전에 색 자체의 휘도를 잰다. 투명 영역의 RGB 0이
        // 평균에 섞이면 밝은 낮 하늘에서도 글자를 흰색으로 잘못 뒤집을 수 있다.
        boolean lightInk = luminance(bitmap) < INK_FLIP_LUMA;
        applyCurvedBottomFade(bitmap, width, height);
        return new Sky(bitmap, lightInk);
    }

    /**
     * 직선이던 하늘 밴드 하단을 완만한 곡선으로 자르고, 같은 곡선을 아래로
     * 조금씩 내려 그린 알파 마스크로 주행 화면과 자연스럽게 섞는다.
     */
    private static void applyCurvedBottomFade(Bitmap bitmap, int width, int horizon) {
        Bitmap mask = null;
        try {
            mask = Bitmap.createBitmap(width, bitmap.getHeight(), Bitmap.Config.ARGB_8888);
            Canvas mc = new Canvas(mask);
            Paint mp = new Paint(Paint.ANTI_ALIAS_FLAG);
            mp.setStyle(Paint.Style.FILL);

            // 큰(아래쪽) 곡선부터 낮은 알파로 겹치면 경계와 평행한 부드러운
            // 그라데이션이 만들어진다. 상태 변경 때만 계산하므로 프레임 비용은 없다.
            int[] offsets = {34, 27, 21, 16, 11, 7, 4, 2, 0};
            int[] alphas = {12, 18, 26, 38, 54, 76, 105, 145, 255};
            for (int i = 0; i < offsets.length; i++) {
                mp.setColor(Color.argb(alphas[i], 255, 255, 255));
                mc.drawPath(curvedHorizon(width, horizon, offsets[i]), mp);
            }

            Paint apply = new Paint(Paint.ANTI_ALIAS_FLAG);
            apply.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.DST_IN));
            new Canvas(bitmap).drawBitmap(mask, 0f, 0f, apply);
            apply.setXfermode(null);
        } catch (Throwable ignored) {
            // 마스크 생성 실패 시 기존 직선 하늘이라도 계속 표시한다.
        } finally {
            if (mask != null && !mask.isRecycled()) {
                mask.recycle();
            }
        }
    }

    /** 화면 폭 전체를 잇는 저주파 곡선. 작은 굴곡만 사용해 HUD가 출렁이지 않게 한다. */
    private static Path curvedHorizon(int width, int horizon, int offset) {
        float w = width;
        float y = horizon + offset;
        Path path = new Path();
        path.moveTo(0f, 0f);
        path.lineTo(0f, y - 5f);
        path.cubicTo(w * 0.08f, y - 13f, w * 0.18f, y + 8f,
                w * 0.29f, y - 3f);
        path.cubicTo(w * 0.38f, y - 12f, w * 0.47f, y + 10f,
                w * 0.58f, y + 1f);
        path.cubicTo(w * 0.68f, y + 12f, w * 0.77f, y - 11f,
                w * 0.86f, y - 3f);
        path.cubicTo(w * 0.92f, y + 2f, w * 0.97f, y + 9f,
                w, y + 2f);
        path.lineTo(w, 0f);
        path.close();
        return path;
    }

    private static int[] gradientFor(int icon, boolean day) {
        switch (icon) {
            case WeatherService.ICON_CLEAR:
                return day ? new int[]{Color.rgb(38, 108, 190), Color.rgb(150, 196, 232)}
                        : new int[]{Color.rgb(10, 20, 48), Color.rgb(30, 46, 84)};
            case WeatherService.ICON_FEW:
                return day ? new int[]{Color.rgb(44, 116, 196), Color.rgb(162, 202, 234)}
                        : new int[]{Color.rgb(12, 24, 54), Color.rgb(34, 52, 92)};
            case WeatherService.ICON_FOG:
                return day ? new int[]{Color.rgb(176, 181, 184), Color.rgb(214, 217, 219)}
                        : new int[]{Color.rgb(44, 48, 55), Color.rgb(78, 83, 90)};
            case WeatherService.ICON_RAIN:
                return day ? new int[]{Color.rgb(78, 90, 102), Color.rgb(140, 152, 163)}
                        : new int[]{Color.rgb(16, 24, 34), Color.rgb(44, 54, 68)};
            case WeatherService.ICON_SNOW:
                return day ? new int[]{Color.rgb(150, 160, 170), Color.rgb(214, 221, 227)}
                        : new int[]{Color.rgb(30, 38, 52), Color.rgb(66, 76, 92)};
            case WeatherService.ICON_THUNDER:
                return day ? new int[]{Color.rgb(52, 58, 70), Color.rgb(104, 112, 126)}
                        : new int[]{Color.rgb(10, 14, 24), Color.rgb(34, 40, 54)};
            default:
                return day ? new int[]{Color.rgb(132, 142, 152), Color.rgb(198, 205, 211)}
                        : new int[]{Color.rgb(28, 34, 44), Color.rgb(58, 66, 78)};
        }
    }

    private static int cloudColor(int icon, boolean day) {
        switch (icon) {
            case WeatherService.ICON_FEW:
                return day ? Color.rgb(255, 255, 255) : Color.rgb(150, 164, 190);
            case WeatherService.ICON_FOG:
                return day ? Color.rgb(250, 250, 250) : Color.rgb(120, 126, 136);
            case WeatherService.ICON_RAIN:
                return day ? Color.rgb(206, 214, 222) : Color.rgb(78, 88, 104);
            case WeatherService.ICON_SNOW:
                return day ? Color.rgb(245, 248, 251) : Color.rgb(104, 114, 130);
            case WeatherService.ICON_THUNDER:
                return day ? Color.rgb(150, 158, 172) : Color.rgb(62, 70, 86);
            default:
                return day ? Color.rgb(238, 241, 245) : Color.rgb(96, 106, 122);
        }
    }

    /**
     * 구름 덩이 수. cloud_cover 가 오면 0~100% 를 그대로 쓰고(연속),
     * 없으면 아이콘 분류로 대신 정한다.
     */
    private static int cloudCount(int icon, int cloudPct) {
        if (icon == WeatherService.ICON_CLEAR) {
            return cloudPct <= 0 ? 0 : Math.min(6, cloudPct / 12);
        }
        int floor;
        switch (icon) {
            case WeatherService.ICON_FEW:
                floor = 5;
                break;
            case WeatherService.ICON_FOG:
                floor = 16;
                break;
            case WeatherService.ICON_RAIN:
            case WeatherService.ICON_SNOW:
                floor = 18;
                break;
            case WeatherService.ICON_THUNDER:
                floor = 20;
                break;
            default:
                floor = 14;
                break;
        }
        if (cloudPct < 0) {
            return icon == WeatherService.ICON_FEW ? 7 : floor + 6;
        }
        return Math.max(floor, 2 + Math.round(cloudPct * 0.28f));
    }

    private static int seedFor(int icon) {
        return 11 + icon * 10;
    }

    /**
     * 자연스러운 구름. 한 덩이는 큰 몸통 봉우리 몇 개 위에 작은 봉우리를 가장자리를
     * 따라 여러 겹 붙여 윤곽을 불규칙하게 만들고, 봉우리마다 밝기를 조금씩 달리한다.
     * 아래는 그늘색, 위쪽은 은은한 빛, 밑면은 평평하되 부드럽게 흐린다.
     * 흐린 날(덩이가 많을 때)은 넓고 납작한 층구름으로 그린다. 덩이마다 별도
     * 레이어에 그려 겹친 부분이 진해지지 않게 하고, 멀리(위쪽) 있는 구름일수록
     * 작고 옅다. 날씨가 바뀔 때만 다시 그리므로 프레임 비용은 없다.
     */
    private static void drawClouds(Canvas c, Paint p, int w, int h, int count,
                                   int color, int seed) {
        Random rnd = new Random(seed);
        boolean layered = count >= 14;
        int clusters = layered ? Math.max(5, Math.round(count * 0.40f))
                : Math.max(2, Math.round(count * 0.55f));
        float[] depths = new float[clusters];
        for (int i = 0; i < clusters; i++) {
            depths[i] = rnd.nextFloat();
        }
        java.util.Arrays.sort(depths);   // 먼 구름부터 그려 가까운 구름이 위에 온다
        int[] slots = new int[clusters];  // 가로로 고르게 흩어 놓는다
        for (int i = 0; i < clusters; i++) {
            slots[i] = i;
        }
        for (int i = clusters - 1; i > 0; i--) {
            int k = rnd.nextInt(i + 1);
            int t = slots[i];
            slots[i] = slots[k];
            slots[k] = t;
        }

        boolean bright = luma(color) > 200f;
        int shade = mix(color, Color.rgb(40, 48, 64), bright ? 0.24f : 0.32f);
        int light = mix(color, Color.WHITE, bright ? 0.40f : 0.14f);
        BlurMaskFilter fine = new BlurMaskFilter(Math.max(3f, h * 0.020f),
                BlurMaskFilter.Blur.NORMAL);
        BlurMaskFilter soft = new BlurMaskFilter(Math.max(5f, h * 0.040f),
                BlurMaskFilter.Blur.NORMAL);
        BlurMaskFilter haze = new BlurMaskFilter(Math.max(8f, h * 0.085f),
                BlurMaskFilter.Blur.NORMAL);
        p.setStyle(Paint.Style.FILL);

        if (layered) {
            // 하늘 전체에 옅게 깔린 구름층
            p.setMaskFilter(haze);
            p.setColor(color);
            for (int k = 0; k < 5; k++) {
                float y = h * (0.15f + 0.16f * k);
                p.setAlpha(45 + rnd.nextInt(30));
                c.drawOval(-w * 0.1f + rnd.nextFloat() * w * 0.3f, y - h * 0.07f,
                        w * 0.8f + rnd.nextFloat() * w * 0.3f, y + h * 0.07f, p);
            }
        }

        float[] px = new float[40];
        float[] py = new float[40];
        float[] pr = new float[40];
        int[] pc = new int[40];
        for (int i = 0; i < clusters; i++) {
            float depth = depths[i];
            float cw = layered ? w * (0.12f + depth * 0.10f + rnd.nextFloat() * 0.08f)
                    : w * (0.06f + depth * 0.08f + rnd.nextFloat() * 0.06f);
            float ch = cw * (layered ? 0.13f + rnd.nextFloat() * 0.06f
                    : 0.20f + rnd.nextFloat() * 0.09f);
            float cx = (slots[i] + 0.15f + rnd.nextFloat() * 0.7f) / clusters * w;
            float base = h * (0.20f + depth * 0.50f) + ch * 0.30f;   // 평평한 밑면
            int alpha = Math.min(215, (layered ? 80 : 75) + Math.round(depth * 85f)
                    + rnd.nextInt(30));

            // 덩이 옆으로 얇게 퍼진 새털구름 띠
            if (rnd.nextFloat() < 0.5f) {
                float sx = cx + (rnd.nextFloat() - 0.5f) * cw;
                float sy = base - ch * (0.2f + rnd.nextFloat() * 0.6f);
                p.setMaskFilter(haze);
                p.setColor(color);
                p.setAlpha(Math.round(alpha * 0.30f));
                c.drawOval(sx - cw * 0.95f, sy - ch * 0.14f, sx + cw * 0.95f, sy + ch * 0.14f, p);
            }

            // 큰 몸통 봉우리, 그다음 가장자리를 따라 붙는 작은 봉우리
            int body = 4 + rnd.nextInt(3);
            int n = 0;
            for (int j = 0; j < body; j++) {
                float t = -0.85f + 1.7f * (j + rnd.nextFloat()) / body;
                float dome = (float) Math.pow(Math.max(0f, 1f - t * t), 0.7f);
                pr[n] = ch * (0.40f + 0.55f * dome) * (0.80f + rnd.nextFloat() * 0.40f);
                px[n] = cx + t * cw * 0.48f;
                py[n] = base - pr[n] * (0.55f + rnd.nextFloat() * 0.30f);
                pc[n] = mix(color, shade, rnd.nextFloat() * 0.12f);
                n++;
            }
            int detail = Math.min(40 - n, 10 + rnd.nextInt(10));
            for (int j = 0; j < detail; j++) {
                int parent = rnd.nextInt(n);
                double ang = Math.toRadians(-175 + rnd.nextFloat() * 170);   // 윗쪽 반원
                float rr = pr[parent] * (0.22f + rnd.nextFloat() * 0.30f);
                float dist = pr[parent] * (0.70f + rnd.nextFloat() * 0.25f);
                px[n] = px[parent] + (float) Math.cos(ang) * dist;
                py[n] = Math.min(base - rr * 0.6f, py[parent] + (float) Math.sin(ang) * dist);
                pr[n] = rr;
                pc[n] = mix(color, rnd.nextBoolean() ? shade : light, rnd.nextFloat() * 0.18f);
                n++;
            }

            c.saveLayerAlpha(cx - cw * 1.2f, base - ch * 3.6f, cx + cw * 1.2f, base + ch * 1.6f, alpha);

            // 밑에 깔리는 옅은 안개
            p.setMaskFilter(haze);
            p.setColor(color);
            p.setAlpha(75);
            c.drawOval(cx - cw * 0.85f, base - ch * 0.60f, cx + cw * 0.85f, base + ch * 0.30f, p);

            // 그늘(아래쪽) → 몸통 → 은은한 빛(위쪽) 순서
            p.setMaskFilter(soft);
            p.setColor(shade);
            for (int j = 0; j < n; j++) {
                c.drawCircle(px[j], py[j] + pr[j] * 0.25f, pr[j], p);
            }
            for (int j = 0; j < n; j++) {
                p.setMaskFilter(j < body ? soft : fine);
                p.setColor(pc[j]);
                c.drawCircle(px[j], py[j], pr[j] * 0.90f, p);
            }
            p.setMaskFilter(soft);
            p.setColor(light);
            for (int j = 0; j < n; j++) {
                p.setAlpha(60 + rnd.nextInt(60));
                c.drawCircle(px[j] - pr[j] * 0.10f, py[j] - pr[j] * 0.30f, pr[j] * 0.50f, p);
            }

            // 밑면을 평평하되 부드럽게 지운다(칼로 자른 직선이 보이지 않게)
            p.setMaskFilter(null);
            p.setAlpha(255);
            p.setShader(new LinearGradient(0f, base - ch * 0.15f, 0f, base + ch * 0.60f,
                    Color.TRANSPARENT, Color.BLACK, Shader.TileMode.CLAMP));
            p.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.DST_OUT));
            c.drawRect(cx - cw * 1.2f, base - ch * 0.15f, cx + cw * 1.2f, base + ch * 1.6f, p);
            p.setXfermode(null);
            p.setShader(null);
            c.restore();
        }
        p.setMaskFilter(null);
        p.setAlpha(255);
    }

    private static float luma(int color) {
        return 0.299f * Color.red(color) + 0.587f * Color.green(color) + 0.114f * Color.blue(color);
    }

    private static int mix(int a, int b, float t) {
        return Color.rgb(
                Math.round(Color.red(a) + (Color.red(b) - Color.red(a)) * t),
                Math.round(Color.green(a) + (Color.green(b) - Color.green(a)) * t),
                Math.round(Color.blue(a) + (Color.blue(b) - Color.blue(a)) * t));
    }

    private static void drawStars(Canvas c, Paint p, int w, int h, int count) {
        Random rnd = new Random(3);
        p.setStyle(Paint.Style.FILL);
        for (int i = 0; i < count; i++) {
            float x = rnd.nextFloat() * w;
            float y = rnd.nextFloat() * h * 0.70f;
            p.setColor(Color.WHITE);
            p.setAlpha(90 + rnd.nextInt(120));
            c.drawCircle(x, y, 0.7f + rnd.nextFloat() * 1.0f, p);
        }
        p.setAlpha(255);
    }

    /**
     * 빗줄기. 먼 비(가늘고 짧고 옅음)·중간·가까운 비(굵고 길고 약간 흐림) 세 겹을
     * 같은 바람 방향으로 기울여 뿌리고, 줄기마다 꼬리는 투명→머리는 진하게 해서
     * 떨어지는 느낌을 준다. 구름 아래쪽일수록 촘촘하고, 군데군데 흐린 빗줄기
     * 기둥(비 커튼)을 깐다.
     */
    private static void drawStreaks(Canvas c, Paint p, int w, int h, int count,
                                    int color) {
        Random rnd = new Random(5);
        int baseAlpha = Color.alpha(color);
        int rgb = color | 0xFF000000;
        float slant = -0.20f;   // 바람: 오른쪽 위 → 왼쪽 아래

        // 비 커튼: 구름 밑으로 늘어진 옅은 기둥
        p.setStyle(Paint.Style.FILL);
        p.setMaskFilter(new BlurMaskFilter(Math.max(10f, h * 0.10f), BlurMaskFilter.Blur.NORMAL));
        for (int i = 0; i < 4; i++) {
            float x = w * (0.08f + 0.24f * i) + rnd.nextFloat() * w * 0.10f;
            float top = h * (0.35f + rnd.nextFloat() * 0.15f);
            p.setColor(rgb);
            p.setAlpha(Math.round(baseAlpha * 0.30f));
            Path shaft = new Path();
            float sw = w * (0.05f + rnd.nextFloat() * 0.05f);
            shaft.moveTo(x - sw, top);
            shaft.lineTo(x + sw, top);
            shaft.lineTo(x + sw + slant * h, h * 1.05f);
            shaft.lineTo(x - sw * 1.4f + slant * h, h * 1.05f);
            shaft.close();
            c.drawPath(shaft, p);
        }
        p.setMaskFilter(null);

        p.setStyle(Paint.Style.STROKE);
        p.setStrokeCap(Paint.Cap.ROUND);
        BlurMaskFilter nearBlur = new BlurMaskFilter(1.2f, BlurMaskFilter.Blur.NORMAL);
        for (int i = 0; i < count; i++) {
            float layer = rnd.nextFloat();          // 0 먼 비 ~ 1 가까운 비
            boolean near = layer > 0.85f;
            float len = near ? 26f + rnd.nextFloat() * 18f
                    : layer > 0.5f ? 14f + rnd.nextFloat() * 10f : 7f + rnd.nextFloat() * 7f;
            float x = rnd.nextFloat() * (w + h * 0.3f);
            // 위쪽(구름 속)은 드물게: y 를 아래로 몰아준다
            float y = h * (0.12f + 0.88f * (float) Math.sqrt(rnd.nextFloat())) - len;
            if (blocked(x, y) || blocked(x + slant * len, y + len)) {
                continue;
            }
            float strength = near ? 0.95f : layer > 0.5f ? 0.70f : 0.42f;
            int a = Math.round(baseAlpha * strength * (0.75f + rnd.nextFloat() * 0.25f));
            float x2 = x + slant * len * (0.9f + rnd.nextFloat() * 0.2f);
            float y2 = y + len;
            p.setStrokeWidth(near ? 2.2f : layer > 0.5f ? 1.4f : 1.0f);
            p.setMaskFilter(near ? nearBlur : null);
            p.setShader(new LinearGradient(x, y, x2, y2,
                    rgb & 0x00FFFFFF, (Math.min(255, a) << 24) | (rgb & 0x00FFFFFF),
                    Shader.TileMode.CLAMP));
            c.drawLine(x, y, x2, y2, p);
        }
        p.setShader(null);
        p.setMaskFilter(null);
        p.setStyle(Paint.Style.FILL);
        p.setAlpha(255);
    }

    /**
     * 눈. 먼 눈(작고 또렷)·중간(살짝 흐림)·가까운 눈(크고 흐린 보케) 세 겹.
     * 송이마다 크기·투명도를 달리하고, 가까운 송이는 드물게만 둔다.
     */
    private static void drawSnow(Canvas c, Paint p, int w, int h, int count,
                                 int color, float sizeScale) {
        Random rnd = new Random(9);
        int baseAlpha = Color.alpha(color);
        p.setStyle(Paint.Style.FILL);
        BlurMaskFilter midBlur = new BlurMaskFilter(1.0f, BlurMaskFilter.Blur.NORMAL);
        BlurMaskFilter nearBlur = new BlurMaskFilter(3.2f, BlurMaskFilter.Blur.NORMAL);
        int total = Math.round(count * 1.6f);
        for (int i = 0; i < total; i++) {
            float layer = rnd.nextFloat();
            float x = rnd.nextFloat() * w;
            float y = h * (0.06f + 0.94f * (float) Math.sqrt(rnd.nextFloat()));
            if (blocked(x, y)) {
                continue;
            }
            float r;
            float strength;
            if (layer > 0.93f) {          // 가까운 큰 송이(보케)
                r = 3.2f + rnd.nextFloat() * 2.4f;
                strength = 0.55f;
                p.setMaskFilter(nearBlur);
            } else if (layer > 0.60f) {   // 중간
                r = 1.5f + rnd.nextFloat() * 1.0f;
                strength = 0.85f;
                p.setMaskFilter(midBlur);
            } else {                      // 먼 작은 송이
                r = 0.7f + rnd.nextFloat() * 0.7f;
                strength = 0.65f;
                p.setMaskFilter(null);
            }
            p.setColor(color);
            p.setAlpha(Math.round(baseAlpha * strength * (0.6f + rnd.nextFloat() * 0.4f)));
            c.drawCircle(x, y, r * sizeScale, p);
        }
        p.setMaskFilter(null);
        p.setAlpha(255);
    }

    /**
     * 안개. 아래로 갈수록 짙어지는 뿌연 막 위에, 가로로 길게 퍼진 흐릿한 안개
     * 띠 여러 겹과 얇은 안개 자락을 겹쳐 고르지 않은 실제 안개처럼 보이게 한다.
     */
    private static void drawFog(Canvas c, Paint p, int w, int h, int color) {
        Random rnd = new Random(23);
        int rgb = color & 0x00FFFFFF;
        p.setStyle(Paint.Style.FILL);

        // 아래로 갈수록 짙어지는 막
        p.setShader(new LinearGradient(0f, 0f, 0f, h,
                (40 << 24) | rgb, (175 << 24) | rgb, Shader.TileMode.CLAMP));
        c.drawRect(0f, 0f, w, h * 1.2f, p);
        p.setShader(null);

        // 가로로 길게 퍼진 안개 띠(먼 띠는 위쪽·옅게, 가까운 띠는 아래쪽·짙게).
        // 띠마다 조금 밝거나 어두운 톤을 섞어 고르지 않게 한다.
        int lightTone = mix(color, Color.WHITE, 0.35f);
        int darkTone = mix(color, Color.rgb(60, 66, 76), 0.22f);
        p.setMaskFilter(new BlurMaskFilter(Math.max(12f, h * 0.12f), BlurMaskFilter.Blur.NORMAL));
        for (int i = 0; i < 9; i++) {
            float depth = (i + rnd.nextFloat()) / 9f;
            float y = h * (0.25f + depth * 0.75f);
            float bw = w * (0.45f + rnd.nextFloat() * 0.55f);
            float bh = h * (0.08f + depth * 0.12f + rnd.nextFloat() * 0.06f);
            float x = -bw * 0.3f + rnd.nextFloat() * (w + bw * 0.6f);
            p.setColor(i % 3 == 1 ? darkTone : lightTone);
            p.setAlpha(60 + Math.round(depth * 70f) + rnd.nextInt(30));
            c.drawOval(x - bw * 0.5f, y - bh * 0.5f, x + bw * 0.5f, y + bh * 0.5f, p);
        }

        // 띠를 따라 뭉게뭉게 흘러가는 안개 덩이(질감)
        p.setMaskFilter(new BlurMaskFilter(Math.max(9f, h * 0.075f), BlurMaskFilter.Blur.NORMAL));
        for (int i = 0; i < 26; i++) {
            float y = h * (0.30f + 0.70f * (float) Math.sqrt(rnd.nextFloat()));
            float r = h * (0.07f + rnd.nextFloat() * 0.10f);
            float x = rnd.nextFloat() * w;
            p.setColor(rnd.nextFloat() < 0.65f ? lightTone : darkTone);
            p.setAlpha(40 + rnd.nextInt(50));
            c.drawOval(x - r * 2.2f, y - r * 0.6f, x + r * 2.2f, y + r * 0.6f, p);
        }

        // 얇은 안개 자락
        p.setMaskFilter(new BlurMaskFilter(Math.max(5f, h * 0.035f), BlurMaskFilter.Blur.NORMAL));
        for (int i = 0; i < 7; i++) {
            float y = h * (0.30f + rnd.nextFloat() * 0.60f);
            float bw = w * (0.20f + rnd.nextFloat() * 0.30f);
            float x = rnd.nextFloat() * w;
            p.setColor(color);
            p.setAlpha(35 + rnd.nextInt(30));
            c.drawOval(x - bw * 0.5f, y - h * 0.018f, x + bw * 0.5f, y + h * 0.018f, p);
        }
        p.setMaskFilter(null);
        p.setAlpha(255);
    }

    /** 속도 숫자 자리인지. 여기엔 아무것도 뿌리지 않는다. */
    private static boolean blocked(float x, float y) {
        return x > CLEAR_LEFT && x < CLEAR_RIGHT && y > CLEAR_TOP && y < CLEAR_BOTTOM;
    }

    /**
     * 번개. 구름 속에서 아래로 지그재그로 갈라지며 내려오는 줄기와 잔가지를
     * 바깥 번짐(넓고 흐림) → 안쪽 빛 → 흰 심 순서로 그리고, 시작점 둘레의
     * 구름을 은은하게 밝힌다. 속도 숫자 자리는 피한다.
     */
    private static void drawBolt(Canvas c, Paint p, float cx, float h) {
        Random rnd = new Random(17);
        // 구름 속 섬광
        p.setStyle(Paint.Style.FILL);
        p.setShader(new RadialGradient(cx, h * 0.12f, h * 0.55f,
                Color.argb(90, 214, 222, 255), Color.TRANSPARENT, Shader.TileMode.CLAMP));
        c.drawCircle(cx, h * 0.12f, h * 0.55f, p);
        p.setShader(null);

        Path main = new Path();
        Path branches = new Path();
        float x = cx;
        float y = h * 0.10f;
        main.moveTo(x, y);
        float maxX = CLEAR_LEFT - 6f;
        while (y < h * 0.92f) {
            float ny = y + h * (0.04f + rnd.nextFloat() * 0.05f);
            float nx = Math.min(maxX, x + (rnd.nextFloat() - 0.62f) * h * 0.09f);
            main.lineTo(nx, ny);
            if (rnd.nextFloat() < 0.28f && ny < h * 0.75f) {
                // 잔가지: 짧게 2~4 마디
                float bx = nx;
                float by = ny;
                branches.moveTo(bx, by);
                float dir = rnd.nextBoolean() ? 1f : -1f;
                int segs = 2 + rnd.nextInt(3);
                for (int k = 0; k < segs; k++) {
                    by += h * (0.03f + rnd.nextFloat() * 0.04f);
                    bx = Math.min(maxX, bx + dir * h * (0.02f + rnd.nextFloat() * 0.05f));
                    branches.lineTo(bx, by);
                }
            }
            x = nx;
            y = ny;
        }

        p.setStyle(Paint.Style.STROKE);
        p.setStrokeCap(Paint.Cap.ROUND);
        p.setStrokeJoin(Paint.Join.ROUND);
        p.setMaskFilter(new BlurMaskFilter(Math.max(6f, h * 0.05f), BlurMaskFilter.Blur.NORMAL));
        p.setColor(Color.argb(150, 170, 190, 255));
        p.setStrokeWidth(h * 0.035f);
        c.drawPath(main, p);
        p.setStrokeWidth(h * 0.018f);
        c.drawPath(branches, p);
        p.setMaskFilter(new BlurMaskFilter(2f, BlurMaskFilter.Blur.NORMAL));
        p.setColor(Color.argb(220, 214, 226, 255));
        p.setStrokeWidth(3.2f);
        c.drawPath(main, p);
        p.setStrokeWidth(1.6f);
        c.drawPath(branches, p);
        p.setMaskFilter(null);
        p.setColor(Color.argb(255, 255, 255, 255));
        p.setStrokeWidth(1.4f);
        c.drawPath(main, p);
        p.setStrokeWidth(0.8f);
        c.drawPath(branches, p);
        p.setStyle(Paint.Style.FILL);
    }

    /** 글자색 판단용 평균 휘도. 24x8 로 줄여서 잰다. */
    private static float luminance(Bitmap bitmap) {
        Bitmap small = null;
        try {
            small = Bitmap.createScaledBitmap(bitmap, 24, 8, true);
            int[] px = new int[24 * 8];
            small.getPixels(px, 0, 24, 0, 0, 24, 8);
            float sum = 0f;
            for (int value : px) {
                sum += 0.299f * Color.red(value) + 0.587f * Color.green(value)
                        + 0.114f * Color.blue(value);
            }
            return sum / px.length;
        } catch (Throwable t) {
            return 255f;
        } finally {
            if (small != null && !small.isRecycled()) {
                small.recycle();
            }
        }
    }
}
