"""Test production accessibility-click adapter using small Android test doubles.

This does not replace the on-device nMirror accessibility test.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

STUBS = {
  "android/os/SystemClock.java": '''package android.os; public class SystemClock {
    public static long now=1000; public static long uptimeMillis(){return now;}
  }''',
  "android/util/Log.java": '''package android.util; public class Log {
    public static int i(String a,String b){return 0;}
    public static int e(String a,String b,Throwable t){return 0;}
  }''',
  "android/view/View.java": '''package android.view; public class View {
    public static final int IMPORTANT_FOR_ACCESSIBILITY_YES=1;
    public interface OnClickListener {void onClick(View view);}
    public boolean attached=true,shown=true,enabled=true,clickable;
    public Object tag; public int id=-1,important;public CharSequence description; public OnClickListener listener;
    public boolean isAttachedToWindow(){return attached;} public boolean isShown(){return shown;}
    public boolean isEnabled(){return enabled;}
    public Object getTag(int key){return tag;} public void setTag(int key,Object value){tag=value;}
    public void setId(int value){id=value;}
    public void setImportantForAccessibility(int value){important=value;}
    public void setContentDescription(CharSequence value){description=value;}
    public void setOnClickListener(OnClickListener value){listener=value;clickable=true;}
    public void performClick(){listener.onClick(this);}
  }''',
  "kotlin/jvm/functions/Function0.java": '''package kotlin.jvm.functions;
    public interface Function0<R> { R invoke(); }''',
  "VoiceButtonCheck.java": '''import android.view.View; import android.os.SystemClock;
    import com.naver.map.carrot.CarrotVoiceButton; import kotlin.jvm.functions.Function0;
    public class VoiceButtonCheck {
      static int checks,clicks;
      static void check(boolean b){checks++;if(!b)throw new AssertionError("check "+checks);}
      public static void main(String[] args){
        View v=new View(); Function0<Object> click=()->{clicks++;return null;};
        CarrotVoiceButton.bind(null,click);CarrotVoiceButton.bind(v,null);check(v.listener==null);
        CarrotVoiceButton.bind(v,click);
        check(v.id==0x7f0b0174);check(v.important==1);check(v.clickable);
        check("클로바 음성인식".contentEquals(v.description));check(clicks==0);
        v.performClick();check(clicks==1);
        v.performClick();check(clicks==1);
        SystemClock.now+=349;v.performClick();check(clicks==1);
        SystemClock.now++;v.performClick();check(clicks==2);
        SystemClock.now+=500;v.shown=false;v.performClick();check(clicks==2);v.shown=true;
        v.enabled=false;v.performClick();check(clicks==2);v.enabled=true;
        v.attached=false;v.performClick();check(clicks==2);v.attached=true;
        v.performClick();check(clicks==3);
        Function0<Object> latest=()->{clicks+=10;return null;};
        CarrotVoiceButton.bind(v,latest);v.performClick();check(clicks==3);
        SystemClock.now+=350;v.performClick();check(clicks==13);
        View another=new View();CarrotVoiceButton.bind(another,click);another.performClick();check(clicks==14);
        Function0<Object> fail=()->{throw new IllegalStateException("expected");};
        CarrotVoiceButton.bind(v,fail);SystemClock.now+=350;v.performClick();check(clicks==14);
        System.out.println("PASS: "+checks+" accessibility callback assertions");
      }
    }''',
}


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--ecj", type=Path)
  args = parser.parse_args()
  with tempfile.TemporaryDirectory(prefix="naver-voice-test-") as temp:
    root = Path(temp)
    files = []
    for name, body in STUBS.items():
      p = root / name
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_text(body, encoding="utf-8")
      files.append(p)
    classes = root / "classes"
    classes.mkdir()
    compiler = ["java", "-jar", str(args.ecj), "-8"] if args.ecj else ["javac", "--release", "8"]
    subprocess.run(compiler + ["-encoding", "UTF-8", "-d", str(classes)] + [str(p) for p in files]
                   + [str(Path(__file__).with_name("CarrotVoiceButton.java"))], check=True)
    subprocess.run(["java", "-cp", str(classes), "VoiceButtonCheck"], check=True)


if __name__ == "__main__":
  main()
