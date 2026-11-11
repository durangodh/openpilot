package ai.comma.naverhud;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.SystemClock;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** Receives the HUD's small car icon, with vehicle lamps and BSD already drawn. */
final class NaverCarMarker {
    private static final int PORT = 7213;
    private static final int REQUEST = 0x43415231; // CAR1
    private static final int MAX_PNG = 64 * 1024;

    private final ExecutorService reader = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "naver-hud-car-icon");
        thread.setDaemon(true);
        return thread;
    });
    private final AtomicBoolean fetching = new AtomicBoolean();
    private volatile long nextPoll;
    private volatile long lastReply;
    private volatile Bitmap icon;
    private Socket socket;
    private DataOutputStream output;
    private DataInputStream input;
    private int state = -1;

    private Bitmap converted;
    private Object overlayImage;
    private Method bitmapFactory;
    private boolean checkedFactory;

    Bitmap current() {
        long now = SystemClock.elapsedRealtime();
        if (now >= nextPoll && fetching.compareAndSet(false, true)) {
            nextPoll = now + 100;
            reader.execute(this::poll);
        }
        return lastReply != 0 && now - lastReply < 800 ? icon : null;
    }

    Object overlay(Class<?> type, Bitmap bitmap) throws ReflectiveOperationException {
        if (bitmap == converted) return overlayImage;
        if (!checkedFactory) {
            checkedFactory = true;
            try {
                bitmapFactory = type.getMethod("fromBitmap", Bitmap.class);
            } catch (NoSuchMethodException ignored) {
                for (Method method : type.getMethods()) {
                    if (Modifier.isStatic(method.getModifiers()) && method.getReturnType() == type
                            && method.getParameterCount() == 1
                            && method.getParameterTypes()[0] == Bitmap.class) {
                        if (bitmapFactory != null) {
                            bitmapFactory = null;
                            break;
                        }
                        bitmapFactory = method;
                    }
                }
            }
            if (bitmapFactory == null) NaverHudLog.line("HUD car marker: bitmap factory unavailable");
        }
        overlayImage = bitmapFactory == null ? null : bitmapFactory.invoke(null, bitmap);
        converted = bitmap;
        return overlayImage;
    }

    private void poll() {
        try {
            if (socket == null) {
                socket = new Socket();
                socket.connect(new InetSocketAddress("127.0.0.1", PORT), 250);
                socket.setTcpNoDelay(true);
                socket.setSoTimeout(350);
                output = new DataOutputStream(socket.getOutputStream());
                input = new DataInputStream(socket.getInputStream());
            }
            output.writeInt(REQUEST);
            output.writeInt(state);
            output.flush();
            int nextState = input.readInt();
            int length = input.readInt();
            if (nextState < -1 || nextState > 31 || length < 0 || length > MAX_PNG) {
                throw new IOException("invalid car icon frame");
            }
            if (nextState == -1) {
                icon = null;
            } else if (length > 0) {
                byte[] bytes = new byte[length];
                input.readFully(bytes);
                Bitmap frame = BitmapFactory.decodeByteArray(bytes, 0, length);
                if (frame == null) throw new IOException("invalid car icon PNG");
                icon = frame;
            } else if (state != nextState || icon == null) {
                throw new IOException("missing car icon PNG");
            }
            state = nextState;
            lastReply = SystemClock.elapsedRealtime();
        } catch (IOException | RuntimeException error) {
            if (socket != null) {
                try { socket.close(); } catch (IOException ignored) { }
            }
            socket = null;
            state = -1;
            icon = null;
            nextPoll = SystemClock.elapsedRealtime() + 2000;
        } finally {
            fetching.set(false);
        }
    }
}
