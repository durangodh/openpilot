package ai.comma.remotehud;

import java.util.ArrayList;
import java.util.List;

/** Camera-only display tracking. No control or radar state is written here. */
final class CameraVehicleTracker {
    static final class Box {
        double d, y, width, height;
        float left, top, right, bottom, score;
        String type;
        Box(double d, double y, double width, double height, float left, float top,
            float right, float bottom, float score, String type) {
            this.d=d; this.y=y; this.width=width; this.height=height;
            this.left=left; this.top=top; this.right=right; this.bottom=bottom;
            this.score=score; this.type=type;
        }
    }
    static final class Track {
        final int id;
        Box box;
        double vd, vy;
        int hits=1;
        long time;
        boolean matched;
        Track(int id, Box box, long time) { this.id=id; this.box=box; this.time=time; }
    }
    private final List<Track> tracks = new ArrayList<>();
    private int nextId;

    static double clamp(double v, double lo, double hi) { return Math.max(lo,Math.min(hi,v)); }
    static double iou(Box a, Box b) {
        double intersection=Math.max(0,Math.min(a.right,b.right)-Math.max(a.left,b.left))
                *Math.max(0,Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top));
        double union=(a.right-a.left)*(a.bottom-a.top)+(b.right-b.left)*(b.bottom-b.top)-intersection;
        return union>0 ? intersection/union : 0;
    }
    static double predicted(double value, double speed, long ageMs) {
        return value+speed*clamp(ageMs/1000.0,0,0.25);
    }

    List<Track> update(List<Box> input, long now) {
        ArrayList<Box> observations=new ArrayList<>();
        // Merge overlapping image boxes, not nearby vehicles in different lanes.
        input.sort((a,b)->Float.compare(b.score,a.score));
        for (Box b:input) {
            boolean duplicate=false;
            for (Box other:observations) if (iou(b,other)>=0.55) { duplicate=true; break; }
            if (!duplicate) observations.add(b);
        }
        for (Track t:tracks) t.matched=false;
        ArrayList<Track> visible=new ArrayList<>();
        for (Box b:observations) {
            Track best=null;
            double bestCost=Double.POSITIVE_INFINITY;
            for (Track t:tracks) {
                long age=now-t.time;
                if (t.matched || age<=0 || age>1500) continue;
                double dd=Math.abs(predicted(t.box.d,t.vd,age)-b.d);
                double dy=Math.abs(predicted(t.box.y,t.vy,age)-b.y);
                double gate=Math.max(4,Math.min(15,b.d*0.2));
                double imageDx=Math.abs((t.box.left+t.box.right-b.left-b.right)*0.5);
                double imageGate=Math.max(0.06,Math.max(t.box.right-t.box.left,b.right-b.left));
                if (dd>gate || dy>2.2 || (iou(t.box,b)<0.05 && imageDx>imageGate)) continue;
                double cost=dd/gate+dy/2.2+1-iou(t.box,b);
                if (cost<bestCost) { best=t; bestCost=cost; }
            }
            if (best==null) { best=new Track(nextId++,b,now); tracks.add(best); }
            else {
                double dt=(now-best.time)/1000.0;
                best.vd=clamp(best.vd*0.4+(b.d-best.box.d)/dt*0.6,-40,40);
                best.vy=clamp(best.vy*0.4+(b.y-best.box.y)/dt*0.6,-8,8);
                // Favour the current observation instead of dragging the old box.
                b.d=best.box.d*0.15+b.d*0.85;
                b.y=best.box.y*0.15+b.y*0.85;
                best.box=b; best.time=now; best.hits++;
            }
            best.matched=true;
            if (best.hits>=2 || b.score>=0.65f) visible.add(best);
        }
        // Unseen vehicles disappear from output immediately; identity survives
        // only briefly for a reacquisition. Never extrapolate missed detections.
        tracks.removeIf(t->now-t.time>1000);
        return visible;
    }
    void clear() { tracks.clear(); }
}
