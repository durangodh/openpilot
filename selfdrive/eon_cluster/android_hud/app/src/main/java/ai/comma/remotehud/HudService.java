package ai.comma.remotehud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.hardware.usb.UsbDevice;
import android.os.Build;
import android.os.IBinder;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.lang.reflect.Array;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.util.Arrays;
import java.util.Collection;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONArray;
import org.json.JSONObject;

public final class HudService extends Service {
    static final String ACTION_RESCAN_USB = "ai.comma.remotehud.RESCAN_USB";
    private static final String CHANNEL = "remote_hud";
    private static final int HEIGHT = 462;
    private static final int WIDTH = 1920;
    private static volatile long lastEonRxElapsed;
    private static volatile int lastJpegBytes;
    private static volatile long lastJpegSentElapsed;
    private static volatile boolean mapConnected;
    private static volatile float measuredFps;
    private static volatile boolean serviceRunning;
    private static volatile boolean usbConnected;
    private static volatile boolean usbError;
    private TurzxDisplay display;
    private Bitmap egoCar;
    private Thread mapThread;
    private Bitmap otherCar;
    private Thread receiverThread;
    private Thread renderThread;
    private boolean usbReceiverRegistered;

    // --- v0.13 ---
    static final String EXTRA_FROM_BOOT = "ai.comma.remotehud.FROM_BOOT";
    private static final long BOOT_START_DELAY_MS = 30000L;

    private final Handler starter = new Handler(Looper.getMainLooper());
    private PowerManager.WakeLock wakeLock;
    private volatile boolean workersStarted;

    private static volatile String lastEonAddress = "--";
    private static volatile String usbStatus = "미연결 · 1CBE:0092";
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicReference<JSONObject> state = new AtomicReference<>(new JSONObject());
    private final AtomicReference<Bitmap> mapFrame = new AtomicReference<>();
    private final AtomicReference<InetAddress> eonAddress = new AtomicReference<>();
    private final GeometryStabilizer geometry = new GeometryStabilizer(null);
    private final BroadcastReceiver usbReceiver = new BroadcastReceiver() { // from class: ai.comma.remotehud.HudService.1
        @Override
        public void onReceive(Context context, Intent intent) {
            String action = intent.getAction();
            if (TurzxDisplay.isTarget((UsbDevice) intent.getParcelableExtra("device"))) {
                if ("android.hardware.usb.action.USB_DEVICE_DETACHED".equals(action)) {
                    if (HudService.this.display != null) {
                        HudService.this.display.reset();
                    }
                    HudService.usbStatus = "분리됨 · 재연결 대기";
                    HudService.usbConnected = false;
                    HudService.usbError = false;
                    return;
                }
                if ("ai.comma.remotehud.USB_PERMISSION".equals(action)) {
                    boolean booleanExtra = intent.getBooleanExtra("permission", false);
                    if (HudService.this.display != null) {
                        HudService.this.display.reset();
                    }
                    HudService.usbStatus = booleanExtra ? "USB 권한 허용 · 연결 중" : "USB 권한 거부됨 · 재검색 필요";
                    HudService.usbConnected = false;
                    HudService.usbError = !booleanExtra;
                    return;
                }
                if (!"android.hardware.usb.action.USB_DEVICE_ATTACHED".equals(action)) {
                    return;
                }
                HudService.this.requestUsbRescan();
            }
        }
    };

    public static final class StatusSnapshot {
        final String eonAddress;
        final boolean eonConnected;
        final float fps;
        final int lastJpegBytes;
        final boolean mapConnected;
        final boolean running;
        final boolean usbConnected;
        final boolean usbError;
        final String usbStatus;

        StatusSnapshot(boolean z, boolean z2, String str, boolean z3, String str2, boolean z4, boolean z5, float f, int i) {
            this.running = z;
            this.eonConnected = z2;
            this.eonAddress = str;
            this.mapConnected = z3;
            this.usbStatus = str2;
            this.usbConnected = z4;
            this.usbError = z5;
            this.fps = f;
            this.lastJpegBytes = i;
        }
    }

