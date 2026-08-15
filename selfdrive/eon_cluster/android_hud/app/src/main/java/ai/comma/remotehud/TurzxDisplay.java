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

public final class TurzxDisplay {
    static final String ACTION_PERMISSION = "ai.comma.remotehud.USB_PERMISSION";
    static final int PID = 146;
    static final int VID = 7358;
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
        return findTargetDevice == null ? "미연결 · 1CBE:0092" : !this.manager.hasPermission(findTargetDevice) ? "연결됨 · USB 권한 승인 대기" : isOpen() ? "연결됨 · USB 권한 허용" : "연결됨 · 여는 중";
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
                this.manager.requestPermission(this.device, PendingIntent.getBroadcast(this.context, this.device.getDeviceId(), new Intent(ACTION_PERMISSION).setPackage(this.context.getPackageName()), 167772160));
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
        if (this.connection != null && this.connection.claimInterface(this.intf, true)) {
            exchange(command(10, -1, 0), true);
            Thread.sleep(200L);
            exchange(command(13, 8, 0), false);
            Thread.sleep(30L);
            exchange(command(15, 8, 8), false);
            Thread.sleep(50L);
            drainInput(1, 2);
            exchange(command(14, 8, 66), false);
            return true;
        }
        close();
        return false;
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

    public synchronized void sendJpeg(byte[] bArr) throws Exception {
        if (!isOpen()) {
            throw new Exception("display closed");
        }
        command(101, -1, 0);
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
        drainInput(2, 2);
        int bulkTransfer = this.connection.bulkTransfer(this.out, bArr2, length2, 3000);
        if (bulkTransfer != length2) {
            throw new Exception("USB 프레임 쓰기 실패 " + bulkTransfer + "/" + length2);
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
        ByteBuffer.wrap(bArr, 4, 4).order(ByteOrder.LITTLE_ENDIAN).putInt((((((calendar.get(11) * 60) + calendar.get(12)) * 60) + calendar.get(13)) * 1000) + calendar.get(14));
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
            try {
                this.connection.releaseInterface(this.intf);
            } catch (Exception e) {
            }
            this.connection.close();
        }
        this.connection = null;
        this.intf = null;
        this.out = null;
        this.in = null;
    }
}
