from pathlib import Path

root = Path(__file__).resolve().parent
hud = root / "app/src/main/java/ai/comma/remotehud/HudService.java"
display = root / "app/src/main/java/ai/comma/remotehud/TurzxDisplay.java"

text = hud.read_text(encoding="utf-8")

# Runtime controls/state. These are fed by remote_hud_s9.py from the existing
# EonClusterHud* Params so the EON settings panel becomes the S9 settings panel.
old = """    private int usbErrorStreak;\n    private long frameIntervalMs = 125L;\n"""
new = """    private int usbErrorStreak;\n    private long frameIntervalMs = 125L;\n    private volatile long mapFrameIntervalMs = 200L;\n    private long lastMapAcceptedElapsed = 0L;\n    private int configuredFps = 8;\n    private int jpegQuality = 55;\n    private int appliedBrightness = -1;\n    private int configuredTheme = 0;\n    private int configuredLanguage = 0;\n    private int configuredRadarInfo = 4;\n    private int configuredScreenMode = 1;\n    private int configuredOrientation = 0;\n    private boolean configuredMirror = false;\n    private long tripStartElapsed = 0L;\n    private long tripLastElapsed = 0L;\n    private double tripDistanceKm = 0.0;\n"""
if old not in text:
    raise SystemExit("HudService fields anchor not found")
text = text.replace(old, new, 1)

# Throttle only MAP1 decoding to the UI's 2..5 fps setting. TBT/lane overlays
# remain event-driven and are accepted immediately.
old = """                    synchronized(assetLock){if(tagEquals(header,\"MAP1\"))replaceAsset(mapFrame,data);else if(tagEquals(header,\"TBT1\"))replaceAsset(tbtCurrentFrame,data);else if(tagEquals(header,\"TBT2\"))replaceAsset(tbtNextFrame,data);else if(tagEquals(header,\"LANE\"))replaceAsset(laneFrame,data);else throw new Exception(\"bad asset tag\");}\n"""
new = """                    synchronized(assetLock){if(tagEquals(header,\"MAP1\")){long mapNow=SystemClock.elapsedRealtime();if(lastMapAcceptedElapsed==0L||mapNow-lastMapAcceptedElapsed>=mapFrameIntervalMs){replaceAsset(mapFrame,data);lastMapAcceptedElapsed=mapNow;}}else if(tagEquals(header,\"TBT1\"))replaceAsset(tbtCurrentFrame,data);else if(tagEquals(header,\"TBT2\"))replaceAsset(tbtNextFrame,data);else if(tagEquals(header,\"LANE\"))replaceAsset(laneFrame,data);else throw new Exception(\"bad asset tag\");}\n"""
if old not in text:
    raise SystemExit("HudService map throttle anchor not found")
text = text.replace(old, new, 1)

