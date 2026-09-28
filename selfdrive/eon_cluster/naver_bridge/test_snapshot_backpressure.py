"""Deterministic slow-sender / late SDK callback regression harness."""
import test_car_snapshot as harness

harness.SOURCES['android/os/Looper.java'] = '''package android.os;
public class Looper { static final Looper MAIN=new Looper();
public static Looper getMainLooper(){return MAIN;} }'''
harness.SOURCES['android/os/HandlerThread.java'] = '''package android.os;
public class HandlerThread { public HandlerThread(String n){} public void start(){}
public Looper getLooper(){return new Looper();} }'''
harness.SOURCES['android/os/Handler.java'] = '''package android.os;
public class Handler {
  public static java.util.List<Runnable> q=new java.util.ArrayList<>();
  public static java.util.List<Runnable> work=new java.util.ArrayList<>();
  final boolean main; public Handler(Looper l){main=l==Looper.getMainLooper();}
  public boolean post(Runnable r){(main?q:work).add(r);return true;}
  public static void drainMain(){while(!q.isEmpty())q.remove(0).run();}
  public static void drain(){drainMain();while(!work.isEmpty())work.remove(0).run();}
}'''
harness.SOURCES['com/naver/map/carrot/CarrotNaverBridge.java'] = harness.SOURCES[
    'com/naver/map/carrot/CarrotNaverBridge.java'].replace(
        'public int sent;', 'public Runnable onSend; public boolean failSend; public int sent;').replace(
        'sent++;lastName=name;',
        'if(failSend){failSend=false;throw new RuntimeException("test socket failure");}'
        'if(onSend!=null){Runnable r=onSend;onSend=null;r.run();}sent++;lastName=name;')

harness.SOURCES['com/naver/map/carrot/SnapCheck.java'] = r'''
package com.naver.map.carrot;
import android.os.*;
import android.graphics.Bitmap;
import com.naver.maps.map.NaverMap;

public class SnapCheck {
  static void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }
  static NaverMap.SnapshotReadyCallback request(CarrotNaverBridge b, NaverMap map) {
    SystemClock.now += 200;
    CarrotCarMapSnapshot.capture(b);
    // Run only the newest main-thread request; leave the sender stalled.
    Handler.drainMain();
    return map.cb;
  }
  static Bitmap frame() { return new Bitmap(1024, 600); }
  public static void main(String[] args) {
    CarrotNaverBridge b = new CarrotNaverBridge();
    FakeProvider p = new FakeProvider(); p.map = new NaverMap();
    CarrotCarMapSnapshot.provider(p);
    for (int i = 0; i < 50; i++) {
      request(b, p.map).a(frame());
      check(Handler.work.size() == 1, "sender queue must remain bounded");
    }
    Handler.drain();
    check(b.sent == 1, "50 captured frames must coalesce into one latest frame");

    NaverMap.SnapshotReadyCallback old = request(b, p.map);
    SystemClock.now += 1300;
    NaverMap.SnapshotReadyCallback current = request(b, p.map);
    old.a(frame());
    check(Handler.work.isEmpty(), "timed-out callback must not enqueue a stale frame");
    int count = p.map.requests;
    CarrotCarMapSnapshot.capture(b); Handler.drain();
    check(p.map.requests == count, "old callback must not unlock current request");
    current.a(frame()); Handler.drain();
    check(b.sent == 2, "current callback accepted");
    current.a(frame()); Handler.drain();
    check(b.sent == 2, "duplicate callback ignored");

    old = request(b, p.map);
    FakeProvider next = new FakeProvider(); next.map = new NaverMap();
    CarrotCarMapSnapshot.provider(next);
    current = request(b, next.map);
    old.a(frame()); Handler.drain();
    check(b.sent == 2, "old renderer must not replace the new source");
    current.a(frame()); Handler.drain();
    check(b.sent == 3, "new renderer accepted");

    request(b, next.map).a(frame());
    SystemClock.now += 1300; Handler.drain();
    check(b.sent == 3, "expired pending frame dropped");
    request(b, next.map).a(null);
    request(b, next.map).a(frame()); Handler.drain();
    check(b.sent == 4, "null callback releases request without marking a good frame");

    request(b, next.map).a(frame());
    CarrotCarMapSnapshot.provider(p); Handler.drain();
    check(b.sent == 4, "source replacement drops pending frame");
    request(b, p.map).a(frame()); Handler.drain();
    check(b.sent == 5, "sender recovers after source replacement");
    b.onSend = new Runnable() { public void run() {
      for (int i=0;i<8;i++) request(b, p.map).a(frame());
      check(Handler.work.isEmpty(), "in-flight sender must not create more sender jobs");
    }};
    request(b, p.map).a(frame()); Handler.drain();
    check(b.sent == 7, "send in progress followed by only newest of eight frames");
    b.failSend=true;
    request(b, p.map).a(frame()); Handler.drain();
    check(b.sent == 7, "failed send not counted");
    request(b, p.map).a(frame()); Handler.drain();
    check(b.sent == 8, "sender recovers after socket exception");
    System.out.println("PASS bounded queue, timeout, stale/duplicate/null callbacks, source switch, frame expiry");
  }
}
'''

if __name__ == '__main__':
    harness.main()
