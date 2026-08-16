from pathlib import Path

root = Path(__file__).resolve().parent
hud = root / "app/src/main/java/ai/comma/remotehud/HudService.java"
display = root / "app/src/main/java/ai/comma/remotehud/TurzxDisplay.java"

text = hud.read_text(encoding="utf-8")
old = """    private int usbErrorStreak;\n    private long frameIntervalMs = 125L;\n"""
new = """    private int usbErrorStreak;\n    private long frameIntervalMs = 125L;\n    private int configuredFps = 8;\n    private int jpegQuality = 55;\n    private int appliedBrightness = -1;\n"""
if old not in text:
    raise SystemExit("HudService fields anchor not found")
text = text.replace(old, new, 1)

old = """                usbStatus=\"연결됨 · USB 권한 허용\";usbConnected=true;usbError=false;usbErrorStreak=0;Bitmap frame;\n                synchronized(assetLock){frame=render(state.get(),mapFrame.get(),tbtCurrentFrame.get(),tbtNextFrame.get(),laneFrame.get());}\n"""
new = """                usbStatus=\"연결됨 · USB 권한 허용\";usbConnected=true;usbError=false;usbErrorStreak=0;Bitmap frame;\n                JSONObject currentState=state.get();\n                int requestedFps=Math.max(1,Math.min(15,currentState.optInt(\"hudFps\",8)));\n                if(requestedFps!=configuredFps){configuredFps=requestedFps;frameIntervalMs=Math.max(67L,1000L/configuredFps);}\n                jpegQuality=Math.max(20,Math.min(95,currentState.optInt(\"hudJpegQuality\",55)));\n                int requestedBrightness=Math.max(1,Math.min(100,currentState.optInt(\"hudBrightness\",65)));\n                if(requestedBrightness!=appliedBrightness){display.setBrightness(requestedBrightness);appliedBrightness=requestedBrightness;}\n                synchronized(assetLock){frame=render(currentState,mapFrame.get(),tbtCurrentFrame.get(),tbtNextFrame.get(),laneFrame.get());}\n"""
if old not in text:
    raise SystemExit("HudService render anchor not found")
text = text.replace(old, new, 1)

old = "portrait.compress(Bitmap.CompressFormat.JPEG,55,output);"
new = "portrait.compress(Bitmap.CompressFormat.JPEG,jpegQuality,output);"
if old not in text:
    raise SystemExit("HudService JPEG anchor not found")
text = text.replace(old, new, 1)

old = """                display.clearHalt();\n                display.close();\n                if(usbErrorStreak>=USB_RESET_AFTER_ERRORS){\n"""
new = """                display.clearHalt();\n                display.close();\n                appliedBrightness=-1;\n                if(usbErrorStreak>=USB_RESET_AFTER_ERRORS){\n"""
if old not in text:
    raise SystemExit("HudService close anchor not found")
text = text.replace(old, new, 1)
hud.write_text(text, encoding="utf-8")

text = display.read_text(encoding="utf-8")
anchor = """    public synchronized void sendJpeg(byte[] bArr) throws Exception {\n"""
insert = """    public synchronized void setBrightness(int value) throws Exception {\n        if (!isOpen()) {\n            throw new Exception(\"display closed\");\n        }\n        value = Math.max(1, Math.min(100, value));\n        exchange(command(14, 8, value), false);\n    }\n\n    public synchronized void sendJpeg(byte[] bArr) throws Exception {\n"""
if anchor not in text:
    raise SystemExit("TurzxDisplay brightness anchor not found")
text = text.replace(anchor, insert, 1)
display.write_text(text, encoding="utf-8")
