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
    double optDouble(String key,double fallback){ return values.containsKey(key)?((Number)values.get(key)).doubleValue():fallback; }
    int optInt(String key,int fallback){ return values.containsKey(key)?(Integer)values.get(key):fallback; }
    String optString(String key,String fallback){ return values.containsKey(key)?(String)values.get(key):fallback; }
  }
  static class Bitmap { boolean recycled; boolean isRecycled(){return recycled;} void recycle(){recycled=true;} int getWidth(){return 200;} static int mapH=150; int getHeight(){return mapH;} }
  static class SystemClock { static long now=100000L; static long elapsedRealtime(){return now;} }
  static class Rect { void set(int a,int b,int c,int d){} }
  static class RectF { void set(float a,float b,float c,float d){} }
  static class Color { static int BLACK=0xff000000,GRAY=0xff888888,WHITE=0xffffffff; static int argb(int a,int r,int g,int b){return (a<<24)|(r<<16)|(g<<8)|b;} static int rgb(int r,int g,int b){return argb(255,r,g,b);} }
  static class Paint {
    enum Style { FILL } enum Align { CENTER,LEFT,RIGHT }
    int alpha=255, lastBitmapAlpha=-1; Object shader;
    void setShader(Object x){shader=x;} void setStyle(Style x){} void setColor(int x){alpha=x>>>24;} void setFilterBitmap(boolean x){}
    void setAlpha(int x){alpha=x;} void reset(){alpha=255;} void setAntiAlias(boolean b){}
  }
  static class Canvas {
    int banners, maps, markers, nextCalls, etaCalls, sourceBadges, nativeOverlays;
    List<String> events=new ArrayList<>();
    int save(){return 1;} void restoreToCount(int x){}
    int fades, minimumFadeAlpha=255;
    void drawRect(Rect r,Paint p){}
    void drawRect(float a,float b,float c,float d,Paint p){fades++;if(p.shader!=null)minimumFadeAlpha=Math.min(minimumFadeAlpha,p.alpha);}
    void drawBitmap(Bitmap b,Object src,Rect dst,Paint p){
      if(b==null||b.isRecycled())throw new AssertionError("invalid map drawn"); maps++; p.lastBitmapAlpha=p.alpha;
    }
    void drawRoundRect(RectF r,float x,float y,Paint p){banners++;}
  }
  static final int MAP_LEFT=960,HEIGHT=576,TBT_GREEN=0xff1f7a48,TBT_GREEN_DARK=0xff146239;
  final Rect scratchIRect=new Rect(); final RectF scratchRect=new RectF(); boolean frameDark;
  static final float JUNCTION_LEFT=962f, JUNCTION_W=340f, KAKAO_MARKER_X=0.78f, TMAP_MARKER_X=0.5f, NAVER_MARKER_X=0.63f, ETA_H=58f, ETA_TOP=HEIGHT-58f;
  static final long MAP_FADE_MS=300L; Bitmap fadingMap; long fadingMapSince, mapShownSince;
  Bitmap trimmedLane(Bitmap b){return b==null||b.isRecycled()?null:b;}
  int mapRight(){return 1920;} float mapCenterX(){return 1440f;}
  boolean naviLayout; int configuredNavApp=1; static final int NAVI_MAP_RIGHT=1536; static final float NAVI_LANE_CX=920f, NAVI_LANE_H=87f, NAVI_LANE_MARKER_CX=768f, NAVI_LANE_BOTTOM=702f;
  static final float NAVI_BOTTOM_FADE_H=220f, NAVI_LEFT_FADE_END=390f, NAVI_RIGHT_FADE_START=1238f, NAVI_FADE_EDGE=60f; static final int NAVI_SIDE_FADE_ALPHA=110;
  static class Shader { enum TileMode { CLAMP } }
  static class LinearGradient { LinearGradient(float a,float b,float c,float d,int e,int f,Shader.TileMode m){} LinearGradient(float a,float b,float c,float d,int[] e,float[] f,Shader.TileMode m){} }
  static final float NAVI_COL_W=270f, NAVI_COL_LEFT=1246f, NAVI_TBT_TOP=62f, NAVI_ETA_RIGHT=1230f, INFO_Y=702f, NAVI_LANE_LEFT=400f;
  float overlayRight(){return naviLayout?NAVI_COL_LEFT-12f:mapRight();}
  int beginNaviGuide(Canvas c,float y,float anchor){return c.save();}
  int beginNaviGuide(Canvas c,float y,float anchor,float scale){return c.save();}
  int beginNaviGuide(Canvas c,float x,float y,float anchor,float scale){return c.save();}
  String lang(String ko,String en){return en;}
  boolean eonStaleForTest; boolean eonStale(){return eonStaleForTest;}
  JSONObject layout(JSONObject s){return new JSONObject();}
  int beginElement(Canvas c,JSONObject l,String k,float x,float y){return c.save();}
  void text(Canvas c,Paint p,String t,float x,float y,float size,int color,Paint.Align a){if(t.equals("729")){c.nextCalls++;c.events.add("next");}}
  void drawTmapVehicleMarker(Canvas c,Paint p,float x,float y){c.markers++;}
  void drawJunction(Canvas c,Paint p,float y){c.events.add("junction");}
  void drawNaviEta(Canvas c,Paint p,JSONObject s){c.etaCalls++;}
  void drawMapSourceBadge(Canvas c,Paint p,JSONObject s){c.sourceBadges++;c.events.add("source");}
  void drawNativeOverlay(Canvas c,Paint p,Bitmap b,float x,float y,float w,float h,Paint.Align a){
    if(b!=null&&!b.isRecycled()){c.nativeOverlays++;c.events.add("asset");}
  }
  boolean drawTurnIcon(Canvas c,Paint p,float x,float y,float size,int type,String title,int color,boolean b){return false;}
  void drawScaledArrow(Canvas c,Paint p,float x,float y,int type,float size,String title){}
  String distanceText(int n){return Integer.toString(n);}
  int nightMapMaskAlpha(){return frameDark?155:0;}
  static final int CAMERA_OVERSPEED_MARGIN_KPH=5;
  static final long CAMERA_WARNING_BLINK_MS=500L;
  /* PRODUCTION_METHODS */
  static JSONObject state(boolean active,int remain,int turn){return new JSONObject().put("navi",
    new JSONObject().put("active",active).put("remainDist",remain).put("turnDist",turn));}
  void check(String name,JSONObject state,Bitmap map,Bitmap signal,int banners,int maps,int overlays){
    Canvas c=new Canvas(); drawMap(c,new Paint(),state,map,null,null,null,signal);
    // The native map supplies its own marker; the HUD must not draw another.
    if(c.banners!=banners||c.maps!=maps||c.nativeOverlays!=overlays||c.markers!=0||c.nextCalls!=0||c.etaCalls!=1||c.sourceBadges!=1)
      throw new AssertionError(name+": banners="+c.banners+" maps="+c.maps+" overlays="+c.nativeOverlays+" markers="+c.markers+" next="+c.nextCalls+" eta="+c.etaCalls+" badge="+c.sourceBadges);
  }
  public static void main(String[] args){
    NavigationRenderCheck nooHud = new NavigationRenderCheck();
    JSONObject noo = new JSONObject().put("dir",0).put("cur",2).put("tgt",3).put("reason","blindspot");
    JSONObject nooState = new JSONObject().put("noo",noo).put("enabled",true);
    if(!"BLIND SPOT".equals(nooHud.nooEventText(nooState,1,true,400)))
      throw new AssertionError("NOO target hides blocking reason");
    noo.put("dir",1).put("reason","low_speed");
    if(!"WAIT SPEED".equals(nooHud.nooEventText(nooState,1,true,400)))
      throw new AssertionError("NOO request hides speed gate");
    nooState.put("atcDirection",1);
    if(!"R LANE CHG".equals(nooHud.nooEventText(nooState,1,true,400)))
      throw new AssertionError("active NOO must retain priority");
    noo.put("dir",0);
    if(!"R TURN".equals(nooHud.nooEventText(nooState,1,true,400)))
      throw new AssertionError("lane wait must not hide active turn");
    // Camera warning must work identically with cruise on and off.
    for(boolean cruise:new boolean[]{false,true}) {
      JSONObject warning=new JSONObject().put("enabled",cruise).put("speed",65).put("camera",60).put("cameraDist",400);
      if(!cameraWarningOn(warning,true,0L)||!cameraWarningOn(warning,true,499L)
          ||cameraWarningOn(warning,true,500L)||!cameraWarningOn(warning,true,1000L))
        throw new AssertionError("camera blink timing, cruise="+cruise);
      warning.put("speed",64);
      if(cameraWarningOn(warning,true,0L))throw new AssertionError("below +5 km/h threshold");
      warning.put("speed",65).put("cameraDist",0);
      if(cameraWarningOn(warning,true,0L))throw new AssertionError("passed camera warning");
      warning.put("cameraDist",400).put("camera",0);
      if(cameraWarningOn(warning,true,0L))throw new AssertionError("missing camera limit");
      warning.put("camera",60);
      if(cameraWarningOn(warning,false,0L))throw new AssertionError("stale vehicle warning");
    }
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
    // Navigation switch: the old map fades out, then the new one fades in.
    Bitmap old=new Bitmap(); hud.fadingMap=old; hud.fadingMapSince=SystemClock.now; hud.mapShownSince=0L;
    SystemClock.now+=150L; Paint p=new Paint(); Canvas c=new Canvas();
    hud.drawMap(c,p,state(true,1000,200),null,null,null,null,null);
    if(c.maps!=1||p.lastBitmapAlpha<100||p.lastBitmapAlpha>155||p.alpha!=255)
      throw new AssertionError("fade-out midway: maps="+c.maps+" alpha="+p.lastBitmapAlpha+" after="+p.alpha);
    SystemClock.now+=200L; c=new Canvas();
    hud.drawMap(c,new Paint(),state(true,1000,200),null,null,null,null,null);
    if(c.maps!=0||!old.isRecycled()||hud.fadingMap!=null) throw new AssertionError("fade-out did not finish");
    Bitmap fresh=new Bitmap(); p=new Paint(); c=new Canvas();
    hud.drawMap(c,p,state(true,1000,200),fresh,null,null,null,null);
    if(c.maps!=1||p.lastBitmapAlpha!=0||p.alpha!=255) throw new AssertionError("fade-in start alpha="+p.lastBitmapAlpha);
    SystemClock.now+=150L; p=new Paint();
    hud.drawMap(new Canvas(),p,state(true,1000,200),fresh,null,null,null,null);
    if(p.lastBitmapAlpha<100||p.lastBitmapAlpha>155) throw new AssertionError("fade-in midway alpha="+p.lastBitmapAlpha);
    SystemClock.now+=200L; p=new Paint();
    hud.drawMap(new Canvas(),p,state(true,1000,200),fresh,null,null,null,null);
    if(p.lastBitmapAlpha!=255) throw new AssertionError("fade-in end alpha="+p.lastBitmapAlpha);
    // Lane guidance follows the phone's lane image: shown during guidance and
    // also in free driving without a destination; the phone clears it when gone.
    Bitmap lane=new Bitmap(); c=new Canvas();
    hud.drawMap(c,new Paint(),state(true,1000,200),null,null,null,lane,null);
    if(c.nativeOverlays!=1) throw new AssertionError("lane hidden during guidance");
    for(JSONObject idle:new JSONObject[]{new JSONObject(),state(false,1000,200),state(true,0,-1)}){
      c=new Canvas(); hud.drawMap(c,new Paint(),idle,null,null,null,lane,null);
      if(c.nativeOverlays!=1) throw new AssertionError("lane hidden in free driving");
    }
    hud.eonStaleForTest=true; c=new Canvas();
    hud.drawMap(c,new Paint(),state(true,1000,200),null,null,null,lane,null);
    if(c.nativeOverlays!=0) throw new AssertionError("lane shown while EON link is stale");
    hud.eonStaleForTest=false;
    // Navi layout (map 8 : drive 2) draws the same map, guidance and overlays.
    hud.naviLayout=true; Bitmap.mapH=100; SystemClock.now+=1000L;
    hud.check("navi layout",state(true,1000,200),new Bitmap(),signal,1,1,1);
    Canvas fade=new Canvas(); hud.drawMap(fade,new Paint(),state(true,1000,200),new Bitmap(),null,null,null,null);
    if(fade.fades!=3) throw new AssertionError("navi side/bottom fade count="+fade.fades);
    if(fade.minimumFadeAlpha!=255) throw new AssertionError("night mask weakened navi fade: "+fade.minimumFadeAlpha);
    for(int app:new int[]{1,2,3}){
      JSONObject fixture=state(true,6400,0).put("hudNavApp",app);
      fixture.optJSONObject("navi").put("next",new JSONObject().put("turnType",12).put("turnDist",729));
      Canvas order=new Canvas(); hud.drawMap(order,new Paint(),fixture,new Bitmap(),null,null,null,signal);
      if(order.nextCalls!=1||order.events.indexOf("next")<order.events.indexOf("junction")
          ||order.events.indexOf("next")<order.events.indexOf("asset")
          ||order.events.indexOf("next")<order.events.indexOf("source"))
        throw new AssertionError("navi next maneuver obscured for app="+app+": "+order.events);
    }
    // All navigation apps must wait for the correctly shaped map, even after
    // the old ten-second timeout. Switching back must reject the wide frame.
    Bitmap.mapH=150; SystemClock.now+=1000L;
    for(int app:new int[]{1,2,3}){
      JSONObject fixture=state(true,1000,200).put("hudNavApp",app);
      Canvas narrow=new Canvas(); hud.drawMap(narrow,new Paint(),fixture,new Bitmap(),null,null,null,null);
      if(narrow.maps!=0) throw new AssertionError("drive-shaped frame stretched in navi layout, app="+app);
      SystemClock.now+=10001L;
      narrow=new Canvas(); hud.drawMap(narrow,new Paint(),fixture,new Bitmap(),null,null,null,null);
      if(narrow.maps!=0) throw new AssertionError("drive-shaped frame stretched after timeout, app="+app);
      Bitmap.mapH=100;
      Canvas corrected=new Canvas(); hud.drawMap(corrected,new Paint(),fixture,new Bitmap(),null,null,null,null);
      if(corrected.maps!=1) throw new AssertionError("wide map did not recover, app="+app);
      hud.naviLayout=false;
      corrected=new Canvas(); hud.drawMap(corrected,new Paint(),fixture,new Bitmap(),null,null,null,null);
      if(corrected.maps!=0) throw new AssertionError("wide frame stretched in drive layout, app="+app);
      Bitmap.mapH=150;
      corrected=new Canvas(); hud.drawMap(corrected,new Paint(),fixture,new Bitmap(),null,null,null,null);
      if(corrected.maps!=1) throw new AssertionError("drive map did not recover, app="+app);
      hud.naviLayout=true;
    }
    hud.naviLayout=false;
    System.out.println("9 navigation rendering cases and map switch fades passed, including traffic signal overlay");
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
    magisk_service = (Path(__file__).resolve().parents[1] /
                      "magisk/nmirror_fast_start/service.sh").read_text(encoding="utf-8")
    # The map socket must react promptly to the first EON packet and reconnect;
    # otherwise the HUD can spend an avoidable half-second on WAITING FOR MAP.
    assert "MAP_ADDRESS_WAIT_MS = 100L" in source
    assert "MAP_RETRY_WAIT_MS = 200L" in source
    map_loop = source.split("private void mapLoop()", 1)[1].split(
        "/** Low-rate CPU inference", 1)[0]
    assert "SystemClock.sleep(MAP_ADDRESS_WAIT_MS)" in map_loop
    assert "SystemClock.sleep(MAP_RETRY_WAIT_MS)" in map_loop
    # Outside the vehicle, EON still sends UDP and serves map frames while
    # carState/controlsState are unavailable. Keep that state distinct from an
    # actual link timeout so the HUD does not claim that EON disconnected.
    driving = source.split("private void drawDriving(", 1)[1].split(
        "/**\n     * openpilot", 1)[0]
    assert "boolean linkStale = eonStale();" in driving
    assert 'boolean drivingValid = s.optBoolean("drivingValid", true);' in driving
    assert "boolean stale = linkStale || !drivingValid;" in driving
    assert 'lang("차량 데이터 대기", "WAITING FOR VEHICLE")' in driving
    assert 'boolean stale = eonStale() || !s.optBoolean("drivingValid", true);' not in driving
    # A remembered wide navigation layout must not stretch the map while the
    # phone is being used on the bench or EON has no live vehicle streams.
    assert "boolean vehicleLayoutAvailable = !eonStale()" in source
    assert '&& s.optBoolean("drivingValid", true);' in source
    assert "naviLayout = configuredNaviLayout && vehicleLayoutAvailable" in source
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
    turzx_displays = (Path(__file__).resolve().parents[1] /
                      "app/src/main/java/ai/comma/remotehud/TurzxDisplays.java").read_text(encoding="utf-8")
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
    # Once the selected navigation has appeared during boot, Back/Home is a
    # user action. Neither later display events nor Magisk retries may reopen it.
    assert "bootNavigationReady.get()" in source
    assert "bootNavigationReady.set(true)" in source
    assert 'NAV_WAS_READY="$NAV_READY"' not in magisk_service
    assert '[ "$NAV_WAS_READY" != 1 ]' in magisk_service
    assert "displayAwareLaunchCommand(component, false,\n" in source
    assert 'awk -v want=0' in source
    assert 'mResumedActivity:' in source
    assert 'mirrorDisplays.registerDisplayListener' in source
    assert 'mirrorDisplays.unregisterDisplayListener' in source
    assert 'HudPixelBuffer.create(LEGACY_HEIGHT, WIDTH)' in source
    assert 'HudPixelBuffer.create(HEIGHT_123, WIDTH)' in source
    assert 'HudPixelBuffer.create(WIDTH, HEIGHT)' in source
    assert 'HudPixelBuffer.copy(c, phoneFrame, usbLogicalFrameBounds, phonePreviewPaint)' in source
    assert 'HudPixelBuffer.copy(c, phoneFrame, usb123LogicalFrameBounds, phonePreviewPaint)' in source
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
    assert "PID_97 = 0x0092" in turzx
    assert "PID_123 = 0x0123" in turzx
    assert "new TurzxDisplay(context, TurzxDisplay.PID_97" in turzx_displays
    assert "new TurzxDisplay(context, TurzxDisplay.PID_123" in turzx_displays
    assert "void sendJpegs(byte[] legacyJpeg, byte[] wideJpeg)" in turzx_displays
    assert 'xml.contains(appName)' in usb_approver
    assert 'xml.contains("TURZX1.00")' in usb_approver
    assert 'attributeIsTrue(checkNode, "checked")' in usb_approver
    # Every new session primes both native JPEG surfaces before live HUD.
    assert "usbNeedsPrimeFrame = true;" in source
    assert "sendUsbPrimerFrame();" in source
    primer = source.split("private void sendUsbPrimerFrame()", 1)[1].split(
        "private void handleUsbError", 1)[0]
    assert "c.drawColor(Color.BLACK)" in primer
    assert primer.count("display.sendJpegs") == 2
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
    replace_asset = source.split("private static Bitmap decodeAsset", 1)[1].split(
        "private static void recycleAndClear", 1)[0]
    assert "inJustDecodeBounds = true" in replace_asset
    assert "pixels > 8_000_000L" in replace_asset
    # Use complete source methods, so restoring the old early return fails this test.
    methods = []
    for start, end in (("    private void drawMap(", "    private void drawMapSourceBadge("),
                       ("    private static boolean cameraWarningOn(", "    private void drawSpeedRing("),
                       ("    private float drawTbtBanner(", "    private float drawTbtImage("),
                       ("    private void drawTbtNext(", "    // 회전 방향 종류."),
                       ("    private static boolean naviGuiding(", "    private Bitmap laneTrimSource;")):
        methods.append(source[source.index(start):source.index(end)])
    methods.append(source[source.index("    private String nooSide("):source.index("    private static final int TBT_GREEN")])
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

