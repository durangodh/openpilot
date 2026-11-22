"""Execute the production capture-session update against small lifecycle fakes."""
import argparse
from pathlib import Path
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--java-home', type=Path, required=True)
p.add_argument('--work', type=Path, required=True)
a = p.parse_args()
source = (Path(__file__).resolve().parents[1] / 'naver_xposed/app/src/main/java/ai/comma/naverhud/NaverLaneCapture.java').read_text()
method = source[source.index('    void update(Activity'):source.index('    private void tick()')]
harness = '''
public class CaptureSessionCheck {
  static class Activity {}
  static class Image { int clears; void clearImage(){clears++;} }
  static class Listener {int lanes,signals; void onLost(){lanes++;} void onSignalLost(){signals++;} }
  static class Main {int posts,removes; void post(Object r){posts++;} void removeCallbacks(Object r){removes++;} }
  Activity activity; int lanes; boolean signal,running; long generation,signalGeneration;
  final Image laneView=new Image(),signalView=new Image();
  final Listener listener=new Listener(); final Main main=new Main(); final Object tickTask=new Object();
  /* METHOD */
  static void check(boolean ok,String why){if(!ok)throw new AssertionError(why);}
  public static void main(String[] args){
    CaptureSessionCheck c=new CaptureSessionCheck(); Activity phone=new Activity();
    c.update(phone,0,false,true);
    check(c.running && c.main.posts==1,"visible guidance with missing lane metadata must keep capturing");
    long laneToken=c.generation,signalToken=c.signalGeneration;
    int clears=c.laneView.clears;
    c.update(phone,2,false,true);c.update(phone,0,false,true);c.update(phone,3,false,true);
    check(c.running && c.generation==laneToken && c.laneView.clears==clears,
          "lane-count changes must not discard an in-flight original-window capture");
    c.update(phone,3,true,true);
    check(c.generation==laneToken && c.signalGeneration!=signalToken,
          "signal transition must not cancel lane pixels, but must invalidate older signal pixels");
    c.update(phone,0,false,false);
    check(!c.running && c.generation!=laneToken && c.main.removes==1,
          "ending navigation must clear overlays and invalidate previous-session pixels");
    c.update(phone,0,false,true);laneToken=c.generation;
    c.update(new Activity(),2,false,true);
    check(c.generation!=laneToken,"activity replacement must reject the previous window's result");
    c.update(null,2,true,true);
    check(!c.running,"missing activity cannot capture");
    System.out.println("Capture session lifecycle checks passed: 6");
  }
}
'''.replace('/* METHOD */', method)
a.work.mkdir(parents=True, exist_ok=True)
f=a.work/'CaptureSessionCheck.java'; f.write_text(harness)
subprocess.run([str(a.java_home/'bin/javac'),'-encoding','UTF-8','-d',str(a.work),str(f)],check=True)
subprocess.run([str(a.java_home/'bin/java'),'-cp',str(a.work),'CaptureSessionCheck'],check=True)
