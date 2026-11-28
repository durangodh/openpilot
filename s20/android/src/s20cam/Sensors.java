package s20cam;

import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;

import java.io.File;
import java.io.RandomAccessFile;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;

/**
 * Accelerometer + gyroscope -> imu.ring, read by s20sensord in the chroot (s20/sensord/imu_ring.py, same layout).
 * Header 64 bytes: u32 magic 'S20I', u32 version 1, u32 record size 32, u32 capacity, u64 records written.
 * Record i at 64 + 32*(n % capacity): u64 seq (n + 1, 0 while writing), u32 Android sensor type,
 *   f32 x, f32 y, f32 z (Android sensor frame, m/s^2 or rad/s), i64 timestamp ns (CLOCK_BOOTTIME).
 */
final class Sensors implements SensorEventListener {
  static final int MAGIC = 0x49303253;  // "S20I"
  static final int CAP = 4096, REC = 32, HDR = 64;

  private final MappedByteBuffer buf;
  // keep the manager reachable: when it was a local, GC finalized it after ~10 s and the listeners stopped
  private SensorManager sm;
  private HandlerThread thread;
  private long n = 0, nAcc = 0, nGyro = 0, t0 = System.nanoTime();

  Sensors(String path) throws Exception {
    RandomAccessFile raf = new RandomAccessFile(new File(path), "rw");
    long size = HDR + (long) CAP * REC;
    raf.setLength(size);
    buf = raf.getChannel().map(FileChannel.MapMode.READ_WRITE, 0, size);
    buf.order(ByteOrder.LITTLE_ENDIAN);
    buf.putInt(0, 0);
    for (int i = 0; i < CAP; i++) buf.putLong(HDR + i * REC, 0);
    buf.putLong(16, 0);
    buf.putInt(4, 1); buf.putInt(8, REC); buf.putInt(12, CAP);
    buf.putInt(0, MAGIC);
    new File(path).setReadable(true, false);
  }

  void start(int periodUs) {
    thread = new HandlerThread("s20imu");
    thread.start();
    Handler h = new Handler(thread.getLooper());
    sm = sensorManager(thread.getLooper());
    Sensor acc = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
    Sensor gyro = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE_UNCALIBRATED);
    if (gyro == null) gyro = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE);
    Log.i("imu: accelerometer " + (acc != null ? acc.getName() + " (" + acc.getVendor() + ")" : "none")
        + ", gyroscope " + (gyro != null ? gyro.getName() + " type " + gyro.getType() : "none"));
    if (acc != null) sm.registerListener(this, acc, periodUs, h);
    if (gyro != null) sm.registerListener(this, gyro, periodUs, h);
  }

  // a SensorManager bound to the shell context (op package com.android.shell), so the sensor service sees uid 2000's package
  private static SensorManager sensorManager(Looper looper) {
    try {
      Class<?> c = Class.forName("android.hardware.SystemSensorManager");
      java.lang.reflect.Constructor<?> k = c.getDeclaredConstructor(Context.class, Looper.class);
      k.setAccessible(true);
      return (SensorManager) k.newInstance(FakeContext.get(), looper);
    } catch (Throwable t) {
      Log.w("SystemSensorManager(shell context): " + t + ", using the system service");
      return (SensorManager) FakeContext.get().getSystemService(Context.SENSOR_SERVICE);
    }
  }

  @Override public void onSensorChanged(SensorEvent e) {
    int off = HDR + (int) (n % CAP) * REC;
    buf.putLong(off, 0);
    buf.putInt(off + 8, e.sensor.getType());
    buf.putFloat(off + 12, e.values[0]);
    buf.putFloat(off + 16, e.values[1]);
    buf.putFloat(off + 20, e.values[2]);
    buf.putLong(off + 24, e.timestamp);
    buf.putLong(off, n + 1);
    buf.putLong(16, ++n);
    if (e.sensor.getType() == Sensor.TYPE_ACCELEROMETER) nAcc++; else nGyro++;
    long now = System.nanoTime();
    if (now - t0 > 10_000_000_000L) {
      Log.i(String.format("imu: accel %.0f Hz, gyro %.0f Hz, last accel/gyro %s", nAcc * 1e9 / (now - t0), nGyro * 1e9 / (now - t0),
          String.format("[%.2f %.2f %.2f]", e.values[0], e.values[1], e.values[2])));
      nAcc = nGyro = 0; t0 = now;
    }
  }

  @Override public void onAccuracyChanged(Sensor s, int accuracy) {}
}
