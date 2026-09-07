package ai.comma.remotehud;

import java.util.ArrayList;
import java.util.List;

public class CameraVehicleTrackerCheck {
    static void check(boolean ok,String msg) { if(!ok) throw new AssertionError(msg); }
    static CameraVehicleTracker.Box box(double d,double y,float x,float score) {
        return new CameraVehicleTracker.Box(d,y,1.9,1.5,x,.4f,x+.12f,.65f,score,"car");
    }
    static CameraVehicleTracker.Box person(double d,double y,float x,float score) {
        return new CameraVehicleTracker.Box(d,y,.7,1.7,x,.3f,x+.05f,.7f,score,"person");
    }
    static List<CameraVehicleTracker.Box> input(CameraVehicleTracker.Box... boxes) {
        ArrayList<CameraVehicleTracker.Box> out=new ArrayList<>();
        for(CameraVehicleTracker.Box b:boxes) out.add(b);
        return out;
    }
    public static void main(String[] args) {
        CameraVehicleTracker t=new CameraVehicleTracker();
        List<CameraVehicleTracker.Track> a=t.update(input(box(30,0,.4f,.9f)),1000);
        check(a.size()==1,"Clear vehicle should appear on first frame");
        int id=a.get(0).id;
        a=t.update(input(box(26,0,.41f,.9f)),1500);
        check(a.size()==1 && a.get(0).id==id,"Approaching vehicle keeps identity");
        check(a.get(0).vd<0,"Approach velocity sign");
        check(CameraVehicleTracker.predicted(a.get(0).box.d,a.get(0).vd,100)<a.get(0).box.d,"Moves between observations");
        check(CameraVehicleTracker.predicted(30,-10,1000)==27.5,"Prediction limited to 250ms");
        check(CameraVehicleTracker.predicted(30,-10,-100)==30,"Negative age does not reverse motion");
        check(t.update(input(),2000).isEmpty(),"No ghost after a missed frame");
        t.clear();
        check(t.update(input(box(30,0,.4f,.4f)),3000).isEmpty(),"Weak one-frame detection suppressed");
        check(t.update(input(box(30,0,.4f,.4f)),3500).size()==1,"Weak repeated detection confirmed");
        t.clear();
        check(t.update(input(person(20,0,.4f,.95f)),3600).isEmpty(),"One-frame person suppressed");
        check(t.update(input(person(20,0,.4f,.95f)),3900).isEmpty(),"Two-frame person suppressed");
        check(t.update(input(person(20,0,.4f,.95f)),4200).size()==1,"Persistent person confirmed");
        check(CameraVehicleTracker.detectionThreshold("person",.40f)>=.58f,"Person threshold raised");
        check(CameraVehicleTracker.detectionThreshold("car",.40f)==.40f,"Vehicle threshold preserved");
        check(CameraVehicleTracker.plausiblePersonBox(.03,.16),"Tall person box accepted");
        check(!CameraVehicleTracker.plausiblePersonBox(.03,.05),"Short sign-shaped person rejected");
        check(!CameraVehicleTracker.plausiblePersonBox(.01,.16),"Tiny person box rejected");
        t.clear();
        t.update(input(box(20,0,.4f,.9f)),4300);
        t.update(input(box(20,0,.4f,.9f)),4600);
        a=t.update(input(person(20,0,.4f,.95f)),4900);
        check(a.size()==1 && "car".equals(a.get(0).box.type),"Car history cannot confirm false person");
        t.clear();
        a=t.update(input(box(30,0,.2f,.9f),box(30,1,.55f,.8f)),4000);
        check(a.size()==2,"Nearby vehicles with separate image boxes stay separate");
        t.clear();
        a=t.update(input(box(30,0,.4f,.9f),box(30.2,.1,.401f,.8f)),4500);
        check(a.size()==1,"Duplicate image boxes merge");
        t.clear();
        a=t.update(input(box(20,2,.65f,.9f)),5000);
        id=a.get(0).id;
        a=t.update(input(box(24,2.5,.68f,.9f)),5500);
        check(a.get(0).id==id && a.get(0).vd>0 && a.get(0).vy>0,"Passing vehicle motion follows observations");
        t.update(input(),7000);
        a=t.update(input(box(24,2.5,.68f,.9f)),7500);
        check(a.get(0).id!=id,"Old identity expires");
        System.out.println("PASS: appearance, approach/pass movement, identity, duplicates, adjacent cars, disappearance, bounded prediction and expiry");
    }
}