old = """                usbStatus=\"연결됨 · USB 권한 허용\";usbConnected=true;usbError=false;usbErrorStreak=0;Bitmap frame;\n                synchronized(assetLock){frame=render(state.get(),mapFrame.get(),tbtCurrentFrame.get(),tbtNextFrame.get(),laneFrame.get());}\n"""
new = """                usbStatus=\"연결됨 · USB 권한 허용\";usbConnected=true;usbError=false;usbErrorStreak=0;Bitmap frame;\n                JSONObject currentState=state.get();\n                int requestedFps=Math.max(1,Math.min(15,currentState.optInt(\"hudFps\",8)));\n                if(requestedFps!=configuredFps){configuredFps=requestedFps;frameIntervalMs=Math.max(67L,1000L/configuredFps);}\n                int requestedMapFps=Math.max(2,Math.min(5,currentState.optInt(\"hudMapFps\",5)));\n                mapFrameIntervalMs=Math.max(200L,1000L/requestedMapFps);\n                jpegQuality=Math.max(20,Math.min(95,currentState.optInt(\"hudJpegQuality\",55)));\n                configuredTheme=Math.max(0,Math.min(2,currentState.optInt(\"hudTheme\",0)));\n                configuredLanguage=Math.max(0,Math.min(1,currentState.optInt(\"hudLanguage\",0)));\n                configuredRadarInfo=Math.max(0,Math.min(4,currentState.optInt(\"hudRadarInfo\",4)));\n                configuredScreenMode=Math.max(1,Math.min(3,currentState.optInt(\"hudScreenMode\",1)));\n                configuredOrientation=currentState.optInt(\"hudOrientation\",0)==2?2:0;\n                configuredMirror=currentState.optInt(\"hudMirror\",0)!=0;\n                int requestedBrightness=Math.max(1,Math.min(100,currentState.optInt(\"hudBrightness\",65)));\n                if(requestedBrightness!=appliedBrightness){display.setBrightness(requestedBrightness);appliedBrightness=requestedBrightness;}\n                updateTrip(currentState,now);\n                synchronized(assetLock){frame=render(currentState,mapFrame.get(),tbtCurrentFrame.get(),tbtNextFrame.get(),laneFrame.get());}\n"""
if old not in text:
    raise SystemExit("HudService render settings anchor not found")
text = text.replace(old, new, 1)

# Orientation and mirror are now done on S9 just before JPEG encode.
old = "ByteArrayOutputStream output=new ByteArrayOutputStream(180000);Matrix matrix=new Matrix();matrix.setRotate(-90.0f);Bitmap portrait=Bitmap.createBitmap(frame,0,0,frame.getWidth(),frame.getHeight(),matrix,true);frame.recycle();portrait.compress(Bitmap.CompressFormat.JPEG,55,output);"
new = "ByteArrayOutputStream output=new ByteArrayOutputStream(180000);Matrix matrix=new Matrix();matrix.setScale(configuredMirror?-1.0f:1.0f,1.0f);matrix.postRotate(configuredOrientation==2?90.0f:-90.0f);Bitmap portrait=Bitmap.createBitmap(frame,0,0,frame.getWidth(),frame.getHeight(),matrix,true);frame.recycle();portrait.compress(Bitmap.CompressFormat.JPEG,jpegQuality,output);"
if old not in text:
    raise SystemExit("HudService transform/JPEG anchor not found")
text = text.replace(old, new, 1)

old = """                display.clearHalt();\n                display.close();\n                if(usbErrorStreak>=USB_RESET_AFTER_ERRORS){\n"""
new = """                display.clearHalt();\n                display.close();\n                appliedBrightness=-1;\n                if(usbErrorStreak>=USB_RESET_AFTER_ERRORS){\n"""
if old not in text:
    raise SystemExit("HudService close anchor not found")
text = text.replace(old, new, 1)

# Screen mode 1=map/guide, 2=debug, 3=trip report. This preserves the old UI
# semantics but the panels are rendered locally on the S9.
old = """    private Bitmap render(JSONObject s,Bitmap map,Bitmap tbtCurrent,Bitmap tbtNext,Bitmap lane){Bitmap frame=Bitmap.createBitmap(WIDTH,HEIGHT,Bitmap.Config.RGB_565);Canvas c=new Canvas(frame);Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);c.drawColor(Color.rgb(5,8,12));drawDriving(c,p,s);JSONObject l=layout(s);int save=beginElement(c,l,\"system\",960,231);drawSystem(c,p,s);c.restoreToCount(save);drawMap(c,p,s,map,tbtCurrent,tbtNext,lane);return frame;}\n"""
new = """    private Bitmap render(JSONObject s,Bitmap map,Bitmap tbtCurrent,Bitmap tbtNext,Bitmap lane){Bitmap frame=Bitmap.createBitmap(WIDTH,HEIGHT,Bitmap.Config.RGB_565);Canvas c=new Canvas(frame);Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);c.drawColor(Color.rgb(5,8,12));drawDriving(c,p,s);JSONObject l=layout(s);int save=beginElement(c,l,\"system\",960,231);drawSystem(c,p,s);c.restoreToCount(save);if(configuredScreenMode==2)drawDebugRight(c,p,s);else if(configuredScreenMode==3)drawTripRight(c,p,s);else drawMap(c,p,s,map,tbtCurrent,tbtNext,lane);applyThemeOverlay(c,p);return frame;}\n"""
if old not in text:
    raise SystemExit("HudService screen mode anchor not found")
