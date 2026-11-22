// SPDX-License-Identifier: AGPL-3.0-or-later
package ai.comma.signallab;

import android.app.Activity;
import android.os.Bundle;
import android.os.Build;
import android.os.Handler;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Bitmap;
import android.net.Uri;
import android.text.InputType;
import android.view.View;
import android.widget.*;
import java.util.List;

public final class MainActivity extends Activity {
    final Handler handler=new Handler();
    TextView status;
    Preview preview;
    EditText host,token;
    final Runnable refresh=new Runnable(){@Override public void run(){
        SignalService.Snapshot s=SignalService.snapshot;
        long age=android.os.SystemClock.elapsedRealtime()-s.at;
        status.setText(s.text+(s.image!=null&&age>1500?"\n영상 지연 · 후보 표시 중지":""));
        preview.current=(s.image!=null&&age<=1500)?s:null;preview.invalidate();handler.postDelayed(this,250);
    }};
    @Override public void onCreate(Bundle b){
        super.onCreate(b);
        LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);layout.setPadding(24,20,24,16);layout.setBackgroundColor(Color.rgb(17,24,39));
        TextView title=new TextView(this);title.setText("한국 신호등 · EON 시험");title.setTextColor(Color.WHITE);title.setTextSize(23);layout.addView(title);
        TextView note=new TextView(this);note.setText("EON 전방 영상을 메인폰에서 분석합니다.\n표시는 후보이며 내 진행 신호는 미확정입니다.\n차량 제어에 사용하지 않는 10분 시험입니다.");note.setTextColor(0xffffcc66);note.setTextSize(14);layout.addView(note);
        host=new EditText(this);host.setTextColor(Color.WHITE);host.setHintTextColor(0xffaab4c4);host.setSingleLine();host.setHint("이온 IP:7799");host.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);host.setText(getSharedPreferences("signal",0).getString("host",""));layout.addView(host);
        token=new EditText(this);token.setTextColor(Color.WHITE);token.setHintTextColor(0xffaab4c4);token.setSingleLine();token.setHint("이온에 표시된 연결 코드 4자리");token.setInputType(InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_VARIATION_PASSWORD);token.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(4)});String savedCode=getSharedPreferences("signal",0).getString("token","");token.setText(savedCode.matches("[0-9]{4}")?savedCode:"");layout.addView(token);
        LinearLayout buttons=new LinearLayout(this);
        Button start=new Button(this);start.setText("인식 시작");start.setOnClickListener(v->{
            if(SignalService.active){Toast.makeText(this,"이미 실행 중입니다",Toast.LENGTH_SHORT).show();return;}
            try{
                SignalService.endpoint(host.getText().toString().trim());
                getSharedPreferences("signal",0).edit().putString("host",host.getText().toString().trim()).putString("token",token.getText().toString().trim()).apply();
                if(Build.VERSION.SDK_INT>=33&&checkSelfPermission("android.permission.POST_NOTIFICATIONS")!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"},7);
                startForegroundService(new Intent(this,SignalService.class));
            }catch(Exception e){Toast.makeText(this,e.getMessage(),Toast.LENGTH_LONG).show();}
        });buttons.addView(start);
        Button stop=new Button(this);stop.setText("중지");stop.setOnClickListener(v->{stopService(new Intent(this,SignalService.class));SignalService.snapshot=new SignalService.Snapshot(null,java.util.Collections.emptyList(),"중지됨");});buttons.addView(stop);
        Button monitor=new Button(this);monitor.setText("이온 수신 보기");monitor.setOnClickListener(v->{try{
            String url=SignalService.endpoint(host.getText().toString().trim())+"/#"+token.getText().toString().trim();startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));
        }catch(Exception e){Toast.makeText(this,e.getMessage(),Toast.LENGTH_LONG).show();}});buttons.addView(monitor);
        layout.addView(buttons);
        status=new TextView(this);status.setTextColor(Color.WHITE);status.setTextSize(15);layout.addView(status);
        preview=new Preview();layout.addView(preview,new LinearLayout.LayoutParams(-1,0,1));
        TextView version=new TextView(this);version.setText("한국 데이터 학습 모델 · LightSignal v20260727\nAGPL-3.0 · 전체 소스는 함께 제공한 시험 패키지에 포함");version.setTextColor(0xff94a3b8);version.setTextSize(11);layout.addView(version);
        setContentView(layout);
    }
    @Override public void onResume(){super.onResume();handler.post(refresh);}
    @Override public void onPause(){handler.removeCallbacks(refresh);super.onPause();}
    final class Preview extends View{
        SignalService.Snapshot current;
        Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG|Paint.FILTER_BITMAP_FLAG);
        Preview(){super(MainActivity.this);}
        int color(int cls){return cls==0?0xff35e889:cls==1?0xff55ccff:cls==2?0xffff6060:0xffffd55a;}
        @Override protected void onDraw(Canvas c){
            c.drawColor(0xff101010);SignalService.Snapshot s=current;
            if(s==null){paint.setColor(0xffbbbbbb);paint.setTextSize(28);c.drawText("EON 영상 대기",20,50,paint);return;}
            Bitmap image=s.image;float scale=Math.min(getWidth()/(float)image.getWidth(),getHeight()/(float)image.getHeight());
            float w=image.getWidth()*scale,h=image.getHeight()*scale,x=(getWidth()-w)/2,y=(getHeight()-h)/2;
            paint.setStyle(Paint.Style.FILL);c.drawBitmap(image,null,new RectF(x,y,x+w,y+h),paint);
            for(Decoder.Detection d:s.detections){
                paint.setColor(color(d.cls));paint.setStrokeWidth(3);paint.setStyle(Paint.Style.STROKE);
                c.drawRect(x+d.x0*w,y+d.y0*h,x+d.x1*w,y+d.y1*h,paint);
                paint.setStyle(Paint.Style.FILL);paint.setTextSize(24);
                c.drawText(Decoder.KOREAN[d.cls]+" "+Math.round(d.score*100)+"%",x+d.x0*w,Math.max(y+26,y+d.y0*h-4),paint);
            }
        }
    }
}
