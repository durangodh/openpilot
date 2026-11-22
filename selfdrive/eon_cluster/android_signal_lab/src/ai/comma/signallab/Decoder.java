// SPDX-License-Identifier: AGPL-3.0-or-later
package ai.comma.signallab;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** LightSignal v20260727 contract: [1,9,8400], pixel xywh, five ordered classes. */
public final class Decoder {
    public static final String[] LABELS = {"green", "left", "red", "speed_sign", "yellow"};
    public static final String[] KOREAN = {"초록", "좌회전", "빨강", "속도표지", "노랑"};
    public static final class Detection {
        public final int cls;
        public final float score, x0, y0, x1, y1;
        public Detection(int cls, float score, float x0, float y0, float x1, float y1) {
            this.cls=cls; this.score=score; this.x0=x0; this.y0=y0; this.x1=x1; this.y1=y1;
        }
    }
    public static List<Detection> decode(float[] out, int rows, float ratio, int padX, int padY,
                                         int cropX, int cropY, int cropW, int cropH, int fullW, int fullH) {
        if (out.length != rows * 9 || ratio <= 0 || !Float.isFinite(ratio)) throw new IllegalArgumentException("model output contract mismatch");
        List<Detection> found = new ArrayList<>();
        for(int i=0; i<rows; i++) {
            float cx=out[i], cy=out[rows+i], w=out[2*rows+i], h=out[3*rows+i];
            if(!Float.isFinite(cx)||!Float.isFinite(cy)||!Float.isFinite(w)||!Float.isFinite(h)||w<=0||h<=0) continue;
            float left=Math.max(0,(cx-w/2-padX)/ratio), top=Math.max(0,(cy-h/2-padY)/ratio);
            float right=Math.min(cropW,(cx+w/2-padX)/ratio), bottom=Math.min(cropH,(cy+h/2-padY)/ratio);
            if(right<=left||bottom<=top) continue;
            // Preserve multiple classes on the same housing: red + left can coexist.
            for(int cls=0; cls<5; cls++) {
                if(cls==3) continue;
                float confidence=out[(4+cls)*rows+i];
                if(!Float.isFinite(confidence)||confidence<.35f||confidence>1f) continue;
                found.add(new Detection(cls,confidence,(cropX+left)/fullW,(cropY+top)/fullH,
                        (cropX+right)/fullW,(cropY+bottom)/fullH));
            }
        }
        return found;
    }
    static float overlap(Detection a, Detection b, boolean smaller) {
        float inter=Math.max(0,Math.min(a.x1,b.x1)-Math.max(a.x0,b.x0))*Math.max(0,Math.min(a.y1,b.y1)-Math.max(a.y0,b.y0));
        float aa=(a.x1-a.x0)*(a.y1-a.y0), bb=(b.x1-b.x0)*(b.y1-b.y0);
        return inter/Math.max(1e-9f,smaller?Math.min(aa,bb):aa+bb-inter);
    }
    public static List<Detection> suppress(List<Detection> raw) {
        raw.sort(Comparator.comparingDouble((Detection d)->-d.score));
        List<Detection> kept=new ArrayList<>();
        for(Detection candidate:raw) {
            boolean duplicate=false;
            for(Detection prior:kept) {
                if(prior.cls==candidate.cls&&(overlap(prior,candidate,false)>.45f||overlap(prior,candidate,true)>.60f)) { duplicate=true; break; }
            }
            if(!duplicate) kept.add(candidate);
            if(kept.size()>=32) break;
        }
        return kept;
    }
}
