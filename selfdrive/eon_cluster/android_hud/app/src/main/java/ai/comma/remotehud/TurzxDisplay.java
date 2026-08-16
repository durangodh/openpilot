package ai.comma.remotehud;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Calendar;
import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * v0.17 — carrot-wip cluster_usb_display.py 의 복구 절차를 이식.
 *
 * v0.16 까지는 전송이 한 번 실패하면 close() 후 다시 열기만 반복했다. 패널이
 * 엔드포인트를 halt 시킨 상태면 그 재시도는 영원히 실패하고, 물리적으로 USB 를
 * 다시 꽂아야만 복구됐다. 여기서 추가한 것:
 *
 *   1. clearHalt()  — 양쪽 엔드포인트의 halt 를 CLEAR_FEATURE 로 해제.
 *                     매 전송 전과 오류 직후에 호출한다.
 *   2. 프레임 분할 전송 — 한 번에 다 밀지 않고 조각으로 나눠 쓰고, 실패한
 *                     지점에서 멈춘다. 부분 전송으로 패널이 고착되는 것을 줄인다.
 *   3. 초기화 재시도 — 첫 sync 가 실패하면 halt 해제 후 한 번 더 시도.
 *   4. 정상 종료   — 닫기 전에 밝기 0 을 보내 패널을 깨끗한 상태로 남긴다.
 *
 * 안드로이드에는 libusb 의 dev.reset() 에 해당하는 API 가 없다. 위 절차로도
 * 복구되지 않을 때를 위한 포트 리셋은 UsbPortReset(루트) 이 담당한다.
 */
public final class TurzxDisplay {
    static final String ACTION_PERMISSION = "ai.comma.remotehud.USB_PERMISSION";
    static final int PID = 146;
    static final int VID = 7358;

    /** carrot-wip USB_COMMAND_GAP_S = 0.2 */
    private static final long COMMAND_GAP_MS = 200L;
    /** 한 번에 밀어 넣는 최대 바이트. 실패 지점을 좁히고 고착을 줄인다. */
    private static final int CHUNK_BYTES = 16384;

    private UsbDeviceConnection connection;
    private final Context context;
    private UsbDevice device;
    private UsbEndpoint in;
    private UsbInterface intf;
    private UsbManager manager;
    private UsbEndpoint out;
    private int permissionRequestedDeviceId = -1;

    public TurzxDisplay(Context context) {
        this.context = context;
    }

    boolean isOpen() {
        return (this.connection == null || this.out == null) ? false : true;
    }

    public String describeStatus() {
        this.manager = (UsbManager) this.context.getSystemService("usb");
        UsbDevice findTargetDevice = findTargetDevice();
        return findTargetDevice == null ? "미연결 · 1CBE:0092"
                : !this.manager.hasPermission(findTargetDevice) ? "연결됨 · USB 권한 승인 대기"
                : isOpen() ? "연결됨 · USB 권한 허용" : "연결됨 · 여는 중";
    }

    /** 루트 포트 리셋용. 예: "1-1" (없으면 null) */
    public synchronized String deviceNameOrNull() {
        return this.device == null ? null : this.device.getDeviceName();
    }