text = text.replace(old, new, 1)

# Pass full state to camera and radar card so language/radar settings can be
# applied locally.
text = text.replace("drawCamera(c,p,698,171,s.optInt(\"camera\",0),s.optInt(\"cameraDist\",0),s.optBoolean(\"cameraSection\",false));",
                    "drawCamera(c,p,s,698,171,s.optInt(\"camera\",0),s.optInt(\"cameraDist\",0),s.optBoolean(\"cameraSection\",false));", 1)
text = text.replace("drawLeadCard(c,p,s.optJSONObject(\"lead\"));",
                    "drawLeadCard(c,p,s,s.optJSONObject(\"lead\"));", 1)

old = """    private void drawCamera(Canvas c,Paint p,float cx,float cy,int limit,int dist,boolean section){if(limit<=0)return;p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(250,250,250));c.drawCircle(cx,cy,36,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(6);p.setColor(Color.rgb(220,45,45));c.drawCircle(cx,cy,36,p);text(c,p,Integer.toString(limit),cx,cy+9,29,Color.rgb(20,20,20),Paint.Align.CENTER);if(dist>0)text(c,p,(section?\"구간 \":\"\")+distanceText(dist),cx,cy+60,18,Color.rgb(18,18,18),Paint.Align.CENTER);}\n"""
new = """    private void drawCamera(Canvas c,Paint p,JSONObject s,float cx,float cy,int limit,int dist,boolean section){if(limit<=0)return;p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(250,250,250));c.drawCircle(cx,cy,36,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(6);p.setColor(Color.rgb(220,45,45));c.drawCircle(cx,cy,36,p);text(c,p,Integer.toString(limit),cx,cy+9,29,Color.rgb(20,20,20),Paint.Align.CENTER);if(dist>0)text(c,p,(section?lang(\"구간 \",\"ZONE \"):\"\")+distanceText(dist),cx,cy+60,18,Color.rgb(18,18,18),Paint.Align.CENTER);}\n"""
if old not in text:
    raise SystemExit("HudService camera language anchor not found")
text = text.replace(old, new, 1)

old = """    private void drawLeadCard(Canvas c,Paint p,JSONObject lead){RectF box=new RectF(8,376,156,454);drawCard(c,p,box);double d=lead==null?0:lead.optDouble(\"d\",0),v=lead==null?0:lead.optDouble(\"v\",0);text(c,p,\"앞차\",18,400,13,Color.rgb(103,111,116),Paint.Align.LEFT);text(c,p,d>0?String.format(Locale.US,\"%.0f m\",d):\"--\",145,400,19,Color.rgb(18,18,18),Paint.Align.RIGHT);p.setColor(Color.rgb(195,201,204));p.setStrokeWidth(1);c.drawLine(16,414,148,414,p);text(c,p,\"상대\",18,440,13,Color.rgb(103,111,116),Paint.Align.LEFT);text(c,p,d>0?String.format(Locale.US,\"%+.0f km/h\",v):\"--\",145,440,17,Color.rgb(18,18,18),Paint.Align.RIGHT);}\n"""
new = """    private void drawLeadCard(Canvas c,Paint p,JSONObject s,JSONObject lead){if(configuredRadarInfo==0)return;RectF box=new RectF(8,376,156,454);drawCard(c,p,box);double d=lead==null?0:lead.optDouble(\"d\",0),v=lead==null?0:lead.optDouble(\"v\",0);boolean showDistance=configuredRadarInfo==2||configuredRadarInfo==4;text(c,p,lang(\"앞차\",\"LEAD\"),18,400,13,Color.rgb(103,111,116),Paint.Align.LEFT);text(c,p,showDistance&&d>0?String.format(Locale.US,\"%.0f m\",d):\"--\",145,400,19,Color.rgb(18,18,18),Paint.Align.RIGHT);p.setColor(Color.rgb(195,201,204));p.setStrokeWidth(1);c.drawLine(16,414,148,414,p);text(c,p,lang(\"상대\",\"REL\"),18,440,13,Color.rgb(103,111,116),Paint.Align.LEFT);text(c,p,d>0?String.format(Locale.US,\"%+.0f km/h\",v):\"--\",145,440,17,Color.rgb(18,18,18),Paint.Align.RIGHT);}\n"""
if old not in text:
    raise SystemExit("HudService radar anchor not found")
