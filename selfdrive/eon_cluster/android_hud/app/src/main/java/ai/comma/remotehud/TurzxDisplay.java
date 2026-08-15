package ai.comma.remotehud;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.hardware.usb.UsbConstants;
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

final class TurzxDisplay {
  static final int VID = 0x1CBE;
  static final int PID = 0x0092;
  static final String ACTION_PERMISSION = "ai.comma.remotehud.USB_PERMISSION";
  private final Context context;
  private UsbManager manager;
  private UsbDevice device;
  private UsbDeviceConnection connection;
  private UsbInterface intf;
  private UsbEndpoint out;
  private UsbEndpoint in;

  TurzxDisplay(Context context) { this.context = context; }

  boolean isOpen() { return connection != null && out != null; }

  boolean openOrRequestPermission() throws Exception {
    if (isOpen()) return true;
    manager = (UsbManager)context.getSystemService(Context.USB_SERVICE);
    device = null;
    for (UsbDevice candidate : manager.getDeviceList().values()) {
      if (candidate.getVendorId() == VID && candidate.getProductId() == PID) { device = candidate; break; }
    }
    if (device == null) return false;
    if (!manager.hasPermission(device)) {
      PendingIntent pi = PendingIntent.getBroadcast(context, 0, new Intent(ACTION_PERMISSION).setPackage(context.getPackageName()),
          PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
      manager.requestPermission(device, pi);
      return false;
    }
    intf = device.getInterface(0);
    for (int i = 0; i < intf.getEndpointCount(); i++) {
      UsbEndpoint ep = intf.getEndpoint(i);
      if (ep.getType() != UsbConstants.USB_ENDPOINT_XFER_BULK) continue;
      if (ep.getDirection() == UsbConstants.USB_DIR_OUT) out = ep; else in = ep;
    }
    if (out == null || in == null) throw new Exception("TURZX bulk endpoint missing");
    connection = manager.openDevice(device);
    if (connection == null || !connection.claimInterface(intf, true)) { close(); return false; }
    exchange(command(10, -1, 0), true);
    Thread.sleep(200);
    exchange(command(13, 8, 0), false);
    exchange(command(15, 8, 8), false);
    exchange(command(14, 8, 66), false);
    return true;
  }

  synchronized void sendJpeg(byte[] jpeg) throws Exception {
    if (!isOpen()) throw new Exception("display closed");
    byte[] header = command(101, -1, 0);
    int length = jpeg.length;
    // Length bytes are encrypted fields 8..11, so rebuild using a small raw field array.
    byte[] raw = rawCommand(101);
    raw[8] = (byte)(length >>> 24); raw[9] = (byte)(length >>> 16);
    raw[10] = (byte)(length >>> 8); raw[11] = (byte)length;
    header = encrypt(raw);
    byte[] payload = new byte[header.length + jpeg.length];
    System.arraycopy(header, 0, payload, 0, header.length);
    System.arraycopy(jpeg, 0, payload, header.length, jpeg.length);
    // Android 8.x limits one UsbRequest/bulkTransfer buffer to 16 KiB.  The
    // rooted Galaxy S9 used as the TMAP sender can still be on that generation,
    // so keep the byte stream continuous but split each JPEG into safe chunks.
    int offset = 0;
    while (offset < payload.length) {
      int count = Math.min(16384, payload.length - offset);
      byte[] chunk = new byte[count];
      System.arraycopy(payload, offset, chunk, 0, count);
      int written = connection.bulkTransfer(out, chunk, count, 2000);
      if (written != count) throw new Exception("short USB write " + written + "/" + count);
      offset += count;
    }
  }

  private void exchange(byte[] packet, boolean ack) throws Exception {
    int written = connection.bulkTransfer(out, packet, packet.length, 2000);
    if (written != packet.length) throw new Exception("TURZX command failed");
    if (ack) connection.bulkTransfer(in, new byte[512], 512, 2000);
  }

  private static byte[] rawCommand(int id) {
    byte[] raw = new byte[504];
    raw[0] = (byte)id; raw[2] = 0x1A; raw[3] = 0x6D;
    Calendar c = Calendar.getInstance();
    int millis = ((c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE)) * 60 + c.get(Calendar.SECOND)) * 1000 + c.get(Calendar.MILLISECOND);
    ByteBuffer.wrap(raw, 4, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(millis);
    return raw;
  }

  private static byte[] command(int id, int index, int value) throws Exception {
    byte[] raw = rawCommand(id);
    if (index >= 0) raw[index] = (byte)value;
    return encrypt(raw);
  }

  private static byte[] encrypt(byte[] raw) throws Exception {
    byte[] key = "slv3tuzx".getBytes("US-ASCII");
    Cipher cipher = Cipher.getInstance("DES/CBC/NoPadding");
    cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "DES"), new IvParameterSpec(key));
    byte[] encrypted = cipher.doFinal(raw);
    byte[] packet = new byte[512];
    System.arraycopy(encrypted, 0, packet, 0, encrypted.length);
    packet[510] = (byte)0xA1; packet[511] = 0x1A;
    return packet;
  }

  synchronized void close() {
    if (connection != null && intf != null) {
      try { connection.releaseInterface(intf); } catch (Exception ignored) { }
      connection.close();
    }
    connection = null; intf = null; out = null; in = null;
  }
}
