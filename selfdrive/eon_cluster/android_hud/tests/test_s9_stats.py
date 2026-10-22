"""Compile the production sampling methods and exercise failure/freshness paths."""
import argparse
import pathlib
import re
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--java-home', type=pathlib.Path, required=True)
p.add_argument('--work', type=pathlib.Path, required=True)
a = p.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
src = (root / 'app/src/main/java/ai/comma/remotehud/HudService.java').read_text(encoding='utf-8')
methods = []
for name in ('freshStat', 'applyThermal', 'applyCpu'):
    start = re.search(r'    private (?:static )?\w+ ' + name + r'\(', src).start()
    opening = src.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (src[end] == '{') - (src[end] == '}')
        end += 1
    methods.append(src[start:end])
code = r'''
package ai.comma.remotehud;
import java.util.Locale;
public class S9StatsCheck {
  static class SystemClock { static long now = 1000; static long elapsedRealtime() { return now; } }
  long cpuLastTotal, cpuLastIdle, cpuLastSampleElapsed, s9CpuSampleElapsed, s9TempSampleElapsed;
  float s9TempC = -1, s9CpuPercent = -1;
  static int checks;
  static void check(boolean ok) { checks++; if (!ok) throw new AssertionError("check " + checks); }
  METHODS
  public static void main(String[] args) throws Exception {
    if (args.length > 0) {
      if (args[0].equals("hang")) { Thread.sleep(10000); return; }
      if (args[0].equals("overflow")) { System.out.print("x".repeat(40000)); return; }
      if (args[0].equals("fail")) { System.out.print("partial"); System.exit(1); }
      System.out.print("ok"); return;
    }
    S9StatsCheck s = new S9StatsCheck();
    s.applyThermal("BIG:32000\nLITTLE:30000\nbattery:-20000");
    check(s.s9TempC == 32 && s.s9TempSampleElapsed == 1000);
    s.applyThermal(null); check(s.s9TempC == -1 && s.s9TempSampleElapsed == 0);
    s.applyThermal("BIG:NaN\nCPU:Infinity\nbattery:-20000"); check(s.s9TempC == -1);
    s.applyThermal("BIG:33000"); check(s.s9TempC == 33);
    s.applyThermal("broken"); check(s.s9TempC == -1);
    check(freshStat(32, 1000, 9999) == 32);
    check(freshStat(32, 1000, 10000) == -1);
    check(freshStat(32, 1000, 999) == -1);
    check(freshStat(32, 0, 1000) == -1);
    s.applyCpu("cpu 100 0 0 100 0 0 0 0"); check(s.s9CpuPercent == -1);
    s.applyCpu("cpu 150 0 0 150 0 0 0 0"); check(s.s9CpuPercent == 50);
    s.applyCpu(null); check(s.s9CpuPercent == -1 && s.cpuLastTotal == 0);
    s.applyCpu("cpu 200 0 0 200 0 0 0 0"); check(s.s9CpuPercent == -1);
    s.applyCpu("cpu 250 0 0 250 0 0 0 0"); check(s.s9CpuPercent == 50);
    SystemClock.now += 10000;
    s.applyCpu("cpu 300 0 0 300 0 0 0 0"); check(s.s9CpuPercent == -1);
    s.applyCpu("cpu 350 0 0 350 0 0 0 0"); check(s.s9CpuPercent == 50);
    s.applyCpu("cpu 260"); check(s.s9CpuPercent == -1 && s.cpuLastTotal == 0);
    s.applyCpu("cpu 100 0 0 100 0 0 0 0");
    s.applyCpu("cpu 200 0 0 90 0 0 0 0"); check(s.s9CpuPercent == -1);
    s.applyCpu("cpu 200 0 0 90 0 0 0 0"); check(s.s9CpuPercent == -1);
    s.applyCpu("cpu -1 0 0 0 0 0 0 0"); check(s.cpuLastTotal == 0);
    s.applyCpu("cpu 9223372036854775807 1 0 0 0 0 0 0"); check(s.cpuLastTotal == 0);
    String javaBin = System.getProperty("java.home") + "/bin/java";
    for (String mode : new String[]{"ok", "fail", "overflow", "hang"}) {
      Process child = new ProcessBuilder(javaBin, "-cp", System.getProperty("java.class.path"),
          S9StatsCheck.class.getName(), mode).redirectErrorStream(true).start();
      long start = System.nanoTime();
      String result = BoundedProcessRead.read(child, 1000);
      check(mode.equals("ok") ? "ok".equals(result) : result == null);
      check((System.nanoTime() - start) / 1000000 < 2500);
      child.waitFor(2, java.util.concurrent.TimeUnit.SECONDS);
      check(!child.isAlive());
    }
    System.out.println(checks + " S9 stats checks passed");
  }
}
'''.replace('METHODS', '\n'.join(methods))
a.work.mkdir(parents=True, exist_ok=True)
java_file = a.work / 'S9StatsCheck.java'
java_file.write_text(code, encoding='utf-8')
suffix = '.exe' if (a.java_home / 'bin/javac.exe').exists() else ''
subprocess.run([str(a.java_home / ('bin/javac' + suffix)), '-encoding', 'UTF-8', '-d', str(a.work), str(java_file),
                str(root / 'app/src/main/java/ai/comma/remotehud/BoundedProcessRead.java')], check=True)
subprocess.run([str(a.java_home / ('bin/java' + suffix)), '-cp', str(a.work), 'ai.comma.remotehud.S9StatsCheck'], check=True)