text = text.replace(old, new, 1)

# Local S9 helpers for language, auto/dark/light theme and old screen modes.
anchor = """    private void drawMap(Canvas c,Paint p,JSONObject s,Bitmap map,Bitmap tbtCurrent,Bitmap tbtNext,Bitmap lane){"""
insert = """    private String lang(String ko,String en){return configuredLanguage==1?en:ko;}\n    private boolean darkTheme(){if(configuredTheme==1)return true;if(configuredTheme==2)return false;int h=java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);return h<7||h>=19;}\n    private void applyThemeOverlay(Canvas c,Paint p){if(!darkTheme())return;p.setStyle(Paint.Style.FILL);p.setColor(Color.argb(45,0,0,0));c.drawRect(0,0,WIDTH,HEIGHT,p);}\n    private void updateTrip(JSONObject s,long now){if(tripStartElapsed==0L){tripStartElapsed=now;tripLastElapsed=now;return;}long dt=Math.max(0L,Math.min(2000L,now-tripLastElapsed));tripLastElapsed=now;double speed=Math.max(0.0,s.optDouble(\"speed\",0));tripDistanceKm+=speed*dt/3600000.0;}\n    private void drawRightBase(Canvas c,Paint p,String title){p.setStyle(Paint.Style.FILL);p.setColor(darkTheme()?Color.rgb(8,13,19):Color.rgb(232,235,237));c.drawRect(MAP_LEFT,0,WIDTH,HEIGHT,p);text(c,p,title,(MAP_LEFT+WIDTH)/2.0f,42,27,darkTheme()?Color.WHITE:Color.rgb(25,30,34),Paint.Align.CENTER);}\n    private void drawDebugRight(Canvas c,Paint p,JSONObject s){drawRightBase(c,p,lang(\"실시간 디버그\",\"LIVE DEBUG\"));JSONObject sys=s.optJSONObject(\"system\");if(sys==null)sys=s;int fg=darkTheme()?Color.rgb(235,240,245):Color.rgb(25,30,34);int sub=darkTheme()?Color.rgb(160,172,182):Color.rgb(90,100,108);float x=MAP_LEFT+45;float y=95;text(c,p,String.format(Locale.US,\"CPU %.0f%%   TEMP %.0f°C\",sys.optDouble(\"cpu\",0),sys.optDouble(\"temp\",0)),x,y,27,fg,Paint.Align.LEFT);y+=52;text(c,p,String.format(Locale.US,\"SPEED %d   SET %d   GAP %d\",s.optInt(\"speed\",0),s.optInt(\"set\",0),s.optInt(\"gap\",0)),x,y,25,fg,Paint.Align.LEFT);y+=52;JSONObject lead=s.optJSONObject(\"lead\");if(lead!=null)text(c,p,String.format(Locale.US,\"LEAD %.0fm  %+.0fkm/h\",lead.optDouble(\"d\",0),lead.optDouble(\"v\",0)),x,y,25,fg,Paint.Align.LEFT);else text(c,p,\"LEAD --\",x,y,25,sub,Paint.Align.LEFT);y+=52;text(c,p,String.format(Locale.US,\"FPS %d   MAP %dfps   JPEG %d\",configuredFps,Math.max(2,Math.min(5,s.optInt(\"hudMapFps\",5))),jpegQuality),x,y,24,fg,Paint.Align.LEFT);y+=52;text(c,p,lang(\"S9 렌더링 / USB 출력\",\"S9 RENDER / USB OUTPUT\"),x,y,22,sub,Paint.Align.LEFT);}\n    private void drawTripRight(Canvas c,Paint p,JSONObject s){drawRightBase(c,p,lang(\"주행 리포트\",\"TRIP REPORT\"));long elapsed=tripStartElapsed==0L?0L:SystemClock.elapsedRealtime()-tripStartElapsed;double hours=elapsed/3600000.0;double avg=hours>0.0001?tripDistanceKm/hours:0.0;int fg=darkTheme()?Color.rgb(235,240,245):Color.rgb(25,30,34);int sub=darkTheme()?Color.rgb(160,172,182):Color.rgb(90,100,108);float cx=(MAP_LEFT+WIDTH)/2.0f;text(c,p,String.format(Locale.US,\"%.1f km\",tripDistanceKm),cx,145,54,fg,Paint.Align.CENTER);text(c,p,lang(\"주행거리\",\"DISTANCE\"),cx,178,18,sub,Paint.Align.CENTER);text(c,p,String.format(Locale.US,\"%02d:%02d\",elapsed/3600000L,(elapsed/60000L)%60L),cx,255,48,fg,Paint.Align.CENTER);text(c,p,lang(\"주행시간\",\"DRIVE TIME\"),cx,286,18,sub,Paint.Align.CENTER);text(c,p,String.format(Locale.US,\"AVG %.0f km/h\",avg),cx,363,35,fg,Paint.Align.CENTER);}\n\n    private void drawMap(Canvas c,Paint p,JSONObject s,Bitmap map,Bitmap tbtCurrent,Bitmap tbtNext,Bitmap lane){"""
if anchor not in text:
    raise SystemExit("HudService panel helper anchor not found")
