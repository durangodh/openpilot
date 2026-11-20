"""Run the actual HUD receiver against small Android transport stubs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


STUBS = {
    "android/content/BroadcastReceiver.java": """package android.content;
public abstract class BroadcastReceiver {
 public abstract void onReceive(Context context, Intent intent);
}
""",
    "android/content/Context.java": """package android.content;
public class Context {
 public static final int RECEIVER_EXPORTED = 2;
 public BroadcastReceiver receiver;
 public Intent sent;
 public int flags;
 public Intent registerReceiver(BroadcastReceiver r, IntentFilter f, int flags) {
  receiver = r; this.flags = flags; return null;
 }
 public Intent registerReceiver(BroadcastReceiver r, IntentFilter f) {
  receiver = r; return null;
 }
 public void unregisterReceiver(BroadcastReceiver r) { receiver = null; }
 public void sendBroadcast(Intent i) { sent = i; }
}
""",
    "android/content/IntentFilter.java": """package android.content;
public class IntentFilter { public IntentFilter(String action) {} }
""",
    "android/content/Intent.java": """package android.content;
import java.util.HashMap;
public class Intent {
 private final String action;
 private String pkg;
 private final HashMap<String, Object> extras = new HashMap<>();
 public Intent(String action) { this.action = action; }
 public String getAction() { return action; }
 public Intent setPackage(String p) { pkg = p; return this; }
 public String getPackage() { return pkg; }
 public Intent putExtra(String k, String v) { extras.put(k, v); return this; }
 public Intent putExtra(String k, int v) { extras.put(k, v); return this; }
 public Intent putExtra(String k, long v) { extras.put(k, v); return this; }
 public String getStringExtra(String k) { return (String) extras.get(k); }
 public long getLongExtra(String k, long d) { return extras.containsKey(k) ? (Long) extras.get(k) : d; }
 public int getIntExtra(String k, int d) { return extras.containsKey(k) ? (Integer) extras.get(k) : d; }
}
""",
    "android/os/Build.java": """package android.os;
public class Build { public static class VERSION { public static int SDK_INT = 35; } }
""",
    "android/os/SystemClock.java": """package android.os;
public class SystemClock {
 public static long now = 100000;
 public static long elapsedRealtime() { return now; }
}
""",
    "ai/comma/remotehud/HudMusicService.java": """package ai.comma.remotehud;
class HudMusicService {
 static volatile String nowPlaying = "";
 static volatile boolean sessionPlaying;
 static volatile String sessionPackage = "";
}
""",
    "ai/comma/remotehud/HudNaverMusicCheck.java": """package ai.comma.remotehud;
import android.content.*;
import android.os.*;
public final class HudNaverMusicCheck {
 static void check(boolean ok, String what) { if (!ok) throw new AssertionError(what); }
 static Intent update(String title, int state, long sentAt) {
  return new Intent("ai.comma.remotehud.NAVER_MUSIC")
    .putExtra("title", title).putExtra("artist", "가수")
    .putExtra("state", state).putExtra("sentAt", sentAt);
 }
 public static void main(String[] args) {
  Context context = new Context();
  HudNaverMusic bridge = new HudNaverMusic(context);
  bridge.start();
  check(context.flags == Context.RECEIVER_EXPORTED, "Cross-package receiver on API 35");
  check(context.sent.getAction().endsWith("NAVER_MUSIC_REQUEST"), "Request on HUD restart");
  check(context.sent.getPackage().equals("com.nhn.android.nmap"), "Request only Naver");
  context.receiver.onReceive(context, update("내부 벅스", -1, SystemClock.now));
  check(HudNaverMusic.nowPlaying().equals("내부 벅스 · 가수"), "Unpublished session is visible");
  check(HudNaverMusic.status().contains("수신 중"), "Show delivery status");
  HudMusicService.nowPlaying = "스포티파이";
  HudMusicService.sessionPackage = "com.spotify.music";
  HudMusicService.sessionPlaying = true;
  check(HudNaverMusic.nowPlaying().equals("스포티파이"), "Active standalone takes precedence");
  HudMusicService.sessionPlaying = false;
  check(HudNaverMusic.nowPlaying().equals("내부 벅스 · 가수"), "Paused standalone doesn't hide embedded music");
  context.receiver.onReceive(context, update("오래된 곡", 3, SystemClock.now - 40000));
  check(HudNaverMusic.nowPlaying().equals("내부 벅스 · 가수"), "Ignore queued stale broadcast");
  context.receiver.onReceive(context, update("뒤늦게 온 이전 곡", 3, SystemClock.now - 1000));
  check(HudNaverMusic.nowPlaying().equals("내부 벅스 · 가수"), "Ignore out-of-order updates");
  SystemClock.now += 36000;
  check(HudNaverMusic.nowPlaying().equals("스포티파이"), "Expired module falls back to public source");
  check(HudNaverMusic.status().contains("연결 대기"), "Expired module is diagnosed");
  HudMusicService.nowPlaying = "";
  context.receiver.onReceive(context, update("새 곡", 3, SystemClock.now));
  check(HudNaverMusic.nowPlaying().equals("새 곡 · 가수"), "Heartbeat restores fresh data");
  context.receiver.onReceive(context, update("새 곡", 1, SystemClock.now));
  check(HudNaverMusic.nowPlaying().isEmpty(), "Stop clears title");
  check(HudNaverMusic.status().contains("곡 정보 대기"), "Connected without metadata is diagnosed");
  context.receiver.onReceive(context, update("새 곡", 3, SystemClock.now));
  bridge.stop();
  check(context.receiver == null && HudNaverMusic.nowPlaying().isEmpty(), "Unregister and clear on stop");
  Build.VERSION.SDK_INT = 26;
  bridge.start();
  check(context.receiver != null, "Old Android registration");
  bridge.stop();
  System.out.println("HudNaverMusicCheck passed");
 }
}
""",
}


def main():
    with tempfile.TemporaryDirectory(prefix="hud-music-check-") as directory:
        root = Path(directory)
        sources = []
        for path, source in STUBS.items():
            output = root / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(source, encoding="utf-8")
            sources.append(str(output))
        production = Path(__file__).resolve().parents[1] / "app/src/main/java/ai/comma/remotehud/HudNaverMusic.java"
        java = str(Path(os.environ["JAVA_HOME"]) / "bin/java") if os.environ.get("JAVA_HOME") else "java"
        javac = shutil.which("javac")
        compiler = [javac] if javac else [java, "com.sun.tools.javac.Main"]
        subprocess.run(compiler + ["-source", "11", "-target", "11", "-d", str(root)]
                       + sources + [str(production)], check=True)
        subprocess.run([java, "-cp", str(root), "ai.comma.remotehud.HudNaverMusicCheck"], check=True)


if __name__ == "__main__":
    main()