    public synchronized boolean openOrRequestPermission() throws Exception {
        if (isOpen()) {
            return true;
        }
        this.manager = (UsbManager) this.context.getSystemService("usb");
        this.device = findTargetDevice();
        if (this.device == null) {
            this.permissionRequestedDeviceId = -1;
            return false;
        }
        if (!this.manager.hasPermission(this.device)) {
            if (this.permissionRequestedDeviceId != this.device.getDeviceId()) {
                this.permissionRequestedDeviceId = this.device.getDeviceId();
                this.manager.requestPermission(this.device, PendingIntent.getBroadcast(
                        this.context, this.device.getDeviceId(),
                        new Intent(ACTION_PERMISSION).setPackage(this.context.getPackageName()),
                        167772160));
            }
            return false;
        }
        this.permissionRequestedDeviceId = -1;
        this.intf = null;
        this.out = null;
        this.in = null;

        int i = 0;
        while (true) {
            if (i >= this.device.getInterfaceCount()) {
                break;
            }
            UsbInterface usbInterface = this.device.getInterface(i);
            UsbEndpoint usbEndpoint = null;
            UsbEndpoint usbEndpoint2 = null;
            for (int i2 = 0; i2 < usbInterface.getEndpointCount(); i2++) {
                UsbEndpoint endpoint = usbInterface.getEndpoint(i2);
                if (endpoint.getType() == 2) {
                    if (endpoint.getDirection() == 0) {
                        usbEndpoint = endpoint;
                    } else if (endpoint.getDirection() == 128) {
                        usbEndpoint2 = endpoint;
                    }
                }
            }
            if (usbEndpoint == null || usbEndpoint2 == null) {
                i++;
            } else {
                this.intf = usbInterface;
                this.out = usbEndpoint;
                this.in = usbEndpoint2;
                break;
            }
        }
        if (this.intf == null) {
            throw new Exception("TURZX bulk endpoint missing");
        }
        this.connection = this.manager.openDevice(this.device);
        if (this.connection == null || !this.connection.claimInterface(this.intf, true)) {
            close();
            return false;
        }

        // v0.17: 이전 세션이 남긴 halt 를 먼저 털어낸다. 재연결 직후의
        // "TURZX command failed" 는 대부분 여기서 해결된다.
        clearHalt();
        drainInput(5, 2);

        try {
            initialize();
        } catch (Exception first) {
            // carrot-wip: init 무응답이면 리셋 후 한 번 더.
            clearHalt();
            drainInput(5, 2);
            Thread.sleep(300L);
            initialize();
        }
        return true;
    }

    private void initialize() throws Exception {
        exchange(command(10, -1, 0), true);
        Thread.sleep(COMMAND_GAP_MS);
        exchange(command(13, 8, 0), false);
        Thread.sleep(30L);
        exchange(command(15, 8, 8), false);
        Thread.sleep(50L);
        drainInput(5, 2);
        exchange(command(14, 8, 66), false);
    }

    private UsbDevice findTargetDevice() {
        if (this.manager == null) {
            this.manager = (UsbManager) this.context.getSystemService("usb");
        }
        for (UsbDevice usbDevice : this.manager.getDeviceList().values()) {
            if (isTarget(usbDevice)) {
                return usbDevice;
            }
        }
        return null;
    }

    public static boolean isTarget(UsbDevice usbDevice) {
        return usbDevice != null && usbDevice.getVendorId() == VID && usbDevice.getProductId() == PID;
    }

    public synchronized void reset() {
        this.permissionRequestedDeviceId = -1;
        close();
        this.device = null;
    }

    /**
     * 양쪽 벌크 엔드포인트의 halt 를 해제한다.
     * USB 표준 요청: bmRequestType=0x02(endpoint), bRequest=0x01(CLEAR_FEATURE),
     * wValue=0(ENDPOINT_HALT), wIndex=엔드포인트 주소.
     */
    public synchronized void clearHalt() {
        if (this.connection == null) {
            return;
        }
        UsbEndpoint[] endpoints = new UsbEndpoint[]{this.out, this.in};
        for (UsbEndpoint endpoint : endpoints) {
            if (endpoint == null) {
                continue;
            }
            try {
                this.connection.controlTransfer(0x02, 0x01, 0, endpoint.getAddress(), null, 0, 200);
            } catch (Exception ignored) {
            }
        }
    }

