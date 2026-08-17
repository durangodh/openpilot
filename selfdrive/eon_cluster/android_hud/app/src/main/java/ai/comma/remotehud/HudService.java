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
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.hardware.usb.UsbDevice;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * v0.19 변경점
 *
 *  1. 주행씬을 World3D 의 실제 핀홀 원근투영으로 교체 (완전 3D).
 *  2. 프레임 버퍼 재사용 — v0.18 은 매 프레임 1920x462 비트맵을 만들고
 *     거기에 회전 복사본을 하나 더 만들었다(약 3.5MB/프레임, 8fps 면 28MB/s).
 *     이제 462x1920 세로 버퍼 하나만 잡아 두고 캔버스 행렬로 회전한다.
 *  3. Paint / Path / RectF 를 필드로 돌려 써 프레임당 할당을 없앴다.
 *  4. EON UDP 가 끊기면 마지막 상태를 그대로 얼려 두지 않고 화면에 알린다.
 *     (v0.18 은 속도 0 인 채로 굳어 패널이 멈춘 것처럼 보였다)
 *  5. USB halt 처리 정리 + 패널 무응답 감시는 TurzxDisplay v0.19 참고.
 */
public final class HudService extends Service {

    static final String ACTION_RESCAN_USB = "ai.comma.remotehud.RESCAN_USB";
    static final String EXTRA_FROM_BOOT = "ai.comma.remotehud.FROM_BOOT";

    private static final String CHANNEL = "remote_hud";
    private static final long BOOT_START_DELAY_MS = 30000L;

    private static final int WIDTH = 1920;
    private static final int HEIGHT = 462;
    // 패널 폭 비율 5 : 4 : 1  (주행 : TMAP : SYSTEM)
    private static final int DRIVE_RIGHT = 952;
    private static final float DRIVE_CX = 476f;
    private static final int MAP_LEFT = 960;
    private static final int MAP_RIGHT = 1720;
    private static final float MAP_CX = 1340f;
    private static final int SYSTEM_LEFT = 1728;
    private static final int SYSTEM_RIGHT = 1920;

    private static final int USB_RESET_AFTER_ERRORS = 3;
    private static final int USB_SLOWDOWN_AFTER_ERRORS = 5;

    /** EON 텔레메트리가 이보다 오래 끊기면 화면에 표시한다 */
    private static final long EON_STALE_MS = 3000L;

    private static volatile boolean serviceRunning;
    private static volatile boolean mapConnected;
    private static volatile boolean usbConnected;
    private static volatile boolean usbError;
    private static volatile float measuredFps;
    private static volatile int lastJpegBytes;
    private static volatile long lastJpegSentElapsed;
    private static volatile long lastEonRxElapsed;
    private static volatile String lastEonAddress = "--";
    private static volatile String usbStatus = "미연결 · 1CBE:0092";

    private TurzxDisplay display;
    private Bitmap egoCar;
    private Bitmap otherCar;
    private Thread receiverThread;
    private Thread mapThread;
    private Thread renderThread;
    private int usbErrorStreak;
    private boolean usbReceiverRegistered;
    private PowerManager.WakeLock wakeLock;
    private volatile boolean workersStarted;

    private long frameIntervalMs = 125L;
    private volatile long mapFrameIntervalMs = 200L;
    private long lastMapAcceptedElapsed = 0L;
    private int configuredFps = 8;
    private int jpegQuality = 55;
    private int appliedBrightness = -1;
    private int configuredTheme = 0;
    private int configuredLanguage = 0;
    private int configuredRadarInfo = 4;
    private int configuredScreenMode = 1;
    /** 도로변 건물 표시 여부 (장식이므로 끌 수 있다) */
    private boolean configuredBuildings = true;
    private int configuredOrientation = 0;
    private boolean configuredMirror = false;

    private long tripStartElapsed = 0L;
    private long tripLastElapsed = 0L;
    private double tripDistanceKm = 0.0d;
    /** 건물이 뒤로 흘러가도록 하기 위한 누적 주행거리(m) */
    private float worldOdoM = 0f;

    // 렌더 재사용 자원 (렌더 스레드 전용)
    private Bitmap outFrame;
    private Canvas outCanvas;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Matrix outMatrix = new Matrix();
    private final RectF scratchRect = new RectF();
    private final Rect scratchIRect = new Rect();
    private final Path scratchPath = new Path();
    private final World3D world = new World3D();
    private final ByteArrayOutputStream jpegOut = new ByteArrayOutputStream(180000);

    private final Handler starter = new Handler(Looper.getMainLooper());
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicReference<JSONObject> state = new AtomicReference<>(new JSONObject());
    private final AtomicReference<Bitmap> mapFrame = new AtomicReference<>();
    private final AtomicReference<Bitmap> tbtCurrentFrame = new AtomicReference<>();
    private final AtomicReference<Bitmap> tbtNextFrame = new AtomicReference<>();
    private final AtomicReference<Bitmap> laneFrame = new AtomicReference<>();
    private final AtomicReference<InetAddress> eonAddress = new AtomicReference<>();
    private final Object assetLock = new Object();