text = text.replace(anchor, insert, 1)

# Remaining Korean labels that are part of the S9 renderer.
text = text.replace('text(c,p,"TMAP 화면 대기",1536,240,34,Color.GRAY,Paint.Align.CENTER);',
                    'text(c,p,lang("TMAP 화면 대기","WAITING FOR TMAP"),1536,240,34,Color.GRAY,Paint.Align.CENTER);', 1)
text = text.replace('String title=navi.optString("title","경로 안내");',
                    'String title=navi.optString("title",lang("경로 안내","ROUTE GUIDE"));', 1)
text = text.replace('text(c,p,"남은 "+distanceText(remain),678,363,11,Color.rgb(180,188,194),Paint.Align.CENTER);',
                    'text(c,p,lang("남은 ","LEFT ")+distanceText(remain),678,363,11,Color.rgb(180,188,194),Paint.Align.CENTER);', 1)

hud.write_text(text, encoding="utf-8")

# S9 controls the physical panel brightness over its existing USB connection.
text = display.read_text(encoding="utf-8")
anchor = """    public synchronized void sendJpeg(byte[] bArr) throws Exception {\n"""
insert = """    public synchronized void setBrightness(int value) throws Exception {\n        if (!isOpen()) {\n            throw new Exception(\"display closed\");\n        }\n        value = Math.max(1, Math.min(100, value));\n        exchange(command(14, 8, value), false);\n    }\n\n    public synchronized void sendJpeg(byte[] bArr) throws Exception {\n"""
if anchor not in text:
    raise SystemExit("TurzxDisplay brightness anchor not found")
text = text.replace(anchor, insert, 1)
display.write_text(text, encoding="utf-8")