    public synchronized void sendJpeg(byte[] bArr) throws Exception {
        if (!isOpen()) {
            throw new Exception("display closed");
        }
        int length = bArr.length;
        byte[] rawCommand = rawCommand(101);
        rawCommand[8] = (byte) (length >>> 24);
        rawCommand[9] = (byte) (length >>> 16);
        rawCommand[10] = (byte) (length >>> 8);
        rawCommand[11] = (byte) length;
        byte[] encrypt = encrypt(rawCommand);
        int length2 = encrypt.length + bArr.length;
        byte[] bArr2 = new byte[length2];
        System.arraycopy(encrypt, 0, bArr2, 0, encrypt.length);
        System.arraycopy(bArr, 0, bArr2, encrypt.length, bArr.length);

        // carrot-wip: 매 전송 전에 halt 해제 + 남은 응답 비우기.
        clearHalt();
        drainInput(2, 2);
        writeChunked(bArr2);
    }

    /**
     * 프레임을 조각내어 전송한다. 한 조각이라도 못 쓰면 그 자리에서 예외를
     * 던져, 나머지를 계속 밀어 넣어 패널을 더 헝클지 않는다.
     */
    private void writeChunked(byte[] payload) throws Exception {
        int offset = 0;
        byte[] chunk = null;
        while (offset < payload.length) {
            int size = Math.min(CHUNK_BYTES, payload.length - offset);
            if (chunk == null || chunk.length != size) {
                chunk = new byte[size];
            }
            System.arraycopy(payload, offset, chunk, 0, size);
            int written = this.connection.bulkTransfer(this.out, chunk, size, 3000);
            if (written != size) {
                throw new Exception("USB 프레임 쓰기 실패 " + (offset + Math.max(written, 0))
                        + "/" + payload.length);
            }
            offset += size;
        }
    }

    private void exchange(byte[] bArr, boolean z) throws Exception {
        if (this.connection.bulkTransfer(this.out, bArr, bArr.length, 2000) != bArr.length) {
            throw new Exception("TURZX command failed");
        }
        if (z && this.connection.bulkTransfer(this.in, new byte[512], 512, 2000) <= 0) {
            throw new Exception("TURZX sync response missing");
        }
    }

    private void drainInput(int i, int i2) {
        if (this.connection == null || this.in == null) {
            return;
        }
        for (int i3 = 0; i3 < i && this.connection.bulkTransfer(this.in, new byte[512], 512, i2) > 0; i3++) {
        }
    }

    private static byte[] rawCommand(int i) {
        byte[] bArr = new byte[504];
        bArr[0] = (byte) i;
        bArr[2] = 26;
        bArr[3] = 109;
        Calendar calendar = Calendar.getInstance();
        ByteBuffer.wrap(bArr, 4, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(
                (((((calendar.get(11) * 60) + calendar.get(12)) * 60) + calendar.get(13)) * 1000)
                        + calendar.get(14));
        return bArr;
    }

    private static byte[] command(int i, int i2, int i3) throws Exception {
        byte[] rawCommand = rawCommand(i);
        if (i2 >= 0) {
            rawCommand[i2] = (byte) i3;
        }
        return encrypt(rawCommand);
    }

    private static byte[] encrypt(byte[] bArr) throws Exception {
        byte[] bytes = "slv3tuzx".getBytes("US-ASCII");
        Cipher cipher = Cipher.getInstance("DES/CBC/NoPadding");
        cipher.init(1, new SecretKeySpec(bytes, "DES"), new IvParameterSpec(bytes));
        byte[] doFinal = cipher.doFinal(bArr);
        byte[] bArr2 = new byte[512];
        System.arraycopy(doFinal, 0, bArr2, 0, doFinal.length);
        bArr2[510] = -95;
        bArr2[511] = 26;
        return bArr2;
    }

    public synchronized void close() {
        if (this.connection != null && this.intf != null) {
            // carrot-wip close(): 밝기 0 을 보내고 정상 종료.
            try {
                this.connection.bulkTransfer(this.out, command(14, 8, 0), 512, 200);
            } catch (Exception ignored) {
            }
            try {
                this.connection.releaseInterface(this.intf);
            } catch (Exception ignored) {
            }
            this.connection.close();
        }
        this.connection = null;
        this.intf = null;
        this.out = null;
        this.in = null;
    }
}
