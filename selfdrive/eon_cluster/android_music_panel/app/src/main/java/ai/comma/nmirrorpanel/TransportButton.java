package ai.comma.nmirrorpanel;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.view.View;

final class TransportButton extends View {
    static final int PREVIOUS=0, PLAY=1, PAUSE=2, NEXT=3;
    private int kind;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    TransportButton(Context context, int kind, String description) {
        super(context); this.kind=kind; setContentDescription(description);
        setClickable(true); setFocusable(true);
    }
    void setKind(int kind) { this.kind=kind; invalidate(); }
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float size=Math.min(getWidth(), getHeight());
        canvas.save(); canvas.translate(getWidth()/2f, getHeight()/2f); canvas.scale(size/64f,size/64f);
        paint.setColor(kind==PLAY||kind==PAUSE ? 0xff70e2c8 : 0xff26353d);
        paint.setAlpha(isEnabled()?255:80); canvas.drawCircle(0,0,29,paint);
        paint.setColor(kind==PLAY||kind==PAUSE ? 0xff10191e : 0xffeef6f7);
        paint.setAlpha(isEnabled()?255:80);
        if(kind==PAUSE) { canvas.drawRect(-9,-11,-3,11,paint); canvas.drawRect(3,-11,9,11,paint); }
        else {
            if(kind==PREVIOUS) canvas.scale(-1,1);
            Path p=new Path(); p.moveTo(-7,-12); p.lineTo(12,0); p.lineTo(-7,12); p.close(); canvas.drawPath(p,paint);
            if(kind!=PLAY) canvas.drawRect(11,-12,15,12,paint);
        }
        canvas.restore();
    }
}
