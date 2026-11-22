// SPDX-License-Identifier: AGPL-3.0-or-later
package ai.comma.signallab;

import android.app.Service;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.SystemClock;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Collections;
import org.json.JSONArray;
import org.json.JSONObject;

public final class SignalService extends Service {
    public static final class Snapshot {
        public final Bitmap image;
        public final List<Decoder.Detection> detections, highlights;
        public final String text;
        public final long at;
        Snapshot(Bitmap b,List<Decoder.Detection> d,String t){this(b,d,Collections.emptyList(),t);}
        Snapshot(Bitmap b,List<Decoder.Detection> d,List<Decoder.Detection> h,String t){image=b;detections=d;highlights=h;text=t;at=SystemClock.elapsedRealtime();}
    }
    static volatile Snapshot snapshot=new Snapshot(null,Collections.emptyList(),"연결 대기");
    static volatile boolean active;
    volatile boolean running;
    Thread worker;
    PowerManager.WakeLock wake;
    @Override public IBinder onBind(Intent i){return null;}
    @Override public void onCreate(){
        super.onCreate();
        NotificationManager manager=getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("signal_lab","EON 신호등",NotificationManager.IMPORTANCE_LOW));
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop=PendingIntent.getService(this,1,new Intent(this,SignalService.class).setAction("STOP"),PendingIntent.FLAG_IMMUTABLE);
        Notification notification=new Notification.Builder(this,"signal_lab").setSmallIcon(android.R.drawable.ic_menu_view)
                .setContentTitle("한국 신호등 인식").setContentText("EON 영상 분석 · 이온 자동 정지 보조")
                .setContentIntent(open).addAction(new Notification.Action.Builder(null,"중지",stop).build()).setOngoing(true).build();
        startForeground(71,notification);
    }
    @Override public int onStartCommand(Intent intent,int flags,int id){
        if(intent!=null&&"STOP".equals(intent.getAction())){stopSelf();return START_NOT_STICKY;}
        if(worker==null){running=true;active=true;worker=new Thread(this::run,"korean-signal-inference");worker.start();}
        return START_STICKY;
    }
    static String endpoint(String value)throws Exception{
        if(!value.startsWith("http://"))value="http://"+value;
        URL url=new URL(value);
        String host=url.getHost();
        if(!host.matches("[0-9.]+")||url.getUserInfo()!=null||url.getQuery()!=null||url.getRef()!=null
                ||(!url.getPath().isEmpty()&&!url.getPath().equals("/")))throw new IllegalArgumentException("이온의 로컬 IP:7799를 입력하세요");
        InetAddress ip=InetAddress.getByName(host);
        if(!ip.isSiteLocalAddress()&&!ip.isLoopbackAddress())throw new IllegalArgumentException("로컬 네트워크 주소만 연결합니다");
        return "http://"+host+":"+(url.getPort()==-1?7799:url.getPort());
    }
    HttpURLConnection connection(String endpoint,String path,String token)throws Exception{
        HttpURLConnection c=(HttpURLConnection)new URL(endpoint+path).openConnection();
        c.setInstanceFollowRedirects(false);c.setConnectTimeout(1800);c.setReadTimeout(1800);
        c.setRequestProperty("Authorization","Bearer "+token);c.setUseCaches(false);return c;
    }
    static byte[] bounded(InputStream in,int limit)throws Exception{
        java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream();byte[] b=new byte[16384];int n;
        while((n=in.read(b))!=-1){if(out.size()+n>limit)throw new IllegalArgumentException("응답 크기 초과");out.write(b,0,n);}return out.toByteArray();
    }
    static List<Decoder.Detection> highlights(JSONObject selection)throws Exception{
        java.util.ArrayList<Decoder.Detection> out=new java.util.ArrayList<>();
        if(selection==null||selection.optInt("version")!=1||!"stable".equals(selection.optString("status"))
                ||!Boolean.FALSE.equals(selection.opt("ego_signal_confirmed")))return out;
        JSONArray ds=selection.optJSONArray("detections");
        if(ds==null||ds.length()>4)return out;
        for(int i=0;i<ds.length();i++){
            JSONObject d=ds.getJSONObject(i);int cls=-1;
            for(int j=0;j<Decoder.LABELS.length;j++)if(j!=3&&Decoder.LABELS[j].equals(d.optString("label")))cls=j;
            JSONArray box=d.getJSONArray("box");float score=(float)d.getDouble("score");
            if(cls<0||box.length()!=4||!Float.isFinite(score)||score<.35f||score>1f)throw new IllegalArgumentException("잘못된 중앙 후보");
            float[] b=new float[4];
            for(int j=0;j<4;j++){b[j]=(float)box.getDouble(j);if(!Float.isFinite(b[j])||b[j]<0||b[j]>1)throw new IllegalArgumentException("잘못된 후보 위치");}
            if(b[2]<=b[0]||b[3]<=b[1])throw new IllegalArgumentException("빈 후보 위치");
            out.add(new Decoder.Detection(cls,score,b[0],b[1],b[2],b[3]));
        }
        return out;
    }
    static String selectionText(JSONObject selection,List<Decoder.Detection> chosen){
        if(selection==null||selection.optInt("version")!=1)return "EON 업데이트 필요 · 안정화 결과 없음";
        String state=selection.optString("status");
        if("stable".equals(state)&&!chosen.isEmpty()){
            java.util.ArrayList<String> names=new java.util.ArrayList<>();
            for(Decoder.Detection d:chosen)names.add(Decoder.KOREAN[d.cls]);
            return "내 앞 신호: "+android.text.TextUtils.join("/",names)+" · 이온 정지 보조에 사용";
        }
        if("ambiguous".equals(state))return "비슷한 위치의 여러 후보 · 미확정";
        if("conflict".equals(state))return "신호 색 충돌 · 미확정";
        if("warming".equals(state))return "중앙 후보 연속 확인 중";
        return "중앙 후보 없음";
    }
    void run(){
        long sessionStart=SystemClock.elapsedRealtime();
        try{
            String base=endpoint(getSharedPreferences("signal",0).getString("host",""));
            String token=getSharedPreferences("signal",0).getString("token","").trim();
            if(!token.matches("[0-9]{4}"))throw new IllegalArgumentException("이온에 표시된 연결 코드 4자리를 입력하세요");
            wake=((PowerManager)getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"EonSignalLab:trial");
            wake.acquire(12*60*60*1000L);   // 2026-11-22: runs the whole drive (was a 10 min trial)
            snapshot=new Snapshot(null,Collections.emptyList(),"한국형 모델 로딩 중");
            try(KoreanDetector detector=new KoreanDetector(this)){
                while(running){
                    long begin=SystemClock.elapsedRealtime();
                    try{
                        byte[] jpeg;String session;int frame;
                        HttpURLConnection get=connection(base,"/frame.jpg",token);
                        try{
                            int code=get.getResponseCode();
                            if(code!=200)throw new IllegalStateException(code==401?"연결 코드가 맞지 않습니다 (4자리 확인)":code==429?"입력 횟수 초과 · 잠시 후 재시도":"이온 카메라 대기 (HTTP "+code+")");
                            session=get.getHeaderField("X-Signal-Session");frame=Integer.parseInt(get.getHeaderField("X-Signal-Frame"));
                            if(session==null||session.length()!=24)throw new IllegalStateException("이온 시험 서버의 영상이 아닙니다");
                            try(InputStream in=get.getInputStream()){jpeg=bounded(in,2*1024*1024);}
                        }finally{get.disconnect();}
                        BitmapFactory.Options bounds=new BitmapFactory.Options();bounds.inJustDecodeBounds=true;
                        BitmapFactory.decodeByteArray(jpeg,0,jpeg.length,bounds);
                        if(bounds.outWidth<=0||bounds.outHeight<=0||bounds.outWidth>1920||bounds.outHeight>1440)throw new IllegalStateException("잘못된 영상 크기");
                        Bitmap image=BitmapFactory.decodeByteArray(jpeg,0,jpeg.length);
                        if(image==null)throw new IllegalStateException("영상 해석 실패");
                        long t=SystemClock.elapsedRealtime();List<Decoder.Detection> found=detector.detect(image);
                        long infer=SystemClock.elapsedRealtime()-t;
                        JSONObject message=new JSONObject().put("session",session).put("frame_id",frame)
                                .put("model_sha256",KoreanDetector.SHA).put("infer_ms",infer);
                        JSONArray ds=new JSONArray();
                        for(Decoder.Detection d:found){
                            ds.put(new JSONObject().put("label",Decoder.LABELS[d.cls]).put("score",d.score)
                                    .put("box",new JSONArray(new double[]{d.x0,d.y0,d.x1,d.y1})));
                        }
                        message.put("detections",ds);
                        HttpURLConnection post=connection(base,"/result",token);
                        JSONObject reply;
                        try{
                            post.setRequestMethod("POST");post.setDoOutput(true);post.setRequestProperty("Content-Type","application/json");
                            byte[] body=message.toString().getBytes(StandardCharsets.UTF_8);post.setFixedLengthStreamingMode(body.length);
                            try(OutputStream out=post.getOutputStream()){out.write(body);}
                            int code=post.getResponseCode();
                            if(code!=200)throw new IllegalStateException(code==400?"결과가 늦거나 세션이 바뀌어 폐기됨":"결과 전송 실패 (HTTP "+code+")");
                            try(InputStream in=post.getInputStream()){reply=new JSONObject(new String(bounded(in,8192),StandardCharsets.UTF_8));}
                        }finally{post.disconnect();}
                        JSONObject selection=reply.optJSONObject("selection");
                        List<Decoder.Detection> chosen=highlights(selection);
                        String status=selectionText(selection,chosen)+"\n"+String.format(java.util.Locale.ROOT,
                                "원본 %d개 · 추론 %dms · 왕복 %.0fms",found.size(),infer,reply.optDouble("roundtrip_ms"));
                        snapshot=new Snapshot(image,found,chosen,status);
                    }catch(Exception e){if(running){snapshot=new Snapshot(null,Collections.emptyList(),e.getMessage()==null?e.getClass().getSimpleName():e.getMessage());Thread.sleep(3000);}}
                    long wait=200-(SystemClock.elapsedRealtime()-begin);
                    if(wait>0)Thread.sleep(wait);else Thread.sleep(20);
                }
            }
                    }catch(Exception e){if(running)snapshot=new Snapshot(null,Collections.emptyList(),"시작 실패: "+e.getMessage());}
        finally{running=false;active=false;if(wake!=null&&wake.isHeld())wake.release();stopSelf();}
    }
    @Override public void onDestroy(){running=false;active=false;if(worker!=null)worker.interrupt();super.onDestroy();}
}
