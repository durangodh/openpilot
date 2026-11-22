package ai.comma.signallab;
import java.util.*;
public final class DecoderCheck {
 static int n;static void check(boolean b,String s){n++;if(!b)throw new AssertionError(s);}
 public static void main(String[]args){
  float[] out=new float[9];out[0]=320;out[1]=320;out[2]=100;out[3]=40;out[4+2]=.9f;out[4+1]=.7f;out[4+3]=.99f;
  List<Decoder.Detection> d=Decoder.decode(out,1,1,0,0,0,0,640,640,640,640);
  check(d.size()==2,"red and left preserved; speed sign excluded");
  check(d.stream().anyMatch(x->x.cls==2),"red class order");check(d.stream().anyMatch(x->x.cls==1),"left class order");
  check(Math.abs(d.get(0).x0-270/640f)<1e-5,"pixel coordinates mapped");
  List<Decoder.Detection> same=new ArrayList<>(d);same.add(new Decoder.Detection(2,.8f,270/640f,300/640f,370/640f,340/640f));
  check(Decoder.suppress(same).size()==2,"NMS per class preserves simultaneous red and left");
  float[] nan=out.clone();nan[0]=Float.NaN;
  check(Decoder.decode(nan,1,1,0,0,0,0,640,640,640,640).isEmpty(),"NaN coordinates rejected");
  float[] conf=out.clone();conf[6]=Float.NaN;conf[5]=0;
  check(Decoder.decode(conf,1,1,0,0,0,0,640,640,640,640).isEmpty(),"NaN scores rejected");
  float[] pad=out.clone();pad[1]=20;
  check(Decoder.decode(pad,1,1,0,100,0,0,640,400,640,400).isEmpty(),"padding detections rejected");
  List<Decoder.Detection> tile=Decoder.decode(out,1,1,0,0,400,0,640,640,1040,640);
  check(Math.abs(tile.get(0).x0-670/1040f)<1e-5,"tile offset mapped");
  try{Decoder.decode(new float[8],1,1,0,0,0,0,640,640,640,640);throw new AssertionError("wrong contract accepted");}catch(IllegalArgumentException ok){n++;}
  System.out.println("Decoder checks passed: "+n);
 }
}
