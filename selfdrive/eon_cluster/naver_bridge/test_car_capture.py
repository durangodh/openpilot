"""Run real phone/car capture code with controlled Surface lifecycle callbacks."""
import subprocess
import sys
from pathlib import Path
import test_capture

test_capture.SOURCES["com/naver/map/carrot/CarMapCheck.java"] = """package com.naver.map.carrot;
import android.view.*;
public class CarMapCheck {
 static void check(boolean ok){if(!ok)throw new AssertionError();}
 public static void main(String[] args){
  CarrotNaverBridge bridge=new CarrotNaverBridge();
  Object first=new Object(), second=new Object();
  Surface one=new Surface(), two=new Surface();
  check(!CarrotCarMapCapture.capture(bridge));
  CarrotCarMapCapture.available(first,one,0,576);
  check(!CarrotCarMapCapture.active());
  CarrotCarMapCapture.available(first,one,1280,720);
  check(CarrotCarMapCapture.capture(bridge));
  check(CarrotCarMapCapture.active());
  check(bridge.sent==1 && bridge.last.getWidth()==960 && bridge.last.getHeight()==576);
  check(PixelCopy.pendingBitmap.isRecycled());
  // A hidden/absent phone map must not clear the live car map.
  CarrotMapCapture.capture(new android.app.Activity(),bridge);
  check(bridge.cleared==0 && bridge.sent==1);
  PixelCopy.defer=true;
  CarrotCarMapCapture.capture(bridge);
  int calls=PixelCopy.calls;
  CarrotCarMapCapture.capture(bridge);check(PixelCopy.calls==calls);
  CarrotCarMapCapture.available(second,two,1920,1080);
  check(!CarrotCarMapCapture.active()); // replacement has no successful frame yet
  CarrotCarMapCapture.destroyed(first);check(!CarrotCarMapCapture.active());
  PixelCopy.pending.onPixelCopyFinished(0);
  check(bridge.sent==1 && PixelCopy.pendingBitmap.isRecycled());
  PixelCopy.defer=false;
  CarrotCarMapCapture.capture(bridge);check(bridge.sent==2);
  PixelCopy.result=3;
  CarrotCarMapCapture.capture(bridge);check(bridge.sent==2 && bridge.cleared==1 && !CarrotCarMapCapture.active());
  PixelCopy.result=0;PixelCopy.fail=true;
  CarrotCarMapCapture.capture(bridge);
  PixelCopy.fail=false;
  CarrotCarMapCapture.capture(bridge);check(bridge.sent==3);
  PixelCopy.defer=true;
  CarrotCarMapCapture.capture(bridge);
  CarrotCarMapCapture.destroyed(second);
  PixelCopy.pending.onPixelCopyFinished(0);
  check(bridge.sent==3 && PixelCopy.pendingBitmap.isRecycled());
  check(!CarrotCarMapCapture.capture(bridge));
  PixelCopy.defer=false;
  CarrotMapCapture.capture(new android.app.Activity(),bridge);
  check(bridge.cleared>=2); // Disconnection hands ownership back to the phone.
  two.valid=false;
  CarrotCarMapCapture.available(second,two,960,576);check(!CarrotCarMapCapture.active());
  System.out.println("PASS: car Surface without Activity, lifecycle handover, stale callbacks, error retry, allocation bound, phone isolation");
 }
}"""


if __name__ == "__main__":
  test_capture.main()
  # Reuse the classes compiled by the phone capture regression harness.
  args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
  java_home = Path(args["--java-home"])
  java = java_home / ("bin/java.exe" if (java_home / "bin/java.exe").exists() else "bin/java")
  subprocess.run([str(java), "-cp", str(Path(args["--work"]) / "classes"), "com.naver.map.carrot.CarMapCheck"], check=True)