    public static StatusSnapshot getStatusSnapshot() {
        long elapsedRealtime = SystemClock.elapsedRealtime();
        boolean z = serviceRunning && lastEonRxElapsed > 0 && elapsedRealtime - lastEonRxElapsed < 2000;
        boolean z2 = serviceRunning && lastJpegSentElapsed > 0 && elapsedRealtime - lastJpegSentElapsed < 2000;
        return new StatusSnapshot(serviceRunning, z, lastEonAddress, serviceRunning && mapConnected, usbStatus, serviceRunning && usbConnected, usbError, z2 ? measuredFps : 0.0f, z2 ? lastJpegBytes : 0);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        this.egoCar = BitmapFactory.decodeResource(getResources(), R.drawable.hud_ego_car);
        this.otherCar = BitmapFactory.decodeResource(getResources(), R.drawable.hud_other_car);
        ((NotificationManager) getSystemService("notification")).createNotificationChannel(new NotificationChannel(CHANNEL, "EON Remote HUD", 2));
        startForeground(72, new Notification.Builder(this, CHANNEL).setContentTitle("EON Remote HUD").setContentText("상태 화면을 열려면 누르세요").setSmallIcon(android.R.drawable.ic_menu_directions).setContentIntent(PendingIntent.getActivity(this, 0, new Intent(this, (Class<?>) MainActivity.class), 201326592)).setOngoing(true).build());
        IntentFilter intentFilter = new IntentFilter();
        intentFilter.addAction("android.hardware.usb.action.USB_DEVICE_ATTACHED");
        intentFilter.addAction("android.hardware.usb.action.USB_DEVICE_DETACHED");
        intentFilter.addAction("ai.comma.remotehud.USB_PERMISSION");
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(this.usbReceiver, intentFilter, 4);
        } else {
            registerReceiver(this.usbReceiver, intentFilter);
        }
        this.usbReceiverRegistered = true;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_RESCAN_USB.equals(intent.getAction()) && this.running.get()) {
            requestUsbRescan();
            return START_STICKY;
        }
        if (this.running.get()) {
            return START_STICKY;
        }

        this.running.set(true);
        serviceRunning = true;
        mapConnected = false;
        usbConnected = false;
        usbError = false;
        measuredFps = 0.0f;
        lastJpegBytes = 0;

        acquireWakeLock();

        boolean fromBoot = intent != null && intent.getBooleanExtra(EXTRA_FROM_BOOT, false);
        if (fromBoot) {
            usbStatus = "부팅 대기 " + (BOOT_START_DELAY_MS / 1000L) + "초";
            this.starter.postDelayed(new Runnable() {
                @Override
                public void run() {
                    startWorkers();
                }
            }, BOOT_START_DELAY_MS);
        } else {
            startWorkers();
        }

        return START_STICKY;
    }

    private void startWorkers() {
        if (!this.running.get() || this.workersStarted) {
            return;
        }
        this.workersStarted = true;
        usbStatus = "외부 HUD 검색 중";

        this.display = new TurzxDisplay(this);

        this.receiverThread = new Thread(new Runnable() {
            @Override
            public void run() {
                receiveLoop();
            }
        }, "hud-telemetry");
        this.mapThread = new Thread(new Runnable() {
            @Override
            public void run() {
                mapLoop();
            }
        }, "hud-tmap");
        this.renderThread = new Thread(new Runnable() {
            @Override
            public void run() {
                renderLoop();
            }
        }, "hud-render");

        this.receiverThread.start();
        this.mapThread.start();
        this.renderThread.start();
    }

    private void acquireWakeLock() {
        try {
            if (this.wakeLock == null) {
                PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
                this.wakeLock = pm.newWakeLock(
                        PowerManager.PARTIAL_WAKE_LOCK, "RemoteHUD::render");
                this.wakeLock.setReferenceCounted(false);
            }
            if (!this.wakeLock.isHeld()) {
                this.wakeLock.acquire();
            }
        } catch (Exception ignored) {
        }
    }

    private void releaseWakeLock() {
        try {
            if (this.wakeLock != null && this.wakeLock.isHeld()) {
                this.wakeLock.release();
            }
        } catch (Exception ignored) {
        }
        this.wakeLock = null;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        if (AppPrefs.isAutoStart(this)) {
            try {
                startForegroundService(new Intent(getApplicationContext(), HudService.class));
            } catch (Exception ignored) {
            }
        }
        super.onTaskRemoved(rootIntent);
    }

    public void requestUsbRescan() {
        if (this.display != null) {
            this.display.reset();
        }
        usbStatus = "외부 HUD 재검색 중";
        usbConnected = false;
        usbError = false;
        measuredFps = 0.0f;
        lastJpegBytes = 0;
    }

    public void receiveLoop() {
        while (this.running.get()) {
            try (DatagramSocket datagramSocket = new DatagramSocket(7210)) {
                datagramSocket.setBroadcast(true);
                datagramSocket.setSoTimeout(1000);
                byte[] bArr = new byte[8192];
                while (this.running.get()) {
                    try {
                        DatagramPacket datagramPacket = new DatagramPacket(bArr, 8192);
                        datagramSocket.receive(datagramPacket);
                        this.state.set(new JSONObject(new String(datagramPacket.getData(), datagramPacket.getOffset(), datagramPacket.getLength(), "UTF-8")));
                        this.eonAddress.set(datagramPacket.getAddress());
                        lastEonRxElapsed = SystemClock.elapsedRealtime();
                        lastEonAddress = datagramPacket.getAddress().getHostAddress();
                        byte[] bytes = "HUD1".getBytes("US-ASCII");
                        datagramSocket.send(new DatagramPacket(bytes, bytes.length, datagramPacket.getAddress(), datagramPacket.getPort()));
                    } catch (SocketTimeoutException e) {
                    }
                }
            } catch (Exception e) {
                // 소켓 생성/수신 실패: 잠시 쉬었다가 다시 연다 (스레드를 죽이지 않는다)
                SystemClock.sleep(1000L);
            }
        }
    }

    public void mapLoop() {
        while (this.running.get()) {
            InetAddress inetAddress = this.eonAddress.get();
            if (inetAddress == null) {
                SystemClock.sleep(500L);
                continue;
            }
            try (Socket socket = new Socket()) {
                socket.connect(new InetSocketAddress(inetAddress, 7211), 2000);
                mapConnected = true;
                socket.setSoTimeout(4000);
                DataInputStream dataInputStream = new DataInputStream(socket.getInputStream());
                byte[] bArr = new byte[4];
                while (this.running.get() && inetAddress.equals(this.eonAddress.get())) {
                    dataInputStream.readFully(bArr);
                    if (bArr[0] != 77 || bArr[1] != 65 || bArr[2] != 80 || bArr[3] != 49) {
                        throw new Exception("bad map frame");
                    }
                    int readInt = dataInputStream.readInt();
                    if (readInt <= 4 || readInt > 2097152) {
                        throw new Exception("bad map size");
                    }
                    byte[] bArr2 = new byte[readInt];
                    dataInputStream.readFully(bArr2);
                    Bitmap decodeByteArray = BitmapFactory.decodeByteArray(bArr2, 0, readInt);
                    if (decodeByteArray != null) {
                        synchronized (this.mapFrame) {
                            Bitmap andSet = this.mapFrame.getAndSet(decodeByteArray);
                            if (andSet != null && andSet != decodeByteArray) {
                                andSet.recycle();
                            }
                        }
                    }
                }
            } catch (Exception e) {
                mapConnected = false;
                SystemClock.sleep(500L);
            }
        }
        mapConnected = false;
    }

    public void renderLoop() {
        Bitmap render;
        long elapsedRealtime = SystemClock.elapsedRealtime();
        long j = 0;
        int i = 0;
        while (this.running.get()) {
            long elapsedRealtime2 = SystemClock.elapsedRealtime();
            if (elapsedRealtime2 < j) {
                SystemClock.sleep(Math.min(20L, j - elapsedRealtime2));
            } else {
                j = 125 + elapsedRealtime2;
                try {
                    if (!this.display.openOrRequestPermission()) {
                        usbStatus = this.display.describeStatus();
                        usbConnected = false;
                        usbError = false;
                        SystemClock.sleep(500L);
                    } else {
                        usbStatus = "연결됨 · USB 권한 허용";
                        usbConnected = true;
                        usbError = false;
                        synchronized (this.mapFrame) {
                            render = render(this.state.get(), this.mapFrame.get());
                        }
                        ByteArrayOutputStream byteArrayOutputStream = new ByteArrayOutputStream(180000);
                        Matrix matrix = new Matrix();
                        matrix.setRotate(-90.0f);
                        Bitmap createBitmap = Bitmap.createBitmap(render, 0, 0, render.getWidth(), render.getHeight(), matrix, true);
                        render.recycle();
                        createBitmap.compress(Bitmap.CompressFormat.JPEG, 55, byteArrayOutputStream);
                        createBitmap.recycle();
                        byte[] byteArray = byteArrayOutputStream.toByteArray();
                        this.display.sendJpeg(byteArray);
                        lastJpegBytes = byteArray.length;
                        lastJpegSentElapsed = SystemClock.elapsedRealtime();
                        i++;
                        long j2 = lastJpegSentElapsed - elapsedRealtime;
                        if (j2 >= 1000) {
                            measuredFps = (i * 1000.0f) / ((float) j2);
                            elapsedRealtime = lastJpegSentElapsed;
                            i = 0;
                        }
                    }
                } catch (Exception e) {
                    i = 0;
                    usbStatus = "USB 오류 · " + e.getMessage();
                    usbConnected = false;
                    usbError = true;
                    this.display.close();
                    SystemClock.sleep(500L);
                }
            }
        }
    }

    private Bitmap render(JSONObject jSONObject, Bitmap bitmap) {
        Bitmap createBitmap = Bitmap.createBitmap(WIDTH, HEIGHT, Bitmap.Config.RGB_565);
        Canvas canvas = new Canvas(createBitmap);
        Paint paint = new Paint(1);
        canvas.drawColor(Color.rgb(5, 8, 12));
        drawDriving(canvas, paint, jSONObject);
        drawSystem(canvas, paint, jSONObject);
        drawMap(canvas, paint, bitmap);
        return createBitmap;
    }

    private void drawDriving(Canvas canvas, Paint paint, JSONObject jSONObject) {
        boolean optBoolean = jSONObject.optBoolean("enabled", false);
        int save = canvas.save();
        canvas.clipRect(8, 8, 760, 454);
        drawWorld(canvas, paint, jSONObject, optBoolean, 768);
        canvas.restoreToCount(save);
        paint.setShader(null);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(2.0f);
        paint.setColor(Color.rgb(48, 60, 72));
        canvas.drawRoundRect(new RectF(8.0f, 8.0f, 760.0f, 454.0f), 22.0f, 22.0f, paint);
        text(canvas, paint, jSONObject.optString("gear", "--"), 38.0f, 48.0f, 37.0f, -1, Paint.Align.LEFT);
        text(canvas, paint, "KM/H", 384.0f, 46.0f, 26.0f, -3355444, Paint.Align.CENTER);
        text(canvas, paint, Integer.toString(jSONObject.optInt("speed", 0)), 384.0f, 132.0f, 82.0f, -1, Paint.Align.CENTER);
        int optInt = jSONObject.optInt("set", 0);
        text(canvas, paint, "SET  " + (optInt > 0 ? Integer.valueOf(optInt) : "--"), 384.0f, 171.0f, 29.0f, optBoolean ? Color.rgb(0, 230, 135) : -3355444, Paint.Align.CENTER);
        int optInt2 = jSONObject.optInt("limit", 0);
        text(canvas, paint, "LIMIT " + (optInt2 > 0 ? Integer.valueOf(optInt2) : "--"), 736.0f, 48.0f, 28.0f, -1, Paint.Align.RIGHT);
        int optInt3 = jSONObject.optInt("camera", 0);
        int optInt4 = jSONObject.optInt("cameraDist", 0);
        if (optInt3 > 0) {
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(5.0f);
            paint.setColor(Color.rgb(235, 74, 74));
            canvas.drawCircle(692.0f, 127.0f, 42.0f, paint);
            text(canvas, paint, Integer.toString(optInt3), 692.0f, 139.0f, 35.0f, -1, Paint.Align.CENTER);
            text(canvas, paint, String.valueOf(optInt4) + " m", 692.0f, 190.0f, 25.0f, Color.rgb(255, 205, 80), Paint.Align.CENTER);
        }
        int optInt5 = jSONObject.optInt("gap", 0);
        text(canvas, paint, "GAP " + (optInt5 > 0 ? Integer.valueOf(optInt5) : "--"), 34.0f, 440.0f, 26.0f, -3355444, Paint.Align.LEFT);
    }

    public static final class StableScene {
        final JSONArray edges;
        final JSONArray lanes;
        final JSONArray path;

        StableScene(JSONArray jSONArray, JSONArray jSONArray2, JSONArray jSONArray3) {
            this.path = jSONArray;
            this.lanes = jSONArray2;
            this.edges = jSONArray3;
        }
    }

    public static final class GeometryStabilizer {
        private static final float EDGE_CONFIDENCE = 0.25f;
        private static final float EMA_ALPHA = 0.28f;
        private static final long HOLD_MS = 500;
        private static final float LANE_CONFIDENCE = 0.25f;
        private static final float MAX_ROAD_WIDTH_M = 18.0f;
        private static final float MIN_ROAD_WIDTH_M = 4.5f;
        private static final float[] SAMPLE_X = {0.0f, 3.0f, 6.0f, 10.0f, 15.0f, 22.0f, 30.0f, 42.0f, 58.0f, 78.0f, 105.0f};
        private final boolean[] edgeActive;
        private final long[] edgeLastValid;
        private final float[][] edges;
        private final boolean[] laneActive;
        private final long[] laneLastValid;
        private final float[][] lanes;
        private final float[] path;
        private boolean pathActive;
        private long pathLastValid;

        private GeometryStabilizer() {
            this.lanes = (float[][]) Array.newInstance((Class<?>) Float.TYPE, 4, SAMPLE_X.length);
            this.edges = (float[][]) Array.newInstance((Class<?>) Float.TYPE, 2, SAMPLE_X.length);
            this.path = new float[SAMPLE_X.length];
            this.laneActive = new boolean[4];
            this.edgeActive = new boolean[2];
            this.laneLastValid = new long[4];
            this.edgeLastValid = new long[2];
            this.pathActive = false;
            this.pathLastValid = 0L;
        }

        /* synthetic */ GeometryStabilizer(GeometryStabilizer geometryStabilizer) {
            this();
        }

        StableScene update(JSONObject jSONObject, long j) {
            updatePath(jSONObject.optJSONArray("path"), j);
            updateLines(jSONObject.optJSONArray("lanes"), this.lanes, this.laneActive, this.laneLastValid, 0.25f, j);
            updateLines(jSONObject.optJSONArray("edges"), this.edges, this.edgeActive, this.edgeLastValid, 0.25f, j);
            constrainRoadAndLanes();
            return new StableScene(toPath(), toLines(this.lanes, this.laneActive), toLines(this.edges, this.edgeActive));
        }

        private void updatePath(JSONArray jSONArray, long j) {
            float[] sample = sample(jSONArray);
            if (sample != null) {
                blend(this.path, sample, this.pathActive);
                this.pathActive = true;
                this.pathLastValid = j;
            } else if (j - this.pathLastValid > HOLD_MS) {
                this.pathActive = false;
            }
        }

        private void updateLines(JSONArray jSONArray, float[][] fArr, boolean[] zArr, long[] jArr, float f, long j) {
            for (int i = 0; i < fArr.length; i++) {
                float[] fArr2 = null;
                JSONObject optJSONObject = jSONArray == null ? null : jSONArray.optJSONObject(i);
                float optDouble = optJSONObject == null ? 0.0f : (float) optJSONObject.optDouble("c", 0.0d);
                if (optJSONObject != null && optDouble >= f) {
                    fArr2 = sample(optJSONObject.optJSONArray("p"));
                }
                if (fArr2 != null) {
                    blend(fArr[i], fArr2, zArr[i]);
                    zArr[i] = true;
                    jArr[i] = j;
                } else if (j - jArr[i] > HOLD_MS) {
                    zArr[i] = false;
                }
            }
        }

        private static void blend(float[] fArr, float[] fArr2, boolean z) {
            if (!z) {
                System.arraycopy(fArr2, 0, fArr, 0, fArr.length);
                return;
            }
            for (int i = 0; i < fArr.length; i++) {
                fArr[i] = fArr[i] + ((fArr2[i] - fArr[i]) * EMA_ALPHA);
            }
        }

        private static float[] sample(JSONArray jSONArray) {
            if (jSONArray == null || jSONArray.length() < 2) {
                return null;
            }
            float[] fArr = new float[SAMPLE_X.length];
            for (int i = 0; i < SAMPLE_X.length; i++) {
                float f = SAMPLE_X[i];
                JSONArray optJSONArray = jSONArray.optJSONArray(0);
                JSONArray optJSONArray2 = jSONArray.optJSONArray(jSONArray.length() - 1);
                if (optJSONArray == null || optJSONArray2 == null) {
                    return null;
                }
                float optDouble = (float) optJSONArray.optDouble(1, Double.NaN);
                boolean z = ((double) f) <= optJSONArray.optDouble(0, 0.0d);
                int i2 = 0;
                while (!z) {
                    int i3 = i2 + 1;
                    if (i3 >= jSONArray.length()) {
                        break;
                    }
                    JSONArray optJSONArray3 = jSONArray.optJSONArray(i2);
                    JSONArray optJSONArray4 = jSONArray.optJSONArray(i3);
                    if (optJSONArray3 != null && optJSONArray4 != null) {
                        float optDouble2 = (float) optJSONArray3.optDouble(0);
                        float optDouble3 = (float) optJSONArray4.optDouble(0);
                        if (f <= optDouble3 || i2 + 2 == jSONArray.length()) {
                            optDouble = (float) (optJSONArray3.optDouble(1) + ((optJSONArray4.optDouble(1) - optJSONArray3.optDouble(1)) * (Math.abs(optDouble3 - optDouble2) >= 0.001f ? Math.max(0.0f, Math.min(1.0f, (f - optDouble2) / (optDouble3 - optDouble2))) : 0.0f)));
                            z = true;
                        }
                    }
                    i2 = i3;
                }
                if (!Float.isFinite(optDouble)) {
                    return null;
                }
                fArr[i] = optDouble;
            }
            return fArr;
        }

        private void constrainRoadAndLanes() {
            if (this.edgeActive[0] && this.edgeActive[1]) {
                float f = 0.0f;
                int i = 0;
                while (i < SAMPLE_X.length) {
                    float min = Math.min(this.edges[0][i], this.edges[1][i]);
                    float max = Math.max(this.edges[0][i], this.edges[1][i]);
                    float f2 = (min + max) * 0.5f;
                    float max2 = Math.max(MIN_ROAD_WIDTH_M, Math.min(MAX_ROAD_WIDTH_M, max - min));
                    f = i > 0 ? Math.max(f - 2.0f, Math.min(f + 2.0f, max2)) : max2;
                    float f3 = 0.5f * f;
                    this.edges[0][i] = f2 - f3;
                    this.edges[1][i] = f2 + f3;
                    int[] iArr = new int[4];
                    float[] fArr = new float[4];
                    int i2 = 0;
                    for (int i3 = 0; i3 < this.lanes.length; i3++) {
                        if (this.laneActive[i3]) {
                            iArr[i2] = i3;
                            fArr[i2] = Math.max(this.edges[0][i] + 0.2f, Math.min(this.edges[1][i] - 0.2f, this.lanes[i3][i]));
                            i2++;
                        }
                    }
                    int i4 = 0;
                    while (i4 < i2) {
                        int i5 = i4 + 1;
                        for (int i6 = i5; i6 < i2; i6++) {
                            if (fArr[i6] < fArr[i4]) {
                                float f4 = fArr[i4];
                                fArr[i4] = fArr[i6];
                                fArr[i6] = f4;
                            }
                        }
                        i4 = i5;
                    }
                    for (int i7 = 1; i7 < i2; i7++) {
                        fArr[i7] = Math.max(fArr[i7], fArr[i7 - 1] + 0.3f);
                    }
                    if (i2 > 0) {
                        int i8 = i2 - 1;
                        if (fArr[i8] > this.edges[1][i] - 0.2f) {
                            float f5 = fArr[i8] - (this.edges[1][i] - 0.2f);
                            for (int i9 = 0; i9 < i2; i9++) {
                                fArr[i9] = fArr[i9] - f5;
                            }
                        }
                    }
                    for (int i10 = 0; i10 < i2; i10++) {
                        this.lanes[iArr[i10]][i] = fArr[i10];
                    }
                    if (this.pathActive) {
                        this.path[i] = Math.max(this.edges[0][i] + 0.45f, Math.min(this.edges[1][i] - 0.45f, this.path[i]));
                    }
                    i++;
                }
            }
        }

        private JSONArray toPath() {
            JSONArray jSONArray = new JSONArray();
            if (!this.pathActive) {
                return jSONArray;
            }
            for (int i = 0; i < SAMPLE_X.length; i++) {
                jSONArray.put(new JSONArray((Collection) Arrays.asList(Float.valueOf(SAMPLE_X[i]), Float.valueOf(this.path[i]))));
            }
            return jSONArray;
        }

        private static JSONArray toLines(float[][] fArr, boolean[] zArr) {
            JSONArray jSONArray = new JSONArray();
            for (int i = 0; i < fArr.length; i++) {
                if (zArr[i]) {
                    JSONArray jSONArray2 = new JSONArray();
                    for (int i2 = 0; i2 < SAMPLE_X.length; i2++) {
                        jSONArray2.put(new JSONArray((Collection) Arrays.asList(Float.valueOf(SAMPLE_X[i2]), Float.valueOf(fArr[i][i2]))));
                    }
                    JSONObject jSONObject = new JSONObject();
                    try {
                        jSONObject.put("c", 1.0d);
                        jSONObject.put("p", jSONArray2);
                        jSONArray.put(jSONObject);
                    } catch (Exception e) {
                    }
                }
            }
            return jSONArray;
        }
    }

    private void drawWorld(Canvas canvas, Paint paint, JSONObject jSONObject, boolean z, int i) {
        float f = i * 0.5f;
        StableScene update = this.geometry.update(jSONObject, SystemClock.elapsedRealtime());
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new LinearGradient(0.0f, 8.0f, 0.0f, 188.0f, Color.rgb(7, 17, 29), Color.rgb(26, 39, 51), Shader.TileMode.CLAMP));
        canvas.drawRect(8.0f, 8.0f, i - 8, 188.0f, paint);
        paint.setShader(new LinearGradient(0.0f, 188.0f, 0.0f, 456.0f, Color.rgb(39, 45, 51), Color.rgb(10, 13, 17), Shader.TileMode.CLAMP));
        canvas.drawPath(roadSurface(update.edges, f, 188.0f, 456.0f, i), paint);
        paint.setShader(null);
        drawModelLines(canvas, paint, update.edges, f, 188.0f, 456.0f, Color.rgb(255, 78, 62), false);
        drawModelLines(canvas, paint, update.lanes, f, 188.0f, 456.0f, -1, true);
        drawPathSurface(canvas, paint, update.path, z, f, 188.0f, 456.0f);
        JSONArray jSONArray = update.path;
        JSONObject optJSONObject = jSONObject.optJSONObject("lead2");
        if (optJSONObject != null) {
            drawLead(canvas, paint, optJSONObject, jSONArray, f, 188.0f, 456.0f, false);
        }
        JSONObject optJSONObject2 = jSONObject.optJSONObject("lead");
        if (optJSONObject2 != null) {
            drawLead(canvas, paint, optJSONObject2, jSONArray, f, 188.0f, 456.0f, true);
        }
        drawVehicleSprite(canvas, paint, this.egoCar, f, 407.0f, 108.0f, pathYaw(jSONArray, 5.0f, f, 188.0f, 456.0f), 255);
        if (jSONObject.optBoolean("leftBsd", false)) {
            drawBsdVehicle(canvas, paint, f - 102.0f, 414.0f, true);
        }
        if (jSONObject.optBoolean("rightBsd", false)) {
            drawBsdVehicle(canvas, paint, f + 102.0f, 414.0f, false);
        }
        if (jSONObject.optBoolean("leftBlinker", false)) {
            drawTurnArrow(canvas, paint, 60.0f, 250.0f, true);
        }
        if (jSONObject.optBoolean("rightBlinker", false)) {
            drawTurnArrow(canvas, paint, i - 60, 250.0f, false);
        }
    }

    private float[] project(float f, float f2, float f3, float f4, float f5) {
        float max = Math.max(0.0f, f);
        return new float[]{f3 - (f2 * (105.0f / ((max / 17.0f) + 1.0f))), f5 - (((f5 - f4) * max) / (13.0f + max))};
    }

    private Path roadSurface(JSONArray jSONArray, float f, float f2, float f3, int i) {
        if (jSONArray != null && jSONArray.length() >= 2) {
            JSONObject optJSONObject = jSONArray.optJSONObject(0);
            JSONObject optJSONObject2 = jSONArray.optJSONObject(1);
            JSONArray optJSONArray = optJSONObject == null ? null : optJSONObject.optJSONArray("p");
            JSONArray optJSONArray2 = optJSONObject2 != null ? optJSONObject2.optJSONArray("p") : null;
            if (optJSONArray != null && optJSONArray2 != null && optJSONArray.length() >= 2 && optJSONArray2.length() >= 2) {
                Path path = new Path();
                boolean z = false;
                for (int i2 = 0; i2 < optJSONArray.length(); i2++) {
                    JSONArray optJSONArray3 = optJSONArray.optJSONArray(i2);
                    if (optJSONArray3 != null) {
                        float[] project = project((float) optJSONArray3.optDouble(0), (float) optJSONArray3.optDouble(1), f, f2, f3);
                        if (!z) {
                            path.moveTo(project[0], project[1]);
                            z = true;
                        } else {
                            path.lineTo(project[0], project[1]);
                        }
                    }
                }
                for (int length = optJSONArray2.length() - 1; length >= 0; length--) {
                    JSONArray optJSONArray4 = optJSONArray2.optJSONArray(length);
                    if (optJSONArray4 != null) {
                        float[] project2 = project((float) optJSONArray4.optDouble(0), (float) optJSONArray4.optDouble(1), f, f2, f3);
                        path.lineTo(project2[0], project2[1]);
                    }
                }
                path.close();
                return path;
            }
        }
        Path path2 = new Path();
        path2.moveTo(f - 92.0f, f2);
        path2.lineTo(f + 92.0f, f2);
        path2.lineTo(i - 26, f3);
        path2.lineTo(26.0f, f3);
        path2.close();
        return path2;
    }

    private void drawModelLines(Canvas canvas, Paint paint, JSONArray jSONArray, float f, float f2, float f3, int i, boolean z) {
        JSONArray optJSONArray;
        if (jSONArray == null) {
            return;
        }
        for (int i2 = 0; i2 < jSONArray.length(); i2++) {
            JSONObject optJSONObject = jSONArray.optJSONObject(i2);
            if (optJSONObject != null) {
                float optDouble = (float) optJSONObject.optDouble("c", 0.0d);
                if (optDouble >= 0.12f && (optJSONArray = optJSONObject.optJSONArray("p")) != null && optJSONArray.length() >= 2) {
                    drawPerspectiveLine(canvas, paint, optJSONArray, optDouble, f, f2, f3, i, z);
                }
            }
        }
    }

    private void drawPerspectiveLine(Canvas canvas, Paint paint, JSONArray jSONArray, float f, float f2, float f3, float f4, int i, boolean z) {
        int i2;
        int i3;
        paint.setShader(null);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setColor((((int) ((185.0f * f) + 70.0f)) << 24) | (i & 16777215));
        int i4 = 0;
        int i5 = 0;
        while (true) {
            int i6 = i5 + 1;
            if (i6 < jSONArray.length()) {
                JSONArray optJSONArray = jSONArray.optJSONArray(i5);
                JSONArray optJSONArray2 = jSONArray.optJSONArray(i6);
                if (optJSONArray != null && optJSONArray2 != null) {
                    float optDouble = (float) optJSONArray.optDouble(i4);
                    float optDouble2 = (float) optJSONArray.optDouble(1);
                    float optDouble3 = (float) optJSONArray2.optDouble(i4);
                    float optDouble4 = (float) optJSONArray2.optDouble(1);
                    float f5 = optDouble3 - optDouble;
                    int max = Math.max(1, (int) Math.ceil(Math.abs(f5)));
                    int i7 = i4;
                    while (i7 < max) {
                        float f6 = max;
                        float f7 = i7 / f6;
                        int i8 = i7 + 1;
                        float f8 = i8 / f6;
                        float f9 = (f5 * f7) + optDouble;
                        float f10 = (f5 * f8) + optDouble;
                        if (z) {
                            i2 = i8;
                            if ((((int) Math.floor(((f9 + f10) * 0.5f) / 4.5f)) & 1) != 0) {
                                i3 = i2;
                                i4 = 0;
                                i7 = i3;
                            }
                        } else {
                            i2 = i8;
                        }
                        float f11 = optDouble4 - optDouble2;
                        float[] project = project(f9, optDouble2 + (f7 * f11), f2, f3, f4);
                        float[] project2 = project(f10, optDouble2 + (f11 * f8), f2, f3, f4);
                        paint.setStrokeWidth((z ? 1.6f : 1.2f) + ((z ? 5.2f : 3.6f) / ((Math.max(0.0f, (f9 + f10) * 0.5f) / 16.0f) + 1.0f)));
                        i4 = 0;
                        i3 = i2;
                        canvas.drawLine(project[0], project[1], project2[0], project2[1], paint);
                        i7 = i3;
                    }
                }
                i5 = i6;
            } else {
                return;
            }
        }
    }

    private void drawPathSurface(Canvas canvas, Paint paint, JSONArray jSONArray, boolean z, float f, float f2, float f3) {
        if (jSONArray == null || jSONArray.length() < 2) {
            return;
        }
        int length = jSONArray.length();
        float[][] fArr = new float[length][];
        float[][] fArr2 = new float[length][];
        int i = 0;
        for (int i2 = 0; i2 < length; i2++) {
            JSONArray optJSONArray = jSONArray.optJSONArray(i2);
            if (optJSONArray != null) {
                float optDouble = (float) optJSONArray.optDouble(0);
                float optDouble2 = (float) optJSONArray.optDouble(1);
                fArr[i] = project(optDouble, optDouble2 + 0.72f, f, f2, f3);
                fArr2[i] = project(optDouble, optDouble2 - 0.72f, f, f2, f3);
                i++;
            }
        }
        if (i < 2) {
            return;
        }
        Path path = new Path();
        path.moveTo(fArr[0][0], fArr[0][1]);
        for (int i3 = 1; i3 < i; i3++) {
            path.lineTo(fArr[i3][0], fArr[i3][1]);
        }
        for (int i4 = i - 1; i4 >= 0; i4--) {
            path.lineTo(fArr2[i4][0], fArr2[i4][1]);
        }
        path.close();
        int argb = z ? Color.argb(205, 0, 183, 255) : Color.argb(135, 90, 102, 112);
        int argb2 = z ? Color.argb(35, 0, 220, 150) : Color.argb(20, 70, 80, 90);
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new LinearGradient(0.0f, f3, 0.0f, f2, argb, argb2, Shader.TileMode.CLAMP));
        canvas.drawPath(path, paint);
        paint.setShader(null);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(2.0f);
        paint.setColor(z ? Color.rgb(94, 225, 255) : Color.rgb(100, 110, 120));
        canvas.drawPath(path, paint);
    }

    private void drawLead(Canvas canvas, Paint paint, JSONObject jSONObject, JSONArray jSONArray, float f, float f2, float f3, boolean z) {
        float optDouble = (float) jSONObject.optDouble("d", 0.0d);
        float optDouble2 = (float) jSONObject.optDouble("y", 0.0d);
        if (optDouble > 0.0f && optDouble <= 150.0f) {
            float[] project = project(optDouble, optDouble2, f, f2, f3);
            float max = Math.max(0.22f, 1.0f / ((optDouble / 24.0f) + 1.0f));
            float max2 = Math.max(22.0f, 94.0f * max);
            drawVehicleSprite(canvas, paint, this.otherCar, project[0], project[1] - (0.18f * max2), max2, pathYaw(jSONArray, optDouble, f, f2, f3), z ? 255 : 215);
            if (z) {
                text(canvas, paint, String.format(Locale.US, "%.0fm", Float.valueOf(optDouble)), project[0], project[1] - (max2 * 0.92f), Math.max(17.0f, max * 23.0f), -1, Paint.Align.CENTER);
            }
        }
    }

    private void drawVehicleSprite(Canvas canvas, Paint paint, Bitmap bitmap, float f, float f2, float f3, float f4, int i) {
        if (bitmap == null || bitmap.isRecycled()) {
            return;
        }
        float height = (bitmap.getHeight() * f3) / bitmap.getWidth();
        int save = canvas.save();
        canvas.translate(f, f2);
        canvas.rotate(f4);
        paint.setShader(null);
        paint.setStyle(Paint.Style.FILL);
        paint.setAlpha(i);
        paint.setFilterBitmap(true);
        canvas.drawBitmap(bitmap, (Rect) null, new RectF((-f3) * 0.5f, (-height) * 0.5f, f3 * 0.5f, height * 0.5f), paint);
        paint.setAlpha(255);
        canvas.restoreToCount(save);
    }

    private void drawBsdVehicle(Canvas canvas, Paint paint, float f, float f2, boolean z) {
        drawVehicleSprite(canvas, paint, this.otherCar, f, f2, 58.0f, z ? -16.0f : 16.0f, 245);
    }

    private float pathYaw(JSONArray jSONArray, float f, float f2, float f3, float f4) {
        if (jSONArray == null || jSONArray.length() < 2) {
            return 0.0f;
        }
        JSONArray jSONArray2 = null;
        JSONArray jSONArray3 = null;
        int i = 0;
        while (true) {
            if (i >= jSONArray.length()) {
                break;
            }
            JSONArray optJSONArray = jSONArray.optJSONArray(i);
            if (optJSONArray != null) {
                float optDouble = (float) optJSONArray.optDouble(0);
                if (optDouble <= f) {
                    jSONArray3 = optJSONArray;
                }
                if (optDouble >= f) {
                    jSONArray2 = optJSONArray;
                    break;
                }
            }
            i++;
        }
        if (jSONArray3 == null) {
            jSONArray3 = jSONArray.optJSONArray(0);
        }
        if (jSONArray2 == null) {
            jSONArray2 = jSONArray.optJSONArray(jSONArray.length() - 1);
        }
        if (jSONArray3 == null || jSONArray2 == null || jSONArray3 == jSONArray2) {
            return 0.0f;
        }
        float[] project = project((float) jSONArray3.optDouble(0), (float) jSONArray3.optDouble(1), f2, f3, f4);
        float[] project2 = project((float) jSONArray2.optDouble(0), (float) jSONArray2.optDouble(1), f2, f3, f4);
        return Math.max(-22.0f, Math.min(22.0f, (float) Math.toDegrees(Math.atan2(project2[0] - project[0], project[1] - project2[1]))));
    }

    private void drawTurnArrow(Canvas canvas, Paint paint, float f, float f2, boolean z) {
        Path path = new Path();
        float f3 = z ? -1.0f : 1.0f;
        float f4 = (28.0f * f3) + f;
        path.moveTo(f4, f2 - 26.0f);
        path.lineTo(f - (f3 * 24.0f), f2);
        path.lineTo(f4, f2 + 26.0f);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(9.0f);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setColor(Color.rgb(0, 235, 135));
        canvas.drawPath(path, paint);
    }

    private void drawSystem(Canvas canvas, Paint paint, JSONObject jSONObject) {
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.rgb(16, 21, 28));
        canvas.drawRoundRect(new RectF(776, 8.0f, 1144, 454.0f), 22.0f, 22.0f, paint);
        float f = WIDTH / 2.0f;
        text(canvas, paint, "SYSTEM", f, 55.0f, 30.0f, -3355444, Paint.Align.CENTER);
        float f2 = 810;
        text(canvas, paint, "CPU", f2, 122.0f, 27.0f, -7829368, Paint.Align.LEFT);
        float f3 = 1110;
        text(canvas, paint, String.valueOf(jSONObject.optInt("cpu", 0)) + "%", f3, 122.0f, 44.0f, -1, Paint.Align.RIGHT);
        text(canvas, paint, "TEMP", f2, 202.0f, 27.0f, -7829368, Paint.Align.LEFT);
        text(canvas, paint, String.format(Locale.US, "%.0f°C", Double.valueOf(jSONObject.optDouble("temp", 0.0d))), f3, 202.0f, 44.0f, -1, Paint.Align.RIGHT);
        text(canvas, paint, "ACCEL", f2, 282.0f, 27.0f, -7829368, Paint.Align.LEFT);
        text(canvas, paint, String.format(Locale.US, "%+.2f", Double.valueOf(jSONObject.optDouble("accel", 0.0d))), f3, 282.0f, 39.0f, -1, Paint.Align.RIGHT);
        long max = Math.max(0L, System.currentTimeMillis() - jSONObject.optLong("t", 0L));
        text(canvas, paint, max < 1500 ? "EON CONNECTED" : "WAITING FOR EON", f, 406.0f, 25.0f, max < 1500 ? Color.rgb(0, 220, 120) : Color.rgb(235, 74, 74), Paint.Align.CENTER);
    }

    private void drawMap(Canvas canvas, Paint paint, Bitmap bitmap) {
        Rect rect;
        Rect rect2 = new Rect(1152, 0, WIDTH, HEIGHT);
        if (bitmap == null || bitmap.isRecycled()) {
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(-16777216);
            canvas.drawRect(rect2, paint);
            text(canvas, paint, "TMAP 화면 대기", 1536.0f, 240.0f, 34.0f, -7829368, Paint.Align.CENTER);
            return;
        }
        float width = rect2.width() / rect2.height();
        if (bitmap.getWidth() / bitmap.getHeight() > width) {
            int round = Math.round(bitmap.getHeight() * width);
            int width2 = (bitmap.getWidth() - round) / 2;
            rect = new Rect(width2, 0, round + width2, bitmap.getHeight());
        } else {
            int round2 = Math.round(bitmap.getWidth() / width);
            int height = (bitmap.getHeight() - round2) / 2;
            rect = new Rect(0, height, bitmap.getWidth(), round2 + height);
        }
        canvas.drawBitmap(bitmap, rect, rect2, paint);
    }

    private static void text(Canvas canvas, Paint paint, String str, float f, float f2, float f3, int i, Paint.Align align) {
        paint.setStyle(Paint.Style.FILL);
        paint.setTypeface(Typeface.create("sans", 1));
        paint.setTextSize(f3);
        paint.setTextAlign(align);
        paint.setColor(i);
        canvas.drawText(str, f, f2, paint);
    }

    @Override
    public void onDestroy() {
        this.running.set(false);
        this.starter.removeCallbacksAndMessages(null);
        this.workersStarted = false;
        serviceRunning = false;
        mapConnected = false;
        usbConnected = false;
        usbError = false;
        usbStatus = "서비스 중지됨";
        measuredFps = 0.0f;
        lastJpegBytes = 0;
        if (this.display != null) {
            this.display.close();
        }
        if (this.egoCar != null) {
            this.egoCar.recycle();
            this.egoCar = null;
        }
        if (this.otherCar != null) {
            this.otherCar.recycle();
            this.otherCar = null;
        }
        synchronized (this.mapFrame) {
            Bitmap andSet = this.mapFrame.getAndSet(null);
            if (andSet != null) {
                andSet.recycle();
            }
        }
        if (this.usbReceiverRegistered) {
            try {
                unregisterReceiver(this.usbReceiver);
            } catch (Exception e) {
            }
            this.usbReceiverRegistered = false;
        }
        releaseWakeLock();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
