"""Execute the production drawMap/drawTbtBanner bodies against recording graphics.

This tests rendering decisions, not Android GPU pixels or device navigation.
"""
import argparse
from pathlib import Path
import subprocess


HARNESS = r'''
import java.util.*;
public class NavigationRenderCheck {
  static class JSONObject {
    Map<String,Object> values = new HashMap<>();
    JSONObject put(String key,Object value){ values.put(key,value); return this; }
    JSONObject optJSONObject(String key){ return (JSONObject)values.get(key); }
    boolean optBoolean(String key,boolean fallback){ return values.containsKey(key)?(Boolean)values.get(key):fallback; }
    int optInt(String key,int fallback){ return values.containsKey(key)?(Integer)values.get(key):fallback; }
    String optString(String key,String fallback){ return values.containsKey(key)?(String)values.get(key):fallback; }
  }
  static class Bitmap { boolean recycled; boolean isRecycled(){return recycled;} }
  static class Rect { void set(int a,int b,int c,int d){} }
  static class RectF { void set(float a,float b,float c,float d){} }
  static class Color { static int BLACK=0,GRAY=1,WHITE=2; static int argb(int a,int r,int g,int b){return 3;} }
  static class Paint {
    enum Style { FILL } enum Align { CENTER,LEFT,RIGHT }
    void setShader(Object x){} void setStyle(Style x){} void setColor(int x){} void setFilterBitmap(boolean x){}
  }
  static class Canvas {
    int banners, maps, markers, nextCalls, etaCalls, sourceBadges, nativeOverlays;
    int save(){return 1;} void restoreToCount(int x){}
    void drawRect(Rect r,Paint p){}
    void drawBitmap(Bitmap b,Object src,Rect dst,Paint p){
      if(b==null||b.isRecycled())throw new AssertionError("invalid map drawn"); maps++;
    }
    void drawRoundRect(RectF r,float x,float y,Paint p){banners++;}
  }
  static final int MAP_LEFT=960,HEIGHT=576,TBT_GREEN=4,TBT_GREEN_DARK=5;
  final Rect scratchIRect=new Rect(); final RectF scratchRect=new RectF(); boolean frameDark;
  int mapRight(){return 1920;} float mapCenterX(){return 1440f;}
  String lang(String ko,String en){return en;}
  JSONObject layout(JSONObject s){return new JSONObject();}
  int beginElement(Canvas c,JSONObject l,String k,float x,float y){return c.save();}
  void text(Canvas c,Paint p,String t,float x,float y,float size,int color,Paint.Align a){}
  void drawTmapVehicleMarker(Canvas c,Paint p,float x,float y){c.markers++;}
  void drawTbtNext(Canvas c,Paint p,JSONObject n,float x,float y){c.nextCalls++;}
  void drawJunction(Canvas c,Paint p,float y){}
  void drawNaviEta(Canvas c,Paint p,JSONObject s){c.etaCalls++;}
  void drawMapSourceBadge(Canvas c,Paint p,JSONObject s){c.sourceBadges++;}
  void drawNativeOverlay(Canvas c,Paint p,Bitmap b,float x,float y,float w,float h,Paint.Align a){
    if(b!=null&&!b.isRecycled())c.nativeOverlays++;
  }
  boolean drawTurnIcon(Canvas c,Paint p,float x,float y,float size,int type,String title,int color,boolean b){return false;}
  void drawScaledArrow(Canvas c,Paint p,float x,float y,int type,float size,String title){}
  String distanceText(int n){return Integer.toString(n);}
  /* PRODUCTION_METHODS */
  static JSONObject state(boolean active,int remain,int turn){return new JSONObject().put("navi",
    new JSONObject().put("active",active).put("remainDist",remain).put("turnDist",turn));}
  void check(String name,JSONObject state,Bitmap map,Bitmap signal,int banners,int maps,int overlays){
    Canvas c=new Canvas(); drawMap(c,new Paint(),state,map,null,null,null,signal);
    // The native map supplies its own marker; the HUD must not draw another.
    if(c.banners!=banners||c.maps!=maps||c.nativeOverlays!=overlays||c.markers!=0||c.nextCalls!=1||c.etaCalls!=1||c.sourceBadges!=1)
      throw new AssertionError(name+": banners="+c.banners+" maps="+c.maps+" overlays="+c.nativeOverlays+" markers="+c.markers+" next="+c.nextCalls+" eta="+c.etaCalls+" badge="+c.sourceBadges);
  }
  public static void main(String[] args){
    NavigationRenderCheck hud=new NavigationRenderCheck(); Bitmap map=new Bitmap();
    Bitmap signal=new Bitmap();
    hud.check("map and signal available",state(true,1000,200),map,signal,1,1,1);
    hud.check("map missing, signal available",state(true,1000,200),null,signal,1,0,1);
    map.recycled=true; hud.check("map recycled",state(true,1000,200),map,null,1,0,0);
    hud.frameDark=true; hud.check("dark map missing",state(true,1000,200),null,null,1,0,0);
    map.recycled=false; hud.check("map recovered",state(true,1000,200),map,null,1,1,0);
    hud.check("naver guidance before route summary",state(true,0,200),null,null,1,0,0);
    hud.check("ended without map",state(false,1000,200),null,null,0,0,0);
    hud.check("arrived without map",state(true,0,-1),null,null,0,0,0);
    hud.check("no navigation",new JSONObject(),null,null,0,0,0);
    System.out.println("9 navigation rendering cases passed, including traffic signal overlay");
  }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    source = (Path(__file__).resolve().parents[1] /
              "app/src/main/java/ai/comma/remotehud/HudService.java").read_text(encoding="utf-8")
    # GPS source replaces the old health badge, in its original top-row slot.
    badges = source.split("private void drawMapSourceBadge(", 1)[1].split(
        "private void drawJunction(", 1)[0]
    assert "drawGpsSourceBadge(c, p, right - width - 12f, top, height)" in badges
    assert "gpsSourceMonitor.snapshot()" in badges
    assert "NavSelectionProtocol.appLabel" in badges
    for old in ('"gpsState"', '"gpsInfo"', "drawCircle", "sourceTop", "drawGpsBadge("):
        assert old not in badges, old
    model_world = (Path(__file__).resolve().parents[1] /
                   "app/src/main/java/ai/comma/remotehud/ModelWorldGL.java").read_text(encoding="utf-8")
    turzx = (Path(__file__).resolve().parents[1] /
             "app/src/main/java/ai/comma/remotehud/TurzxDisplay.java").read_text(encoding="utf-8")
    activity = (Path(__file__).resolve().parents[1] /
                "app/src/main/java/ai/comma/remotehud/MainActivity.java").read_text(encoding="utf-8")
    manifest = (Path(__file__).resolve().parents[1] /
                "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    usb_granter = (Path(__file__).resolve().parents[1] /
                   "app/src/main/java/ai/comma/remotehud/UsbPermissionGranter.java").read_text(encoding="utf-8")
    usb_approver = (Path(__file__).resolve().parents[1] /
                    "app/src/main/java/ai/comma/remotehud/UsbPermissionAutoApprover.java").read_text(encoding="utf-8")
    usb_reset = (Path(__file__).resolve().parents[1] /
                 "app/src/main/java/ai/comma/remotehud/UsbPortReset.java").read_text(encoding="utf-8")
    map_store = (Path(__file__).resolve().parents[1] /
                 "app/src/main/java/ai/comma/remotehud/HudMapStore.java").read_text(encoding="utf-8")
    # A navigation button request must never suspend the TCP map stream. An
    # unacknowledged request previously left both the S9 and external HUD map
    # blank indefinitely.
    assert "if (!AppPrefs.pendingNavRequest(this).isEmpty())" not in source
    assert "generation == navigationGeneration" not in source
    # HUD selection follows the safe order: start the selected app, then notify
    # the new nMirror. It must not suspend or invalidate the map TCP stream.
    assert 'new Intent("com.aa.nmirror.SET_NAV_SOURCE")' in source
    assert 'sync.setPackage("com.aa.nmirror")' in source
    switch_body = source.split("static void switchNavApps", 1)[1].split(
        "private static void synchronizeNMirrorSelection", 1)[0]
    assert switch_body.index("waitFor()") < switch_body.index("synchronizeNMirrorSelection")
    assert switch_body.index("synchronizeNMirrorSelection") < switch_body.index("am force-stop")
    assert "NMIRROR_SYNC_ATTEMPTS = 4" in source
    assert "am broadcast --user 0" in source
    # 리모컨/갭버튼(창이 없는 백그라운드 Service)에서 dumpsys 탐지가 실패해도
    # 마지막으로 확실히 알던 nMirror 디스플레이로 대신 띄운다 — 없으면 기본
    # 화면(S9 자체)으로 새는 게 유일한 선택지였고, 그게 nMirror 화면만
    # 까맣게 남던 원인이었다.
    assert "displayAwareLaunchCommand(component, true,\n" in source
    assert "AppPrefs.getMirrorDisplayId(context)" in source
    assert "captureMirrorDisplayId(context, launchProcess)" in source
    assert 'am start --display 0 -n' in source
    assert 'display_id=$(dumpsys activity activities' not in source
    assert "scheduleBootNavigationSync();" in source
    assert "launchNavAppOnMirrorDisplay(context, selected)" in source
    assert "displayAwareLaunchCommand(component, false,\n" in source
    assert 'awk -v want=0' in source
    assert 'mResumedActivity:' in source
    assert 'mirrorDisplays.registerDisplayListener' in source
    assert 'mirrorDisplays.unregisterDisplayListener' in source
    assert 'HudPixelBuffer.create(HEIGHT, WIDTH)' in source
    assert 'HudPixelBuffer.create(WIDTH, HEIGHT)' in source
    assert 'HudPixelBuffer.copy(c, phoneFrame, usbLogicalFrameBounds, phonePreviewPaint)' in source
    assert 'c.drawBitmap(phoneFrame, 0f, 0f' not in source
    # A tap must open the selected navigation Activity on the display where
    # the settings screen is currently visible, not only update preferences.
    assert "launchNavigationOnCurrentDisplay(launch)" in activity
    assert "options.setLaunchDisplayId(Display.DEFAULT_DISPLAY)" in activity
    assert "EXTRA_NAV_FOREGROUND_LAUNCHED" in activity
    assert "foregroundLaunched ? null" in source
    assert "getDisplay().getDisplayId() != Display.DEFAULT_DISPLAY" in activity
    secondary_display = activity.split(
        "getDisplay().getDisplayId() != Display.DEFAULT_DISPLAY", 1)[1].split(
        "boolean fromUsbAttach", 1)[0]
    assert "AppPrefs.getNavApp(this)" in secondary_display
    assert "launchNavigationOnCurrentDisplay(launch)" in secondary_display
    assert "finish();" in secondary_display
    # Magisk can be unavailable for a few seconds after boot/package update.
    # A single failed read must not leave the S9 CPU row at "--" forever.
    assert "suUnavailable" not in source
    assert "nextSuStatsRetryElapsed = now + 30000L" in source
    assert "now >= nextSuStatsRetryElapsed" in source
    # The model/path road must mask the Static Map even while a pair of
    # camera-observed edges is temporarily short or stale.
    draw_road = model_world.split("private void drawRoad(", 1)[1].split(
        "private void drawFallbackRoad(", 1)[0]
    assert draw_road.index("drawFallbackRoad(path, scene, color)") < draw_road.index(
        "if (left == null || right == null)")
    # A normal USB open must not issue raw CLEAR_FEATURE: Android does not
    # reset its host-side data toggle with that request. Failed initialization
    # must close the connection so the next attempt starts clean.
    usb_open = turzx.split("public synchronized boolean openOrRequestPermission()", 1)[1].split(
        "private void initialize()", 1)[0]
    assert "clearHalt();" not in usb_open
    init_failure = usb_open.split("catch (Exception first)", 1)[1]
    assert init_failure.index("close();") < init_failure.index("return false;")
    # Try the no-dialog root framework grant first. Only after repeated vendor
    # ABI failures may a tightly scoped auto-approved system dialog recover USB.
    permission_path = usb_open.split("if (!manager.hasPermission(device))", 1)[1].split(
        "permissionRequestedDeviceId = -1", 1)[0]
    assert permission_path.index("UsbPermissionGranter.grantSilently") < permission_path.index(
        "manager.requestPermission")
    assert "silentGrantFailureStreak >= 3" in permission_path
    assert "UsbPermissionAutoApprover.watch(context)" in permission_path
    assert "USB_DEVICE_ATTACHED" not in manifest
    assert "USB_PERMISSION" not in source
    assert "grantDevicePermission" in usb_granter
    assert "setDevicePersistentPermission" in usb_granter
    assert "setDevicePackage" in usb_granter
    assert 'xml.contains(appName)' in usb_approver
    assert 'xml.contains("TURZX1.00")' in usb_approver
    assert 'attributeIsTrue(checkNode, "checked")' in usb_approver
    # Every new session primes the native 462x1920 JPEG surface before live HUD.
    assert "usbNeedsPrimeFrame = true;" in source
    assert "sendUsbPrimerFrame();" in source
    primer = source.split("private void sendUsbPrimerFrame()", 1)[1].split(
        "private void handleUsbError", 1)[0]
    assert "c.drawColor(Color.BLACK)" in primer
    assert primer.count("display.sendJpeg") == 2
    assert "SystemClock.sleep(USB_PRIMER_WARMUP_MS)" in primer
    assert "SystemClock.sleep(USB_PRIMER_SETTLE_MS)" in primer
    # An S9-only reboot leaves the powered TURZX decoder in its previous USB
    # session. Even when VID/PID is already visible, boot must rebind it once
    # before the first JPEG. Duplicate boot broadcasts must not reset it twice.
    assert "bootUsbPreparationDone" in source
    boot_usb = source.split("private void scheduleBootUsbHostRecovery()", 1)[1].split(
        "private boolean hasTurzxUsbDevice()", 1)[0]
    present_panel = boot_usb.split("if (hasTurzxUsbDevice())", 1)[1].split(
        "panelWasMissing = true", 1)[0]
    assert "UsbPortReset.resetPort(null)" in present_panel
    assert "nextUsbAttemptElapsed" in present_panel
    assert "usbNeedsPrimeFrame = true" in present_panel
    assert "bootUsbPreparationDone.set(true)" in present_panel
    ensure_usb = source.split("private boolean ensureUsbReady", 1)[1].split(
        "/**", 1)[0]
    assert "bootUsbHostRecoveryRunning.get()" in ensure_usb
    assert ensure_usb.index("bootUsbHostRecoveryRunning.get()") < ensure_usb.index(
        "display.isOpen()")
    # A fresh START_STICKY service can be recreated with a null Intent, so USB
    # preparation must be unconditional for a new service lifetime. Also close
    # the late-broadcast race at the final frame-output boundary.
    startup = source.split("running.set(true);", 1)[1].split("startWorkers();", 1)[0]
    assert "scheduleBootUsbHostRecovery();" in startup
    send_frame = source.split("private void sendUsbFrame", 1)[1].split(
        "private void sendUsbPrimerFrame", 1)[0]
    assert "synchronized (usbSessionGate)" in send_frame
    assert "bootUsbHostRecoveryRunning.get()" in send_frame
    assert send_frame.index("bootUsbHostRecoveryRunning.get()") < send_frame.index(
        "sendUsbFrameUnderGate")
    boot_schedule = source.split("private void scheduleBootUsbHostRecovery", 1)[1].split(
        "private boolean hasTurzxUsbDevice", 1)[0]
    assert boot_schedule.index("synchronized (usbSessionGate)") < boot_schedule.index(
        "bootUsbHostRecoveryRunning.compareAndSet")
    assert "RECENT_BOOT_UPTIME_MS" in boot_schedule
    assert "USB_RESTART_PREP_DELAY_MS" in boot_schedule
    assert "private volatile boolean usbNeedsPrimeFrame" in source
    # resetPort must verify that sysfs unbind and bind actually succeeded. The
    # old unconditional 'echo done' incorrectly reported success on failure.
    assert '"RESET_OK".equals(line.trim())' in usb_reset
    # Root/sysfs recovery must be bounded and must not block the render loop.
    assert "waitFor(ROOT_TIMEOUT_SECONDS, TimeUnit.SECONDS)" in usb_reset
    assert "destroyForcibly()" in usb_reset
    assert "waitFor(ROOT_TIMEOUT_SECONDS, TimeUnit.SECONDS)" in usb_approver
    stalled_open = source.split("private void recoverStalledOpen", 1)[1].split(
        "/**", 1)[0]
    usb_error = source.split("private void handleUsbError", 1)[1].split(
        "private void scheduleUsbPortReset", 1)[0]
    assert "scheduleUsbPortReset" in stalled_open and "UsbPortReset.resetPort" not in stalled_open
    assert "scheduleUsbPortReset" in usb_error and "UsbPortReset.resetPort" not in usb_error
    # Service shutdown closes blocking I/O and waits off the main thread before
    # recycling any frame that a worker might still be drawing.
    destroy = source.split("public void onDestroy()", 1)[1].split(
        "public IBinder onBind", 1)[0]
    assert destroy.index("stopWorkerIo();") < destroy.index("cleanupAfterWorkers")
    assert 'new Thread(this::cleanupAfterWorkers, "hud-worker-cleanup")' in destroy
    assert "worker.join(remaining)" in source
    assert "recycleRef(tbtCompactFrame)" in source
    assert "recycleRef(crossroadFrame)" in source
    # Reject decompression bombs and validate even exact-size map databases.
    replace_asset = source.split("private void replaceAsset", 1)[1].split(
        "private static void recycleAndClear", 1)[0]
    assert "inJustDecodeBounds = true" in replace_asset
    assert "pixels > 8_000_000L" in replace_asset
    accept_database = map_store.split("private boolean acceptDatabase", 1)[1].split(
        "private void activate", 1)[0]
    assert "validateDatabase(file)" in accept_database
    assert "if (length == expectedBytes)" not in accept_database
    # Use complete source methods, so restoring the old early return fails this test.
    methods = []
    for start, end in (("    private void drawMap(", "    private void drawMapSourceBadge("),
                       ("    private float drawTbtBanner(", "    private float drawTbtImage(")):
        methods.append(source[source.index(start):source.index(end)])
    args.work.mkdir(parents=True, exist_ok=True)
    java_file = args.work / "NavigationRenderCheck.java"
    java_file.write_text(HARNESS.replace("/* PRODUCTION_METHODS */", "\n".join(methods)), encoding="utf-8")
    suffix = ".exe" if (args.java_home / "bin/javac.exe").exists() else ""
    subprocess.run([str(args.java_home / ("bin/javac" + suffix)), "-encoding", "UTF-8",
                    "-d", str(args.work), str(java_file)], check=True)
    subprocess.run([str(args.java_home / ("bin/java" + suffix)), "-cp", str(args.work),
                    "NavigationRenderCheck"], check=True)


if __name__ == "__main__":
    main()