    private final BroadcastReceiver usbReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            UsbDevice device = (UsbDevice) intent.getParcelableExtra("device");
            if (!TurzxDisplay.isTarget(device)) {
                return;
            }
            String action = intent.getAction();
            if ("android.hardware.usb.action.USB_DEVICE_DETACHED".equals(action)) {
                if (display != null) {
                    display.reset();
                }
                usbStatus = "분리됨 · 재연결 대기";
                usbConnected = false;
                usbError = false;
            } else if ("ai.comma.remotehud.USB_PERMISSION".equals(action)) {
                boolean granted = intent.getBooleanExtra("permission", false);
                if (display != null) {
                    display.reset();
                }
                usbStatus = granted ? "USB 권한 허용 · 연결 중" : "USB 권한 거부됨 · 재검색 필요";
                usbConnected = false;
                usbError = !granted;
            } else if ("android.hardware.usb.action.USB_DEVICE_ATTACHED".equals(action)) {
                requestUsbRescan();
            }
        }
    };

    // ── 상태 스냅샷 (MainActivity 표시용) ──────────────────────────────────

    public static final class StatusSnapshot {
        final boolean running;
        final boolean eonConnected;
        final String eonAddress;
        final boolean mapConnected;
        final String usbStatus;
        final boolean usbConnected;
        final boolean usbError;
        final float fps;
        final int lastJpegBytes;

        StatusSnapshot(boolean running, boolean eonConnected, String eonAddress,
                       boolean mapConnected, String usbStatus, boolean usbConnected,
                       boolean usbError, float fps, int lastJpegBytes) {
            this.running = running;
            this.eonConnected = eonConnected;
            this.eonAddress = eonAddress;
            this.mapConnected = mapConnected;
            this.usbStatus = usbStatus;
            this.usbConnected = usbConnected;
            this.usbError = usbError;
            this.fps = fps;
            this.lastJpegBytes = lastJpegBytes;
        }
    }

    public static StatusSnapshot getStatusSnapshot() {
        long now = SystemClock.elapsedRealtime();
        boolean eonOk = serviceRunning && lastEonRxElapsed > 0 && now - lastEonRxElapsed < 2000;
        boolean fpsOk = serviceRunning && lastJpegSentElapsed > 0 && now - lastJpegSentElapsed < 2000;
        return new StatusSnapshot(serviceRunning, eonOk, lastEonAddress, serviceRunning && mapConnected,
                usbStatus, serviceRunning && usbConnected, usbError,
                fpsOk ? measuredFps : 0.0f, fpsOk ? lastJpegBytes : 0);
    }

    // ── 라이프사이클 ──────────────────────────────────────────────────────

    @Override
    public void onCreate() {
        super.onCreate();
        egoCar = BitmapFactory.decodeResource(getResources(), R.drawable.hud_ego_car);
        otherCar = BitmapFactory.decodeResource(getResources(), R.drawable.hud_other_car);

        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "EON Remote HUD",
                NotificationManager.IMPORTANCE_LOW));
        Notification.Builder builder = new Notification.Builder(this, CHANNEL);
        startForeground(72, builder
                .setContentTitle("EON Remote HUD")
                .setContentText("상태 화면을 열려면 누르세요")
                .setSmallIcon(android.R.drawable.ic_menu_directions)
                .setContentIntent(PendingIntent.getActivity(this, 0,
                        new Intent(this, MainActivity.class),
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE))
                .setOngoing(true)
                .build());

        IntentFilter filter = new IntentFilter();
        filter.addAction("android.hardware.usb.action.USB_DEVICE_ATTACHED");
        filter.addAction("android.hardware.usb.action.USB_DEVICE_DETACHED");
        filter.addAction("ai.comma.remotehud.USB_PERMISSION");
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(usbReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(usbReceiver, filter);
        }
        usbReceiverRegistered = true;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_RESCAN_USB.equals(intent.getAction()) && running.get()) {
            requestUsbRescan();
            return START_STICKY;
        }
        if (running.get()) {
            return START_STICKY;
        }
        running.set(true);
        serviceRunning = true;
        mapConnected = false;
        usbConnected = false;
        usbError = false;
        measuredFps = 0.0f;
        lastJpegBytes = 0;
        acquireWakeLock();

        boolean fromBoot = intent != null && intent.getBooleanExtra(EXTRA_FROM_BOOT, false);
        if (fromBoot) {
            usbStatus = "부팅 대기 30초";
            starter.postDelayed(new Runnable() {
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
        if (!running.get() || workersStarted) {
            return;
        }
        workersStarted = true;
        usbStatus = "외부 HUD 검색 중";
        display = new TurzxDisplay(this);
        receiverThread = new Thread(new Runnable() {
            @Override
            public void run() {
                receiveLoop();
            }
        }, "hud-telemetry");
        mapThread = new Thread(new Runnable() {
            @Override
            public void run() {
                mapLoop();
            }
        }, "hud-tmap");
        renderThread = new Thread(new Runnable() {
            @Override
            public void run() {
                renderLoop();
            }
        }, "hud-render");
        receiverThread.start();
        mapThread.start();
        renderThread.start();
    }

    private void acquireWakeLock() {
        try {
            if (wakeLock == null) {
                PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
                wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "RemoteHUD::render");
                wakeLock.setReferenceCounted(false);
            }
            if (!wakeLock.isHeld()) {
                wakeLock.acquire();
            }
        } catch (Exception ignored) {
        }
    }

    private void releaseWakeLock() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (Exception ignored) {
        }
        wakeLock = null;
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
        if (display != null) {
            display.reset();
        }
        usbStatus = "외부 HUD 재검색 중";
        usbConnected = false;
        usbError = false;
        measuredFps = 0.0f;
        lastJpegBytes = 0;
        usbErrorStreak = 0;
    }

    // ── 네트워크 ──────────────────────────────────────────────────────────

    private void receiveLoop() {
        while (running.get()) {
            DatagramSocket socket = null;
            try {
                socket = new DatagramSocket(7210);
                socket.setBroadcast(true);
                socket.setSoTimeout(1000);
                byte[] buffer = new byte[16384];
                while (running.get()) {
                    try {
                        DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                        socket.receive(packet);
                        state.set(new JSONObject(new String(packet.getData(), packet.getOffset(),
                                packet.getLength(), "UTF-8")));
                        eonAddress.set(packet.getAddress());
                        lastEonRxElapsed = SystemClock.elapsedRealtime();
                        lastEonAddress = packet.getAddress().getHostAddress();
                        byte[] ack = "HUD1".getBytes("US-ASCII");
                        socket.send(new DatagramPacket(ack, ack.length, packet.getAddress(), packet.getPort()));
                    } catch (SocketTimeoutException ignored) {
                    }
                }
            } catch (Exception e) {
                SystemClock.sleep(1000L);
            } finally {
                if (socket != null) {
                    try {
                        socket.close();
                    } catch (Exception ignored) {
                    }
                }
            }
        }
    }

    private static boolean tagEquals(byte[] h, String tag) {
        return h.length == 4 && h[0] == tag.charAt(0) && h[1] == tag.charAt(1)
                && h[2] == tag.charAt(2) && h[3] == tag.charAt(3);
    }

    private void replaceAsset(AtomicReference<Bitmap> target, byte[] data) {
        Bitmap decoded = data.length == 0 ? null : BitmapFactory.decodeByteArray(data, 0, data.length);
        if (data.length > 0 && decoded == null) {
            return;
        }
        Bitmap old = target.getAndSet(decoded);
        if (old != null && old != decoded) {
            old.recycle();
        }
    }

    private void mapLoop() {
        while (running.get()) {
            InetAddress address = eonAddress.get();
            if (address == null) {
                SystemClock.sleep(500L);
                continue;
            }
            Socket socket = null;
            try {
                socket = new Socket();
                socket.connect(new InetSocketAddress(address, 7211), 2000);
                mapConnected = true;
                socket.setSoTimeout(4000);
                DataInputStream in = new DataInputStream(socket.getInputStream());
                byte[] header = new byte[4];
                while (running.get() && address.equals(eonAddress.get())) {
                    in.readFully(header);
                    int length = in.readInt();
                    if (length < 0 || length > 2097152) {
                        throw new Exception("bad asset size");
                    }
                    byte[] data = new byte[length];
                    if (length > 0) {
                        in.readFully(data);
                    }
                    synchronized (assetLock) {
                        if (tagEquals(header, "MAP1")) {
                            long mapNow = SystemClock.elapsedRealtime();
                            if (lastMapAcceptedElapsed == 0 || mapNow - lastMapAcceptedElapsed >= mapFrameIntervalMs) {
                                replaceAsset(mapFrame, data);
                                lastMapAcceptedElapsed = mapNow;
                            }
                        } else if (tagEquals(header, "TBT1")) {
                            replaceAsset(tbtCurrentFrame, data);
                        } else if (tagEquals(header, "TBT2")) {
                            replaceAsset(tbtNextFrame, data);
                        } else if (tagEquals(header, "LANE")) {
                            replaceAsset(laneFrame, data);
                        } else {
                            throw new Exception("bad asset tag");
                        }
                    }
                }
            } catch (Exception e) {
                mapConnected = false;
                SystemClock.sleep(500L);
            } finally {
                if (socket != null) {
                    try {
                        socket.close();
                    } catch (Exception ignored) {
                    }
                }
            }
        }
        mapConnected = false;
    }

    // ── 렌더 루프 ─────────────────────────────────────────────────────────

    private void renderLoop() {
        long fpsStart = SystemClock.elapsedRealtime();
        long nextFrame = 0L;
        int frames = 0;

        while (running.get()) {
            long now = SystemClock.elapsedRealtime();
            if (now < nextFrame) {
                SystemClock.sleep(Math.min(20L, nextFrame - now));
                continue;
            }
            long due = now + frameIntervalMs;

            try {
                if (!display.openOrRequestPermission()) {
                    usbStatus = display.describeStatus();
                    usbConnected = false;
                    usbError = false;
                    SystemClock.sleep(500L);
                    nextFrame = due;
                    continue;
                }

                usbStatus = "연결됨 · USB 권한 허용";
                usbConnected = true;
                usbError = false;
                usbErrorStreak = 0;

                JSONObject currentState = state.get();
                int requestedFps = Math.max(0, Math.min(15, currentState.optInt("hudFps", 8)));
                if (requestedFps == 0) {
                    // 프레임 0 = 일시정지가 아니라 패널 끄기. 검은 프레임 한 장을
                    // 보내고 밝기를 최소로 내린 뒤 대기한다. v0.19.1 까지는 전송만
                    // 멈춰서 마지막 화면이 그대로 남아 있었다.
                    if (configuredFps != 0) {
                        configuredFps = 0;
                        sendBlankFrame();
                        display.setBrightness(1);
                        appliedBrightness = 1;
                        lastJpegSentElapsed = SystemClock.elapsedRealtime();
                    }
                    usbStatus = "연결됨 · 패널 꺼짐 (프레임 0)";
                    SystemClock.sleep(250L);
                    nextFrame = SystemClock.elapsedRealtime();
                    continue;
                }
                if (configuredFps == 0) {
                    appliedBrightness = -1;   // 복귀시 밝기를 다시 적용
                }
                if (requestedFps != configuredFps) {
                    configuredFps = requestedFps;
                    frameIntervalMs = Math.max(67L, 1000L / configuredFps);
                }

                int requestedMapFps = Math.max(2, Math.min(5, currentState.optInt("hudMapFps", 5)));
                mapFrameIntervalMs = Math.max(200L, 1000L / requestedMapFps);
                jpegQuality = Math.max(20, Math.min(95, currentState.optInt("hudJpegQuality", 55)));
                configuredTheme = Math.max(0, Math.min(2, currentState.optInt("hudTheme", 0)));
                configuredLanguage = Math.max(0, Math.min(1, currentState.optInt("hudLanguage", 0)));
                configuredRadarInfo = Math.max(0, Math.min(4, currentState.optInt("hudRadarInfo", 4)));
                configuredScreenMode = Math.max(1, Math.min(3, currentState.optInt("hudScreenMode", 1)));
                configuredOrientation = currentState.optInt("hudOrientation", 0) == 2 ? 2 : 0;
                configuredMirror = currentState.optInt("hudMirror", 0) != 0;
                configuredBuildings = currentState.optInt("hudBuildings", 1) != 0;

                int requestedBrightness = Math.max(0, Math.min(100, currentState.optInt("hudBrightness", 65)));
                if (requestedBrightness == 0) {
                    requestedBrightness = darkTheme() ? 35 : 65;
                }
                if (requestedBrightness != appliedBrightness) {
                    display.setBrightness(requestedBrightness);
                    appliedBrightness = requestedBrightness;
                }

                updateTrip(currentState, now);

                Bitmap frame;
                synchronized (assetLock) {
                    frame = render(currentState, mapFrame.get(), tbtCurrentFrame.get(),
                            tbtNextFrame.get(), laneFrame.get());
                }

                jpegOut.reset();
                frame.compress(Bitmap.CompressFormat.JPEG, jpegQuality, jpegOut);
                byte[] jpeg = jpegOut.toByteArray();
                display.sendJpeg(jpeg);

                lastJpegBytes = jpeg.length;
                lastJpegSentElapsed = SystemClock.elapsedRealtime();
                frames++;
                long span = lastJpegSentElapsed - fpsStart;
                if (span >= 1000L) {
                    measuredFps = frames * 1000.0f / span;
                    fpsStart = lastJpegSentElapsed;
                    frames = 0;
                }

                // 패널이 응답을 끊었는지 감시. TurzxDisplay 가 IN 엔드포인트에서
                // 한 번이라도 응답을 본 적이 있을 때만 동작하므로, 원래 조용한
                // 패널에서 헛되이 리셋하는 일은 없다.
                if (display.isUnresponsive(15000L)) {
                    usbStatus = "패널 무응답 · 재초기화";
                    display.close();
                    appliedBrightness = -1;
                    usbErrorStreak++;
                    SystemClock.sleep(600L);
                }
            } catch (Exception e) {
                usbConnected = false;
                usbError = true;
                usbErrorStreak++;
                display.recoverAfterError();
                display.close();
                appliedBrightness = -1;
                if (usbErrorStreak >= USB_RESET_AFTER_ERRORS) {
                    usbStatus = "USB 복구 중 · 포트 재연결";
                    boolean reset = UsbPortReset.resetPort(display.deviceNameOrNull());
                    display.reset();
                    if (!reset) {
                        usbStatus = "USB 오류 · " + e.getMessage() + " (루트 불가)";
                    }
                    SystemClock.sleep(2500L);
                } else {
                    usbStatus = "USB 오류 · " + e.getMessage();
                    SystemClock.sleep(500L);
                }
                if (usbErrorStreak >= USB_SLOWDOWN_AFTER_ERRORS && frameIntervalMs < 250L) {
                    frameIntervalMs = 250L;
                }
                frames = 0;
            }
            nextFrame = due;
        }
    }

    // ── 렌더 ──────────────────────────────────────────────────────────────

    /**
     * 세로 방향 출력 버퍼를 한 번만 잡고 계속 재사용한다. 회전/미러는
     * 캔버스 행렬로 처리하므로 v0.18 처럼 회전 복사본을 새로 만들 필요가 없다.
     */
    private Canvas beginFrame() {
        if (outFrame == null || outFrame.isRecycled()) {
            outFrame = Bitmap.createBitmap(HEIGHT, WIDTH, Bitmap.Config.RGB_565);
            outCanvas = new Canvas(outFrame);
        }
        outMatrix.reset();
        outMatrix.setScale(configuredMirror ? -1f : 1f, 1f);
        outMatrix.postRotate(configuredOrientation == 2 ? 90f : -90f);
        scratchRect.set(0f, 0f, WIDTH, HEIGHT);
        outMatrix.mapRect(scratchRect);
        outMatrix.postTranslate(-scratchRect.left, -scratchRect.top);
        outCanvas.setMatrix(outMatrix);
        return outCanvas;
    }

    /** 패널을 끌 때 보내는 검은 프레임 한 장. */
    private void sendBlankFrame() throws Exception {
        Canvas c = beginFrame();
        c.drawColor(Color.BLACK);
        jpegOut.reset();
        outFrame.compress(Bitmap.CompressFormat.JPEG, 40, jpegOut);
        byte[] jpeg = jpegOut.toByteArray();
        display.sendJpeg(jpeg);
        lastJpegBytes = jpeg.length;
    }

    private Bitmap render(JSONObject s, Bitmap map, Bitmap tbtCurrent, Bitmap tbtNext, Bitmap lane) {
        Canvas c = beginFrame();
        Paint p = paint;
        p.reset();
        p.setAntiAlias(true);
        c.drawColor(Color.rgb(5, 8, 12));

        drawDriving(c, p, s);

        JSONObject l = layout(s);
        int save = beginElement(c, l, "system", 1824f, 231f);
        drawSystem(c, p, s);
        c.restoreToCount(save);

        if (configuredScreenMode == 2) {
            drawDebugRight(c, p, s);
        } else if (configuredScreenMode == 3) {
            drawTripRight(c, p, s);
        } else {
            drawMap(c, p, s, map, tbtCurrent, tbtNext, lane);
        }
        applyThemeOverlay(c, p);
        return outFrame;
    }

    private boolean eonStale() {
        long last = lastEonRxElapsed;
        return last == 0L || SystemClock.elapsedRealtime() - last > EON_STALE_MS;
    }

    private void drawDriving(Canvas c, Paint p, JSONObject s) {
        JSONObject l = layout(s);
        boolean stale = eonStale();
        boolean enabled = !stale && s.optBoolean("enabled", false);

        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(lc(l, "driveBg", Color.rgb(239, 241, 242)));
        c.drawRect(0f, 0f, DRIVE_RIGHT, 462f, p);

        world.draw(c, p, stale ? null : s, enabled, egoCar, otherCar, worldOdoM,
                lc(l, "driveBg", Color.rgb(239, 241, 242)),
                lc(l, "roadTop", Color.rgb(226, 229, 231)),
                lc(l, "roadBottom", Color.rgb(216, 220, 223)),
                lc(l, "pathColor", Color.rgb(24, 126, 224)),
                configuredRadarInfo, configuredBuildings);

        drawBlinkers(c, p, s, stale);

        int save = beginElement(c, l, "lights", 70f, 28f);
        drawLights(c, p, s);
        c.restoreToCount(save);

        int save2 = beginElement(c, l, "prnd", 90f, 116f);
        drawPrnd(c, p, s.optString("gear", "--"));
        c.restoreToCount(save2);

        int save3 = beginElement(c, l, "speed", DRIVE_CX, 74f);
        drawSpeed(c, p, stale ? -1 : s.optInt("speed", 0));
        c.restoreToCount(save3);

        drawModeAndEta(c, p, s);
        drawRange(c, p, s);

        p.setStyle(Paint.Style.STROKE);
        p.setColor(Color.rgb(202, 207, 210));
        p.setStrokeWidth(1f);
        c.drawLine(18f, 129f, 934f, 129f, p);

        int save4 = beginElement(c, l, "wheel", 70f, 171f);
        drawSteeringWheel(c, p, 70f, 171f, (float) s.optDouble("steer", 0d), enabled);
        c.restoreToCount(save4);

        int save5 = beginElement(c, l, "set", DRIVE_CX, 171f);
        drawSetSpeed(c, p, DRIVE_CX, 171f, s.optInt("set", 0), enabled);
        c.restoreToCount(save5);

        int save6 = beginElement(c, l, "camera", 882f, 171f);
        drawCamera(c, p, 882f, 171f, s.optInt("camera", 0), s.optInt("cameraDist", 0),
                s.optBoolean("cameraSection", false));
        c.restoreToCount(save6);

        int save7 = beginElement(c, l, "lead", 82f, 415f);
        drawLeadCard(c, p, s.optJSONObject("lead"));
        c.restoreToCount(save7);

        int save8 = beginElement(c, l, "tpms", 865f, 415f);
        drawTpms(c, p, s.optJSONObject("tpms"));
        c.restoreToCount(save8);

        if (stale) {
            p.setShader(null);
            p.setStyle(Paint.Style.FILL);
            p.setColor(Color.argb(210, 26, 30, 34));
            scratchRect.set(307f, 296f, 645f, 356f);
            c.drawRoundRect(scratchRect, 10f, 10f, p);
            text(c, p, lang("EON 연결 끊김", "EON LINK LOST"), DRIVE_CX, 322f, 24f,
                    Color.rgb(255, 148, 118), Paint.Align.CENTER);
            text(c, p, lastEonAddress, DRIVE_CX, 345f, 15f, Color.rgb(186, 194, 200), Paint.Align.CENTER);
        }

        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(2f);
        p.setColor(Color.rgb(188, 194, 198));
        scratchRect.set(2f, 2f, DRIVE_RIGHT - 2f, 458f);
        c.drawRoundRect(scratchRect, 18f, 18f, p);
    }

    private void drawBlinkers(Canvas c, Paint p, JSONObject s, boolean stale) {
        if (stale || ((SystemClock.elapsedRealtime() / 500L) & 1L) != 0L) {
            return;
        }
        if (s.optBoolean("leftBlinker", false)) {
            drawBlinker(c, p, 336f, 386f, true);
        }
        if (s.optBoolean("rightBlinker", false)) {
            drawBlinker(c, p, 616f, 386f, false);
        }
    }

    private void drawBlinker(Canvas c, Paint p, float x, float y, boolean left) {
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(72, 226, 118));
        scratchPath.rewind();
        if (left) {
            scratchPath.moveTo(x - 16f, y);
            scratchPath.lineTo(x + 10f, y - 13f);
            scratchPath.lineTo(x + 10f, y + 13f);
        } else {
            scratchPath.moveTo(x + 16f, y);
            scratchPath.lineTo(x - 10f, y - 13f);
            scratchPath.lineTo(x - 10f, y + 13f);
        }
        scratchPath.close();
        c.drawPath(scratchPath, p);
    }

    private void drawSpeed(Canvas c, Paint p, int speed) {
        String value = speed < 0 ? "--" : Integer.toString(speed);
        text(c, p, value, DRIVE_CX, 74f, 68f, Color.rgb(18, 18, 18), Paint.Align.CENTER);
        text(c, p, "KM", DRIVE_CX, 117f, 16f, Color.rgb(104, 111, 116), Paint.Align.CENTER);
    }

    private void drawPrnd(Canvas c, Paint p, String gear) {
        float x = 26f;
        String[] items = {"P", "R", "N", "D"};
        for (String g : items) {
            text(c, p, g, x, 116f, 30f,
                    g.equals(gear) ? Color.rgb(18, 18, 18) : Color.rgb(174, 179, 182), Paint.Align.LEFT);
            x += 42f;
        }
    }

    private void drawModeAndEta(Canvas c, Paint p, JSONObject s) {
        JSONObject l = layout(s);
        int mode = s.optInt("drivingMode", 3);
        String label = "NORM";
        int color = Color.rgb(68, 76, 82);
        if (mode == 1) {
            label = "SAFE";
            color = Color.rgb(226, 144, 38);
        } else if (mode == 2) {
            label = "ECO";
            color = Color.rgb(20, 160, 92);
        } else if (mode == 4) {
            label = "FAST";
            color = Color.rgb(222, 67, 70);
        }
        text(c, p, label, lv(l, "modeX", 930f), lv(l, "modeY", 116f), lv(l, "modeSize", 29f),
                color, Paint.Align.RIGHT);

        JSONObject navi = s.optJSONObject("navi");
        if (navi == null || !navi.optBoolean("active", false)) {
            return;
        }
        int remain = navi.optInt("remainTime", 0);
        if (remain <= 0) {
            return;
        }
        long etaMs = System.currentTimeMillis() + remain * 1000L;
        String eta = new SimpleDateFormat("HH:mm", Locale.KOREA).format(new Date(etaMs));
        float etaRight = lv(l, "etaRight", 800f);
        float etaY = lv(l, "etaY", 116f);
        float etaTimeSize = lv(l, "etaTimeSize", 27f);
        float etaLabelSize = lv(l, "etaLabelSize", 14f);
        float gap = lv(l, "etaGap", 8f);
        p.setTypeface(Typeface.create("sans", Typeface.BOLD));
        p.setTextSize(etaTimeSize);
        float etaWidth = p.measureText(eta);
        text(c, p, eta, etaRight, etaY, etaTimeSize, Color.rgb(68, 76, 82), Paint.Align.RIGHT);
        text(c, p, lang("도착", "ETA"), etaRight - etaWidth - gap, etaY - 1f, etaLabelSize,
                Color.rgb(68, 76, 82), Paint.Align.RIGHT);
    }

    private void drawRange(Canvas c, Paint p, JSONObject s) {
        double km = s.optDouble("distanceToEmpty", -1d);
        if (!Double.isFinite(km) || km < 0d) {
            return;
        }
        text(c, p, lang("주행", "RANGE"), 932f, 28f, 12f, Color.rgb(104, 111, 116), Paint.Align.RIGHT);
        text(c, p, String.format(Locale.US, "%.0f km", km), 932f, 51f, 20f,
                Color.rgb(36, 42, 46), Paint.Align.RIGHT);
    }

    private void drawLights(Canvas c, Paint p, JSONObject s) {
        float x = 21f;
        if (s.optBoolean("lowBeam", false)) {
            drawLamp(c, p, x, 28f, 0);
            x += 45f;
        }
        if (s.optBoolean("highBeam", false)) {
            drawLamp(c, p, x, 28f, 1);
            x += 45f;
        }
        if (s.optBoolean("frontFog", false)) {
            drawLamp(c, p, x, 28f, 2);
        }
    }

    private void drawLamp(Canvas c, Paint p, float x, float y, int kind) {
        int color = kind == 1 ? Color.rgb(44, 128, 238) : Color.rgb(39, 177, 89);
        p.setShader(null);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(3f);
        p.setColor(color);
        scratchRect.set(x + 22f, y - 10f, x + 36f, y + 10f);
        c.drawArc(scratchRect, 90f, 180f, false, p);
        for (int i = -1; i <= 1; i++) {
            float yy = y + i * 7f;
            if (kind == 2) {
                c.drawLine(x, yy + 4f, x + 18f, yy, p);
            } else {
                c.drawLine(x, yy, x + 18f, yy, p);
            }
        }
        if (kind == 2) {
            scratchPath.rewind();
            scratchPath.moveTo(x + 9f, y - 12f);
            scratchPath.lineTo(x + 13f, y - 6f);
            scratchPath.lineTo(x + 9f, y);
            scratchPath.lineTo(x + 13f, y + 6f);
            scratchPath.lineTo(x + 9f, y + 12f);
            c.drawPath(scratchPath, p);
        }
    }

    private void drawSteeringWheel(Canvas c, Paint p, float cx, float cy, float angle, boolean enabled) {
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(enabled ? Color.rgb(18, 95, 225) : Color.rgb(92, 101, 107));
        c.drawCircle(cx, cy, 36f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(4f);
        p.setColor(Color.rgb(246, 248, 249));
        c.drawCircle(cx, cy, 28f, p);
        double rad = Math.toRadians(-angle);
        double[] spokes = {-90d, 30d, 150d};
        for (double deg : spokes) {
            double a = rad + Math.toRadians(deg);
            c.drawLine(cx + (float) Math.cos(a) * 7f, cy + (float) Math.sin(a) * 7f,
                    cx + (float) Math.cos(a) * 24f, cy + (float) Math.sin(a) * 24f, p);
        }
        p.setStyle(Paint.Style.FILL);
        c.drawCircle(cx, cy, 6f, p);
    }

    private void drawSetSpeed(Canvas c, Paint p, float cx, float cy, int set, boolean enabled) {
        boolean valid = enabled && set > 0 && set < 255;
        int accent = valid ? Color.rgb(18, 149, 224) : Color.rgb(139, 147, 152);
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(246, 247, 247));
        c.drawCircle(cx, cy, 36f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(6f);
        p.setColor(accent);
        c.drawCircle(cx, cy, 36f, p);
        text(c, p, valid ? Integer.toString(set) : "--", cx, cy + 9f, 29f,
                Color.rgb(18, 18, 18), Paint.Align.CENTER);
        text(c, p, "SET", cx, cy + 55f, 14f, accent, Paint.Align.CENTER);
    }

    private void drawCamera(Canvas c, Paint p, float cx, float cy, int limit, int dist, boolean section) {
        if (limit <= 0) {
            return;
        }
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(250, 250, 250));
        c.drawCircle(cx, cy, 36f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(6f);
        p.setColor(Color.rgb(220, 45, 45));
        c.drawCircle(cx, cy, 36f, p);
        text(c, p, Integer.toString(limit), cx, cy + 9f, 29f, Color.rgb(20, 20, 20), Paint.Align.CENTER);
        if (dist > 0) {
            text(c, p, (section ? lang("구간 ", "ZONE ") : "") + distanceText(dist), cx, cy + 60f, 18f,
                    Color.rgb(18, 18, 18), Paint.Align.CENTER);
        }
    }

    private void drawLeadCard(Canvas c, Paint p, JSONObject lead) {
        if (configuredRadarInfo == 0) {
            return;
        }
        scratchRect.set(8f, 376f, 156f, 454f);
        drawCard(c, p, scratchRect);
        double d = lead == null ? 0d : lead.optDouble("d", 0d);
        double v = lead == null ? 0d : lead.optDouble("v", 0d);
        boolean showDistance = configuredRadarInfo == 2 || configuredRadarInfo == 4;
        text(c, p, lang("앞차", "LEAD"), 18f, 400f, 13f, Color.rgb(103, 111, 116), Paint.Align.LEFT);
        text(c, p, (!showDistance || d <= 0d) ? "--" : String.format(Locale.US, "%.0f m", d),
                145f, 400f, 19f, Color.rgb(18, 18, 18), Paint.Align.RIGHT);
        p.setStyle(Paint.Style.STROKE);
        p.setColor(Color.rgb(195, 201, 204));
        p.setStrokeWidth(1f);
        c.drawLine(16f, 414f, 148f, 414f, p);
        text(c, p, lang("상대", "REL"), 18f, 440f, 13f, Color.rgb(103, 111, 116), Paint.Align.LEFT);
        text(c, p, d > 0d ? String.format(Locale.US, "%+.0f km/h", v) : "--",
                145f, 440f, 17f, Color.rgb(18, 18, 18), Paint.Align.RIGHT);
    }

    private void drawTpms(Canvas c, Paint p, JSONObject tpms) {
        scratchRect.set(791f, 376f, 939f, 454f);
        drawCard(c, p, scratchRect);
        text(c, p, "TPMS", 865f, 394f, 12f, Color.rgb(86, 94, 100), Paint.Align.CENTER);
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(95, 102, 107));
        scratchRect.set(852f, 404f, 878f, 446f);
        c.drawRoundRect(scratchRect, 4f, 4f, p);
        text(c, p, tpmsText(tpmsValue(tpms, "fl")), 840f, 417f, 16f, Color.rgb(18, 18, 18), Paint.Align.RIGHT);
        text(c, p, tpmsText(tpmsValue(tpms, "fr")), 890f, 417f, 16f, Color.rgb(18, 18, 18), Paint.Align.LEFT);
        text(c, p, tpmsText(tpmsValue(tpms, "rl")), 840f, 443f, 16f, Color.rgb(18, 18, 18), Paint.Align.RIGHT);
        text(c, p, tpmsText(tpmsValue(tpms, "rr")), 890f, 443f, 16f, Color.rgb(18, 18, 18), Paint.Align.LEFT);
    }

    private String tpmsText(float v) {
        return (v < 5f || v > 60f) ? "--" : Integer.toString(Math.round(v));
    }

    private float tpmsValue(JSONObject t, String key) {
        return t == null ? -1f : (float) t.optDouble(key, -1d);
    }

    private void drawAtc(Canvas c, Paint p, JSONObject s) {
        JSONObject navi = s.optJSONObject("navi");
        int atcMode = s.optInt("atcMode", 0);
        if (atcMode < 1 || atcMode > 3 || navi == null || !navi.optBoolean("active", false)) {
            return;
        }
        if (!navi.optBoolean("guidanceLive", false)) {
            return;
        }
        int dist = navi.optInt("turnDist", -1);
        if (dist < 0) {
            return;
        }
        // 목적지(경로)가 살아 있을 때만 표시한다. 안내 없이 떠 있지 않도록.
        if (navi.optInt("remainDist", 0) <= 0) {
            return;
        }
        scratchRect.set(962f, 330f, 1106f, 456f);
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(31, 35, 38));
        c.drawRoundRect(scratchRect, 10f, 10f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(2f);
        p.setColor(Color.rgb(238, 241, 243));
        c.drawRoundRect(scratchRect, 10f, 10f, p);

        String title = navi.optString("title", lang("경로 안내", "ROUTE GUIDE"));
        if (title.length() > 12) {
            title = title.substring(0, 11) + "…";
        }
        text(c, p, title, 1034f, 350f, 14f, Color.rgb(248, 249, 250), Paint.Align.CENTER);

        boolean blink = dist > 350 || ((SystemClock.elapsedRealtime() / 500L) & 1L) == 0L;
        if (blink) {
            drawAtcArrow(c, p, 1034f, 388f, navi.optInt("turnType", 0));
        }
        text(c, p, distanceText(dist), 1034f, 428f, 22f, Color.rgb(248, 249, 250), Paint.Align.CENTER);
        int remain = navi.optInt("remainDist", 0);
        if (remain > 0) {
            text(c, p, lang("남은 ", "LEFT ") + distanceText(remain), 1034f, 447f, 11f,
                    Color.rgb(180, 188, 194), Paint.Align.CENTER);
        }
    }

    private static final int TBT_GREEN = Color.rgb(31, 122, 72);

    /**
     * 1행(현재 회전)은 티맵 PNG 를 그대로 쓴다 — 고가차도·복잡분기 아이콘이
     * 살아 있어야 하기 때문. 2행(다음 회전)만 1행과 같은 녹색으로 직접 그려
     * 폭을 줄인다. 패킷의 navi.next 가 없으면 그리지 않는다.
     */
    private void drawTbtNext(Canvas c, Paint p, JSONObject navi) {
        if (navi == null || !navi.optBoolean("active", false)
                || navi.optInt("remainDist", 0) <= 0) {
            return;
        }
        JSONObject next = navi.optJSONObject("next");
        if (next == null) {
            return;
        }
        int nextDist = next.optInt("turnDist", -1);
        if (nextDist < 0) {
            return;
        }
        p.setStyle(Paint.Style.FILL);
        p.setColor(TBT_GREEN);
        scratchRect.set(968f, 134f, 1200f, 190f);
        c.drawRoundRect(scratchRect, 8f, 8f, p);
        drawScaledArrow(c, p, 1000f, 162f, next.optInt("turnType", 0), 0.78f);
        text(c, p, distanceText(nextDist), 1034f, 174f, 29f, Color.WHITE, Paint.Align.LEFT);
    }

    private void drawScaledArrow(Canvas c, Paint p, float cx, float cy, int type, float scale) {
        int save = c.save();
        c.scale(scale, scale, cx, cy);
        drawAtcArrow(c, p, cx, cy, type);
        c.restoreToCount(save);
    }

    private void drawAtcArrow(Canvas c, Paint p, float cx, float cy, int type) {
        int direction = 0;
        if (type == 12 || type == 16 || type == 20 || type == 3 || type == 5) {
            direction = -1;
        } else if (type == 13 || type == 18 || type == 21 || type == 4 || type == 6) {
            direction = 1;
        }
        p.setShader(null);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(6f);
        p.setStrokeCap(Paint.Cap.ROUND);
        p.setStrokeJoin(Paint.Join.ROUND);
        p.setColor(Color.WHITE);
        scratchPath.rewind();
        if (type == 14) {
            scratchPath.moveTo(cx + 10f, cy + 18f);
            scratchPath.lineTo(cx + 10f, cy - 8f);
            scratchPath.quadTo(cx + 10f, cy - 23f, cx - 8f, cy - 23f);
            scratchPath.quadTo(cx - 26f, cy - 23f, cx - 26f, cy - 5f);
            scratchPath.moveTo(cx - 26f, cy - 5f);
            scratchPath.lineTo(cx - 35f, cy - 14f);
            scratchPath.moveTo(cx - 26f, cy - 5f);
            scratchPath.lineTo(cx - 17f, cy - 14f);
        } else if (direction != 0) {
            scratchPath.moveTo(cx, cy + 20f);
            scratchPath.lineTo(cx, cy - 7f);
            scratchPath.lineTo(cx + direction * 25f, cy - 7f);
            scratchPath.moveTo(cx + direction * 25f, cy - 7f);
            scratchPath.lineTo(cx + direction * 14f, cy - 17f);
            scratchPath.moveTo(cx + direction * 25f, cy - 7f);
            scratchPath.lineTo(cx + direction * 14f, cy + 3f);
        } else {
            scratchPath.moveTo(cx, cy + 20f);
            scratchPath.lineTo(cx, cy - 22f);
            scratchPath.moveTo(cx, cy - 22f);
            scratchPath.lineTo(cx - 9f, cy - 11f);
            scratchPath.moveTo(cx, cy - 22f);
            scratchPath.lineTo(cx + 9f, cy - 11f);
        }
        c.drawPath(scratchPath, p);
        p.setStyle(Paint.Style.FILL);
    }

    private void drawCard(Canvas c, Paint p, RectF box) {
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(232, 235, 237));
        c.drawRoundRect(box, 9f, 9f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(2f);
        p.setColor(Color.rgb(158, 166, 171));
        c.drawRoundRect(box, 9f, 9f, p);
    }

    private String distanceText(int meters) {
        return meters >= 1000
                ? String.format(Locale.US, "%.1f km", meters / 1000f)
                : meters + " m";
    }

    // ── 우측 패널 ─────────────────────────────────────────────────────────

    private String systemValue(JSONObject system, String key, String unit) {
        if (system == null || system.isNull(key)) {
            return "--";
        }
        double value = system.optDouble(key, Double.NaN);
        return (Double.isNaN(value) || Double.isInfinite(value))
                ? "--" : String.format(Locale.US, "%.0f%s", value, unit);
    }

    /** 폭 192px 패널이라 라벨을 위, 값을 아래로 쌓는다. */
    private void drawSystemMetric(Canvas c, Paint p, float top, String label, String value) {
        float bottom = top + 84f;
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(16, 23, 32));
        scratchRect.set(1734f, top, 1914f, bottom);
        c.drawRoundRect(scratchRect, 9f, 9f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(2f);
        p.setColor(Color.rgb(55, 68, 80));
        c.drawRoundRect(scratchRect, 9f, 9f, p);
        text(c, p, label, 1824f, top + 26f, 15f, Color.rgb(145, 158, 168), Paint.Align.CENTER);
        text(c, p, value, 1824f, top + 66f, 30f, Color.rgb(235, 240, 245), Paint.Align.CENTER);
    }

    private void drawSystem(Canvas c, Paint p, JSONObject s) {
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(7, 12, 18));
        c.drawRect(SYSTEM_LEFT, 0f, SYSTEM_RIGHT, HEIGHT, p);
        text(c, p, "SYSTEM", 1824f, 26f, 17f, Color.rgb(235, 240, 245), Paint.Align.CENTER);

        JSONObject system = s.optJSONObject("system");
        if (system == null) {
            system = s;
        }
        drawSystemMetric(c, p, 42f, "CPU", systemValue(system, "cpu", "%"));
        drawSystemMetric(c, p, 132f, "TEMP", systemValue(system, "temp", "°C"));
        drawSystemMetric(c, p, 222f, "ENGINE", systemValue(system, "engineTemp", "°C"));
        drawSystemMetric(c, p, 312f, "COOLANT", systemValue(system, "coolantTemp", "°C"));

        double accel = s.optDouble("accel", Double.NaN);
        if (Double.isFinite(accel)) {
            text(c, p, "ACCEL", 1824f, 424f, 13f, Color.rgb(120, 132, 142), Paint.Align.CENTER);
            text(c, p, String.format(Locale.US, "%+.2f", accel), 1824f, 448f, 20f,
                    Color.rgb(173, 184, 192), Paint.Align.CENTER);
        }
    }

    private String lang(String ko, String en) {
        return configuredLanguage == 1 ? en : ko;
    }

    private boolean darkTheme() {
        if (configuredTheme == 1) {
            return true;
        }
        if (configuredTheme == 2) {
            return false;
        }
        int h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY);
        return h < 7 || h >= 19;
    }

    private void applyThemeOverlay(Canvas c, Paint p) {
        if (!darkTheme()) {
            return;
        }
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.argb(45, 0, 0, 0));
        c.drawRect(0f, 0f, WIDTH, HEIGHT, p);
    }

    private void updateTrip(JSONObject s, long now) {
        if (tripStartElapsed == 0L) {
            tripStartElapsed = now;
            tripLastElapsed = now;
            return;
        }
        long dt = Math.max(0L, Math.min(2000L, now - tripLastElapsed));
        tripLastElapsed = now;
        double speed = Math.max(0d, s.optDouble("speed", 0d));
        tripDistanceKm += dt * speed / 3600000d;
        // 건물 스크롤용 누적 거리(m). 오래 달려도 float 정밀도가 남도록 접어 준다.
        worldOdoM += (float) (dt * speed / 3600d);
        if (worldOdoM > 1.0e6f) {
            worldOdoM -= 1.0e6f;
        }
    }

    private void drawRightBase(Canvas c, Paint p, String title) {
        boolean dark = darkTheme();
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(dark ? Color.rgb(8, 13, 19) : Color.rgb(232, 235, 237));
        c.drawRect(MAP_LEFT, 0f, MAP_RIGHT, HEIGHT, p);
        text(c, p, title, MAP_CX, 42f, 27f, dark ? Color.WHITE : Color.rgb(25, 30, 34), Paint.Align.CENTER);
    }

    private void drawDebugRight(Canvas c, Paint p, JSONObject s) {
        drawRightBase(c, p, lang("실시간 디버그", "LIVE DEBUG"));
        JSONObject sys = s.optJSONObject("system");
        if (sys == null) {
            sys = s;
        }
        boolean dark = darkTheme();
        int fg = dark ? Color.rgb(235, 240, 245) : Color.rgb(25, 30, 34);
        int sub = dark ? Color.rgb(160, 172, 182) : Color.rgb(90, 100, 108);

        float y = 95f;
        text(c, p, String.format(Locale.US, "CPU %.0f%%   TEMP %.0f°C",
                sys.optDouble("cpu", 0d), sys.optDouble("temp", 0d)), 1000f, y, 25f, fg, Paint.Align.LEFT);
        y += 52f;
        text(c, p, String.format(Locale.US, "SPEED %d   SET %d   GAP %d",
                s.optInt("speed", 0), s.optInt("set", 0), s.optInt("gap", 0)),
                1000f, y, 23f, fg, Paint.Align.LEFT);
        y += 52f;
        JSONObject lead = s.optJSONObject("lead");
        if (lead != null) {
            text(c, p, String.format(Locale.US, "LEAD %.0fm  %+.0fkm/h",
                    lead.optDouble("d", 0d), lead.optDouble("v", 0d)), 1000f, y, 23f, fg, Paint.Align.LEFT);
        } else {
            text(c, p, "LEAD --", 1000f, y, 23f, sub, Paint.Align.LEFT);
        }
        y += 52f;
        text(c, p, String.format(Locale.US, "FPS %d   MAP %dfps   JPEG %d",
                configuredFps, Math.max(2, Math.min(5, s.optInt("hudMapFps", 5))), jpegQuality),
                1000f, y, 22f, fg, Paint.Align.LEFT);
        y += 52f;
        text(c, p, lang("S9 렌더링 / USB 출력", "S9 RENDER / USB OUTPUT"),
                1000f, y, 20f, sub, Paint.Align.LEFT);
    }

    private void drawTripRight(Canvas c, Paint p, JSONObject s) {
        drawRightBase(c, p, lang("주행 리포트", "TRIP REPORT"));
        long elapsed = tripStartElapsed != 0L ? SystemClock.elapsedRealtime() - tripStartElapsed : 0L;
        double hours = elapsed / 3600000d;
        double avg = hours > 1.0e-4d ? tripDistanceKm / hours : 0d;
        boolean dark = darkTheme();
        int fg = dark ? Color.rgb(235, 240, 245) : Color.rgb(25, 30, 34);
        int sub = dark ? Color.rgb(160, 172, 182) : Color.rgb(90, 100, 108);

        text(c, p, String.format(Locale.US, "%.1f km", tripDistanceKm), MAP_CX, 145f, 54f, fg, Paint.Align.CENTER);
        text(c, p, lang("주행거리", "DISTANCE"), MAP_CX, 178f, 18f, sub, Paint.Align.CENTER);
        text(c, p, String.format(Locale.US, "%02d:%02d", elapsed / 3600000L, (elapsed / 60000L) % 60L),
                MAP_CX, 255f, 48f, fg, Paint.Align.CENTER);
        text(c, p, lang("주행시간", "DRIVE TIME"), MAP_CX, 286f, 18f, sub, Paint.Align.CENTER);
        text(c, p, String.format(Locale.US, "AVG %.0f km/h", avg), MAP_CX, 363f, 35f, fg, Paint.Align.CENTER);
    }

    private void drawMap(Canvas c, Paint p, JSONObject s, Bitmap map, Bitmap tbtCurrent,
                         Bitmap tbtNext, Bitmap lane) {
        scratchIRect.set(MAP_LEFT, 0, MAP_RIGHT, HEIGHT);
        if (map == null || map.isRecycled()) {
            p.setShader(null);
            p.setStyle(Paint.Style.FILL);
            p.setColor(Color.BLACK);
            c.drawRect(scratchIRect, p);
            text(c, p, lang("TMAP 화면 대기", "WAITING FOR TMAP"), MAP_CX, 240f, 34f,
                    Color.GRAY, Paint.Align.CENTER);
            drawAtc(c, p, s);
            return;
        }
        p.setFilterBitmap(true);
        c.drawBitmap(map, null, scratchIRect, p);

        JSONObject l = layout(s);
        int save = beginElement(c, l, "tbt1", 1168f, 64f);
        drawNativeOverlay(c, p, tbtCurrent, 968f, 8f, 1368f, 120f, Paint.Align.LEFT);
        c.restoreToCount(save);
        int save2 = beginElement(c, l, "tbt2", 1084f, 156f);
        drawTbtNext(c, p, s.optJSONObject("navi"));
        c.restoreToCount(save2);
        int save3 = beginElement(c, l, "lane", 1395f, 408f);
        drawNativeOverlay(c, p, lane, 1130f, 366f, 1660f, 450f, Paint.Align.CENTER);
        c.restoreToCount(save3);

        // ATC 안내는 주행 패널에서 TMAP 패널 좌하단으로 이동했다.
        int save4 = beginElement(c, l, "atc", 1034f, 393f);
        drawAtc(c, p, s);
        c.restoreToCount(save4);
    }

    private void drawNativeOverlay(Canvas c, Paint p, Bitmap bitmap,
                                   float left, float top, float right, float bottom, Paint.Align align) {
        if (bitmap == null || bitmap.isRecycled() || bitmap.getWidth() <= 0 || bitmap.getHeight() <= 0) {
            return;
        }
        float boundsW = right - left;
        float boundsH = bottom - top;
        float scale = Math.min(boundsW / bitmap.getWidth(), boundsH / bitmap.getHeight());
        float w = bitmap.getWidth() * scale;
        float h = bitmap.getHeight() * scale;
        float x;
        if (align == Paint.Align.RIGHT) {
            x = right - w;
        } else if (align == Paint.Align.CENTER) {
            x = (left + right) * 0.5f - w / 2f;
        } else {
            x = left;
        }
        float y = top + (boundsH - h) / 2f;
        p.setAlpha(255);
        p.setFilterBitmap(true);
        scratchRect.set(x, y, x + w, y + h);
        c.drawBitmap(bitmap, null, scratchRect, p);
    }

    // ── 레이아웃 헬퍼 ─────────────────────────────────────────────────────

    private JSONObject layout(JSONObject s) {
        JSONObject l = s == null ? null : s.optJSONObject("layout");
        return l == null ? new JSONObject() : l;
    }

    private float lv(JSONObject l, String key, float def) {
        double v = l.optDouble(key, def);
        return Double.isFinite(v) ? (float) v : def;
    }

    private int lc(JSONObject l, String key, int def) {
        int v = l.optInt(key, -1);
        return v < 0 ? def : Color.rgb((v >> 16) & 255, (v >> 8) & 255, v & 255);
    }

    private int beginElement(Canvas c, JSONObject l, String name, float px, float py) {
        int save = c.save();
        float dx = lv(l, name + "Dx", 0f);
        float dy = lv(l, name + "Dy", 0f);
        float scale = Math.max(0.5f, Math.min(2f, lv(l, name + "Scale", 1f)));
        c.translate(dx, dy);
        c.scale(scale, scale, px, py);
        return save;
    }

    private static void text(Canvas c, Paint p, String value, float x, float y,
                             float size, int color, Paint.Align align) {
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setTypeface(Typeface.create("sans", Typeface.BOLD));
        p.setTextSize(size);
        p.setTextAlign(align);
        p.setColor(color);
        c.drawText(value, x, y, p);
    }

    // ── 종료 ──────────────────────────────────────────────────────────────

    private void recycleRef(AtomicReference<Bitmap> ref) {
        Bitmap old = ref.getAndSet(null);
        if (old != null) {
            old.recycle();
        }
    }

    @Override
    public void onDestroy() {
        running.set(false);
        starter.removeCallbacksAndMessages(null);
        workersStarted = false;
        serviceRunning = false;
        mapConnected = false;
        usbConnected = false;
        usbError = false;
        usbStatus = "서비스 중지됨";
        measuredFps = 0.0f;
        lastJpegBytes = 0;
        if (display != null) {
            display.close();
        }
        if (egoCar != null) {
            egoCar.recycle();
            egoCar = null;
        }
        if (otherCar != null) {
            otherCar.recycle();
            otherCar = null;
        }
        if (outFrame != null) {
            outFrame.recycle();
            outFrame = null;
            outCanvas = null;
        }
        synchronized (assetLock) {
            recycleRef(mapFrame);
            recycleRef(tbtCurrentFrame);
            recycleRef(tbtNextFrame);
            recycleRef(laneFrame);
        }
        if (usbReceiverRegistered) {
            try {
                unregisterReceiver(usbReceiver);
            } catch (Exception ignored) {
            }
            usbReceiverRegistered = false;
        }
        releaseWakeLock();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
