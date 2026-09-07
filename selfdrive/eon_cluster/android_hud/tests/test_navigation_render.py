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
    enum Style { FILL } enum Align { CENTER,LEFT }
    void setShader(Object x){} void setStyle(Style x){} void setColor(int x){} void setFilterBitmap(boolean x){}
  }
  static class Canvas {
    int banners, maps, markers, nextCalls, etaCalls, sourceBadges;
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
  void drawNativeOverlay(Canvas c,Paint p,Bitmap b,float x,float y,float w,float h,Paint.Align a){}
  boolean drawTurnIcon(Canvas c,Paint p,float x,float y,float size,int type,String title,int color,boolean b){return false;}
  void drawScaledArrow(Canvas c,Paint p,float x,float y,int type,float size,String title){}
  String distanceText(int n){return Integer.toString(n);}
  /* PRODUCTION_METHODS */
  static JSONObject state(boolean active,int remain,int turn){return new JSONObject().put("navi",
    new JSONObject().put("active",active).put("remainDist",remain).put("turnDist",turn));}
  void check(String name,JSONObject state,Bitmap map,int banners,int maps){
    Canvas c=new Canvas(); drawMap(c,new Paint(),state,map,null,null,null);
    if(c.banners!=banners||c.maps!=maps||c.markers!=maps||c.nextCalls!=1||c.etaCalls!=1||c.sourceBadges!=1)
      throw new AssertionError(name+": banners="+c.banners+" maps="+c.maps+" next="+c.nextCalls+" eta="+c.etaCalls+" badge="+c.sourceBadges);
  }
  public static void main(String[] args){
    NavigationRenderCheck hud=new NavigationRenderCheck(); Bitmap map=new Bitmap();
    hud.check("map available",state(true,1000,200),map,1,1);
    hud.check("map missing",state(true,1000,200),null,1,0);
    map.recycled=true; hud.check("map recycled",state(true,1000,200),map,1,0);
    hud.frameDark=true; hud.check("dark map missing",state(true,1000,200),null,1,0);
    map.recycled=false; hud.check("map recovered",state(true,1000,200),map,1,1);
    hud.check("naver guidance before route summary",state(true,0,200),null,1,0);
    hud.check("ended without map",state(false,1000,200),null,0,0);
    hud.check("arrived without map",state(true,0,-1),null,0,0);
    hud.check("no navigation",new JSONObject(),null,0,0);
    System.out.println("9 navigation rendering cases passed");
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
