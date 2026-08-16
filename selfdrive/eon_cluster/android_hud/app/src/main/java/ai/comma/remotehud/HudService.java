package ai.comma.remotehud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.hardware.usb.UsbDevice;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import org.json.JSONArray;
import org.json.JSONObject;

/** S9-only remote HUD renderer. EON only forwards compact state/native TMAP assets. */
public final class HudService extends Service {
    static final String ACTION_RESCAN_USB = "ai.comma.remotehud.RESCAN_USB";
    static final String EXTRA_FROM_BOOT = "ai.comma.remotehud.FROM_BOOT";

    private static final String CHANNEL = "remote_hud";
    private static final int WIDTH = 1920;
    private static final int HEIGHT = 462;
    private static final int DRIVE_RIGHT = 768;
    private static final int SYSTEM_LEFT = 776;
    private static final int SYSTEM_RIGHT = 1144;
    private static final int MAP_LEFT = 1152;
    private static final long BOOT_START_DELAY_MS = 30000L;

    private static volatile long lastEonRxElapsed;
    private static volatile int lastJpegBytes;
    private static volatile long lastJpegSentElapsed;
    private static volatile boolean mapConnected;
    private static volatile float measuredFps;
    private static volatile boolean serviceRunning;
    private static volatile boolean usbConnected;
    private static volatile boolean usbError;
    private static volatile String lastEonAddress = "--";
    private static volatile String usbStatus = "미연결 · 1CBE:0092";

    private TurzxDisplay display;
    private Bitmap egoCar;
    private Bitmap otherCar;
    private Thread receiverThread;
    private Thread mapThread;
    private Thread renderThread;
    private boolean usbReceiverRegistered;
    private volatile boolean workersStarted;
    private PowerManager.WakeLock wakeLock;

    private final Handler starter = new Handler(Looper.getMainLooper());
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicReference<JSONObject> state = new AtomicReference<>(new JSONObject());
    private final AtomicReference<Bitmap> mapFrame = new AtomicReference<>();
    private final AtomicReference<Bitmap> tbtCurrentFrame = new AtomicReference<>();
    private final AtomicReference<Bitmap> tbtNextFrame = new AtomicReference<>();
    private final AtomicReference<Bitmap> laneFrame = new AtomicReference<>();
    private final AtomicReference<InetAddress> eonAddress = new AtomicReference<>();
    private final Object assetLock = new Object();

    private final BroadcastReceiver usbReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            UsbDevice device = (UsbDevice) intent.getParcelableExtra("device");
            if (!TurzxDisplay.isTarget(device)) return;
            String action = intent.getAction();
            if ("android.hardware.usb.action.USB_DEVICE_DETACHED".equals(action)) {
                if (display != null) display.reset();
                usbStatus = "분리됨 · 재연결 대기";
                usbConnected = false;
                usbError = false;
            } else if ("ai.comma.remotehud.USB_PERMISSION".equals(action)) {
                boolean granted = intent.getBooleanExtra("permission", false);
                if (display != null) display.reset();
                usbStatus = granted ? "USB 권한 허용 · 연결 중" : "USB 권한 거부됨 · 재검색 필요";
                usbConnected = false;
                usbError = !granted;
            } else if ("android.hardware.usb.action.USB_DEVICE_ATTACHED".equals(action)) {
                requestUsbRescan();
            }
        }
    };

    public static final class StatusSnapshot {
        final String eonAddress; final boolean eonConnected; final float fps;
        final int lastJpegBytes; final boolean mapConnected; final boolean running;
        final boolean usbConnected; final boolean usbError; final String usbStatus;
        StatusSnapshot(boolean running, boolean eonConnected, String eonAddress,
                       boolean mapConnected, String usbStatus, boolean usbConnected,
                       boolean usbError, float fps, int lastJpegBytes) {
            this.running=running; this.eonConnected=eonConnected; this.eonAddress=eonAddress;
            this.mapConnected=mapConnected; this.usbStatus=usbStatus; this.usbConnected=usbConnected;
            this.usbError=usbError; this.fps=fps; this.lastJpegBytes=lastJpegBytes;
        }
    }

    public static StatusSnapshot getStatusSnapshot() {
        long now=SystemClock.elapsedRealtime();
        boolean eonOk=serviceRunning && lastEonRxElapsed>0 && now-lastEonRxElapsed<2000;
        boolean fpsOk=serviceRunning && lastJpegSentElapsed>0 && now-lastJpegSentElapsed<2000;
        return new StatusSnapshot(serviceRunning,eonOk,lastEonAddress,serviceRunning&&mapConnected,
                usbStatus,serviceRunning&&usbConnected,usbError,fpsOk?measuredFps:0.0f,fpsOk?lastJpegBytes:0);
    }

    @Override public void onCreate() {
        super.onCreate();
        egoCar=BitmapFactory.decodeResource(getResources(),R.drawable.hud_ego_car);
        otherCar=BitmapFactory.decodeResource(getResources(),R.drawable.hud_other_car);
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        if(Build.VERSION.SDK_INT>=26) nm.createNotificationChannel(new NotificationChannel(CHANNEL,"EON Remote HUD",NotificationManager.IMPORTANCE_LOW));
        Notification.Builder builder=Build.VERSION.SDK_INT>=26?new Notification.Builder(this,CHANNEL):new Notification.Builder(this);
        startForeground(72,builder.setContentTitle("EON Remote HUD").setContentText("상태 화면을 열려면 누르세요")
                .setSmallIcon(android.R.drawable.ic_menu_directions)
                .setContentIntent(PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT))
                .setOngoing(true).build());
        IntentFilter filter=new IntentFilter();
        filter.addAction("android.hardware.usb.action.USB_DEVICE_ATTACHED");
        filter.addAction("android.hardware.usb.action.USB_DEVICE_DETACHED");
        filter.addAction("ai.comma.remotehud.USB_PERMISSION");
        if(Build.VERSION.SDK_INT>=33) registerReceiver(usbReceiver,filter,Context.RECEIVER_NOT_EXPORTED); else registerReceiver(usbReceiver,filter);
        usbReceiverRegistered=true;
    }

    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if(intent!=null&&ACTION_RESCAN_USB.equals(intent.getAction())&&running.get()){requestUsbRescan();return START_STICKY;}
        if(running.get()) return START_STICKY;
        running.set(true); serviceRunning=true; mapConnected=false; usbConnected=false; usbError=false; measuredFps=0; lastJpegBytes=0;
        acquireWakeLock();
        boolean fromBoot=intent!=null&&intent.getBooleanExtra(EXTRA_FROM_BOOT,false);
        if(fromBoot){usbStatus="부팅 대기 "+(BOOT_START_DELAY_MS/1000L)+"초";starter.postDelayed(this::startWorkers,BOOT_START_DELAY_MS);} else startWorkers();
        return START_STICKY;
    }

    private void startWorkers(){
        if(!running.get()||workersStarted)return; workersStarted=true; usbStatus="외부 HUD 검색 중"; display=new TurzxDisplay(this);
        receiverThread=new Thread(this::receiveLoop,"hud-telemetry"); mapThread=new Thread(this::mapLoop,"hud-tmap"); renderThread=new Thread(this::renderLoop,"hud-render");
        receiverThread.start();mapThread.start();renderThread.start();
    }

    private void acquireWakeLock(){try{if(wakeLock==null){PowerManager pm=(PowerManager)getSystemService(POWER_SERVICE);wakeLock=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"RemoteHUD::render");wakeLock.setReferenceCounted(false);}if(!wakeLock.isHeld())wakeLock.acquire();}catch(Exception ignored){}}
    private void releaseWakeLock(){try{if(wakeLock!=null&&wakeLock.isHeld())wakeLock.release();}catch(Exception ignored){}wakeLock=null;}

    @Override public void onTaskRemoved(Intent rootIntent){if(AppPrefs.isAutoStart(this)){try{startForegroundService(new Intent(getApplicationContext(),HudService.class));}catch(Exception ignored){}}super.onTaskRemoved(rootIntent);}
    public void requestUsbRescan(){if(display!=null)display.reset();usbStatus="외부 HUD 재검색 중";usbConnected=false;usbError=false;measuredFps=0;lastJpegBytes=0;}

    private void receiveLoop(){
        while(running.get()){
            try(DatagramSocket socket=new DatagramSocket(7210)){
                socket.setBroadcast(true);socket.setSoTimeout(1000);byte[] buffer=new byte[16384];
                while(running.get()){
                    try{DatagramPacket packet=new DatagramPacket(buffer,buffer.length);socket.receive(packet);state.set(new JSONObject(new String(packet.getData(),packet.getOffset(),packet.getLength(),"UTF-8")));eonAddress.set(packet.getAddress());lastEonRxElapsed=SystemClock.elapsedRealtime();lastEonAddress=packet.getAddress().getHostAddress();byte[] ack="HUD1".getBytes("US-ASCII");socket.send(new DatagramPacket(ack,ack.length,packet.getAddress(),packet.getPort()));}catch(SocketTimeoutException ignored){}
                }
            }catch(Exception ignored){SystemClock.sleep(1000L);}
        }
    }

    private static boolean tagEquals(byte[] h,String tag){return h.length==4&&h[0]==tag.charAt(0)&&h[1]==tag.charAt(1)&&h[2]==tag.charAt(2)&&h[3]==tag.charAt(3);}
    private void replaceAsset(AtomicReference<Bitmap> target,byte[] data){Bitmap decoded=data.length==0?null:BitmapFactory.decodeByteArray(data,0,data.length);if(data.length>0&&decoded==null)return;Bitmap old=target.getAndSet(decoded);if(old!=null&&old!=decoded)old.recycle();}

    private void mapLoop(){
        while(running.get()){
            InetAddress address=eonAddress.get();if(address==null){SystemClock.sleep(500L);continue;}
            try(Socket socket=new Socket()){
                socket.connect(new InetSocketAddress(address,7211),2000);mapConnected=true;socket.setSoTimeout(4000);DataInputStream in=new DataInputStream(socket.getInputStream());byte[] header=new byte[4];
                while(running.get()&&address.equals(eonAddress.get())){
                    in.readFully(header);int length=in.readInt();if(length<0||length>2097152)throw new Exception("bad asset size");byte[] data=new byte[length];if(length>0)in.readFully(data);
                    synchronized(assetLock){if(tagEquals(header,"MAP1"))replaceAsset(mapFrame,data);else if(tagEquals(header,"TBT1"))replaceAsset(tbtCurrentFrame,data);else if(tagEquals(header,"TBT2"))replaceAsset(tbtNextFrame,data);else if(tagEquals(header,"LANE"))replaceAsset(laneFrame,data);else throw new Exception("bad asset tag");}
                }
            }catch(Exception ignored){mapConnected=false;SystemClock.sleep(500L);}
        }mapConnected=false;
    }

    private void renderLoop(){
        long fpsStart=SystemClock.elapsedRealtime(),nextFrame=0L;int frames=0;
        while(running.get()){
            long now=SystemClock.elapsedRealtime();if(now<nextFrame){SystemClock.sleep(Math.min(20L,nextFrame-now));continue;}nextFrame=now+125L;
            try{
                if(!display.openOrRequestPermission()){usbStatus=display.describeStatus();usbConnected=false;usbError=false;SystemClock.sleep(500L);continue;}
                usbStatus="연결됨 · USB 권한 허용";usbConnected=true;usbError=false;Bitmap frame;
                synchronized(assetLock){frame=render(state.get(),mapFrame.get(),tbtCurrentFrame.get(),tbtNextFrame.get(),laneFrame.get());}
                ByteArrayOutputStream output=new ByteArrayOutputStream(180000);Matrix matrix=new Matrix();matrix.setRotate(-90.0f);Bitmap portrait=Bitmap.createBitmap(frame,0,0,frame.getWidth(),frame.getHeight(),matrix,true);frame.recycle();portrait.compress(Bitmap.CompressFormat.JPEG,55,output);portrait.recycle();byte[] jpeg=output.toByteArray();display.sendJpeg(jpeg);lastJpegBytes=jpeg.length;lastJpegSentElapsed=SystemClock.elapsedRealtime();frames++;long span=lastJpegSentElapsed-fpsStart;if(span>=1000L){measuredFps=frames*1000.0f/span;fpsStart=lastJpegSentElapsed;frames=0;}
            }catch(Exception e){frames=0;usbStatus="USB 오류 · "+e.getMessage();usbConnected=false;usbError=true;display.close();SystemClock.sleep(500L);}
        }
    }

    private JSONObject layout(JSONObject s){JSONObject l=s.optJSONObject("layout");return l==null?new JSONObject():l;}
    private float lv(JSONObject l,String key,float def){double v=l.optDouble(key,def);return Double.isFinite(v)?(float)v:def;}
    private int lc(JSONObject l,String key,int def){int v=l.optInt(key,-1);return v<0?def:Color.rgb((v>>16)&255,(v>>8)&255,v&255);}
    private int beginElement(Canvas c,JSONObject l,String name,float px,float py){int save=c.save();float dx=lv(l,name+"Dx",0),dy=lv(l,name+"Dy",0),scale=lv(l,name+"Scale",1);scale=Math.max(0.5f,Math.min(2.0f,scale));c.translate(dx,dy);c.scale(scale,scale,px,py);return save;}

    private Bitmap render(JSONObject s,Bitmap map,Bitmap tbtCurrent,Bitmap tbtNext,Bitmap lane){Bitmap frame=Bitmap.createBitmap(WIDTH,HEIGHT,Bitmap.Config.RGB_565);Canvas c=new Canvas(frame);Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);c.drawColor(Color.rgb(5,8,12));drawDriving(c,p,s);JSONObject l=layout(s);int save=beginElement(c,l,"system",960,231);drawSystem(c,p,s);c.restoreToCount(save);drawMap(c,p,s,map,tbtCurrent,tbtNext,lane);return frame;}

    private void drawDriving(Canvas c,Paint p,JSONObject s){
        JSONObject l=layout(s);boolean enabled=s.optBoolean("enabled",false);p.setStyle(Paint.Style.FILL);p.setColor(lc(l,"driveBg",Color.rgb(239,241,242)));c.drawRect(0,0,DRIVE_RIGHT-3,HEIGHT,p);drawWorld(c,p,s,enabled);
        int save=beginElement(c,l,"lights",70,28);drawLights(c,p,s);c.restoreToCount(save);
        save=beginElement(c,l,"prnd",90,116);drawPrnd(c,p,s.optString("gear","--"));c.restoreToCount(save);
        save=beginElement(c,l,"speed",384,74);drawSpeed(c,p,s.optInt("speed",0));c.restoreToCount(save);
        drawModeAndEta(c,p,s);
        p.setColor(Color.rgb(202,207,210));p.setStrokeWidth(1);c.drawLine(18,129,DRIVE_RIGHT-21,129,p);
        save=beginElement(c,l,"wheel",70,171);drawSteeringWheel(c,p,70,171,(float)s.optDouble("steer",0),enabled);c.restoreToCount(save);
        save=beginElement(c,l,"set",384,171);drawSetSpeed(c,p,384,171,s.optInt("set",0),enabled);c.restoreToCount(save);
        save=beginElement(c,l,"camera",698,171);drawCamera(c,p,698,171,s.optInt("camera",0),s.optInt("cameraDist",0),s.optBoolean("cameraSection",false));c.restoreToCount(save);
        save=beginElement(c,l,"lead",82,415);drawLeadCard(c,p,s.optJSONObject("lead"));c.restoreToCount(save);
        save=beginElement(c,l,"tpms",678,415);drawTpms(c,p,s.optJSONObject("tpms"));c.restoreToCount(save);
        save=beginElement(c,l,"atc",678,309);drawAtc(c,p,s);c.restoreToCount(save);
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2);p.setColor(Color.rgb(188,194,198));c.drawRoundRect(new RectF(2,2,DRIVE_RIGHT-5,HEIGHT-4),18,18,p);
    }

    private void drawSpeed(Canvas c,Paint p,int speed){text(c,p,Integer.toString(Math.max(0,speed)),384,74,68,Color.rgb(18,18,18),Paint.Align.CENTER);text(c,p,"KM",384,117,16,Color.rgb(104,111,116),Paint.Align.CENTER);}
    private void drawPrnd(Canvas c,Paint p,String gear){float x=26;for(String g:new String[]{"P","R","N","D"}){text(c,p,g,x,116,30,g.equals(gear)?Color.rgb(18,18,18):Color.rgb(174,179,182),Paint.Align.LEFT);x+=42;}}

    private void drawModeAndEta(Canvas c,Paint p,JSONObject s){
        JSONObject l=layout(s);int mode=s.optInt("drivingMode",3);String label="NORM";int color=Color.rgb(68,76,82);if(mode==1){label="SAFE";color=Color.rgb(226,144,38);}else if(mode==2){label="ECO";color=Color.rgb(20,160,92);}else if(mode==4){label="FAST";color=Color.rgb(222,67,70);}
        float modeX=lv(l,"modeX",DRIVE_RIGHT-26),modeY=lv(l,"modeY",116),modeSize=lv(l,"modeSize",29);text(c,p,label,modeX,modeY,modeSize,color,Paint.Align.RIGHT);
        JSONObject navi=s.optJSONObject("navi");if(navi!=null&&navi.optBoolean("active",false)){int remain=navi.optInt("remainTime",0);if(remain>0){long etaMs=System.currentTimeMillis()+remain*1000L;String eta=new SimpleDateFormat("HH:mm",Locale.KOREA).format(new Date(etaMs));float etaRight=lv(l,"etaRight",620),etaY=lv(l,"etaY",116),etaTimeSize=lv(l,"etaTimeSize",27),etaLabelSize=lv(l,"etaLabelSize",14),gap=lv(l,"etaGap",8);p.setTypeface(Typeface.create("sans",Typeface.BOLD));p.setTextSize(etaTimeSize);float etaWidth=p.measureText(eta);text(c,p,eta,etaRight,etaY,etaTimeSize,Color.rgb(68,76,82),Paint.Align.RIGHT);text(c,p,"도착",etaRight-etaWidth-gap,etaY-1,etaLabelSize,Color.rgb(68,76,82),Paint.Align.RIGHT);}}
    }

    private void drawLights(Canvas c,Paint p,JSONObject s){float x=21;if(s.optBoolean("lowBeam",false)){drawLamp(c,p,x,28,0);x+=45;}if(s.optBoolean("highBeam",false)){drawLamp(c,p,x,28,1);x+=45;}if(s.optBoolean("frontFog",false))drawLamp(c,p,x,28,2);}
    private void drawLamp(Canvas c,Paint p,float x,float y,int kind){int color=kind==1?Color.rgb(44,128,238):Color.rgb(39,177,89);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(3);p.setColor(color);RectF housing=new RectF(x+22,y-10,x+36,y+10);c.drawArc(housing,90,180,false,p);for(int i=-1;i<=1;i++){float yy=y+i*7;if(kind==2)c.drawLine(x,yy+4,x+18,yy,p);else c.drawLine(x,yy,x+18,yy,p);}if(kind==2){Path wave=new Path();wave.moveTo(x+9,y-12);wave.lineTo(x+13,y-6);wave.lineTo(x+9,y);wave.lineTo(x+13,y+6);wave.lineTo(x+9,y+12);c.drawPath(wave,p);}}

    private void drawSteeringWheel(Canvas c,Paint p,float cx,float cy,float angle,boolean enabled){float r=36;int bg=enabled?Color.rgb(18,95,225):Color.rgb(92,101,107);p.setStyle(Paint.Style.FILL);p.setColor(bg);c.drawCircle(cx,cy,r,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(4);p.setColor(Color.rgb(246,248,249));c.drawCircle(cx,cy,r-8,p);double rad=Math.toRadians(-angle);for(double deg:new double[]{-90,30,150}){double a=rad+Math.toRadians(deg);c.drawLine(cx+(float)Math.cos(a)*7,cy+(float)Math.sin(a)*7,cx+(float)Math.cos(a)*24,cy+(float)Math.sin(a)*24,p);}p.setStyle(Paint.Style.FILL);c.drawCircle(cx,cy,6,p);}
    private void drawSetSpeed(Canvas c,Paint p,float cx,float cy,int set,boolean enabled){boolean valid=enabled&&set>0&&set<255;int accent=valid?Color.rgb(18,149,224):Color.rgb(139,147,152);p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(246,247,247));c.drawCircle(cx,cy,36,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(6);p.setColor(accent);c.drawCircle(cx,cy,36,p);text(c,p,valid?Integer.toString(set):"--",cx,cy+9,29,Color.rgb(18,18,18),Paint.Align.CENTER);text(c,p,"SET",cx,cy+55,14,accent,Paint.Align.CENTER);}
    private void drawCamera(Canvas c,Paint p,float cx,float cy,int limit,int dist,boolean section){if(limit<=0)return;p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(250,250,250));c.drawCircle(cx,cy,36,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(6);p.setColor(Color.rgb(220,45,45));c.drawCircle(cx,cy,36,p);text(c,p,Integer.toString(limit),cx,cy+9,29,Color.rgb(20,20,20),Paint.Align.CENTER);if(dist>0)text(c,p,(section?"구간 ":"")+distanceText(dist),cx,cy+60,18,Color.rgb(18,18,18),Paint.Align.CENTER);}

    private void drawLeadCard(Canvas c,Paint p,JSONObject lead){RectF box=new RectF(8,376,156,454);drawCard(c,p,box);double d=lead==null?0:lead.optDouble("d",0),v=lead==null?0:lead.optDouble("v",0);text(c,p,"앞차",18,400,13,Color.rgb(103,111,116),Paint.Align.LEFT);text(c,p,d>0?String.format(Locale.US,"%.0f m",d):"--",145,400,19,Color.rgb(18,18,18),Paint.Align.RIGHT);p.setColor(Color.rgb(195,201,204));p.setStrokeWidth(1);c.drawLine(16,414,148,414,p);text(c,p,"상대",18,440,13,Color.rgb(103,111,116),Paint.Align.LEFT);text(c,p,d>0?String.format(Locale.US,"%+.0f km/h",v):"--",145,440,17,Color.rgb(18,18,18),Paint.Align.RIGHT);}
    private void drawTpms(Canvas c,Paint p,JSONObject tpms){RectF box=new RectF(604,376,752,454);drawCard(c,p,box);text(c,p,"TPMS",678,394,12,Color.rgb(86,94,100),Paint.Align.CENTER);float fl=tpmsValue(tpms,"fl"),fr=tpmsValue(tpms,"fr"),rl=tpmsValue(tpms,"rl"),rr=tpmsValue(tpms,"rr");p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(95,102,107));c.drawRoundRect(new RectF(665,404,691,446),4,4,p);text(c,p,tpmsText(fl),653,417,16,Color.rgb(18,18,18),Paint.Align.RIGHT);text(c,p,tpmsText(fr),703,417,16,Color.rgb(18,18,18),Paint.Align.LEFT);text(c,p,tpmsText(rl),653,443,16,Color.rgb(18,18,18),Paint.Align.RIGHT);text(c,p,tpmsText(rr),703,443,16,Color.rgb(18,18,18),Paint.Align.LEFT);}
    private float tpmsValue(JSONObject t,String key){return t==null?-1:(float)t.optDouble(key,-1);} private String tpmsText(float v){return v>=5&&v<=60?Integer.toString(Math.round(v)):"--";}

    private void drawAtc(Canvas c,Paint p,JSONObject s){JSONObject navi=s.optJSONObject("navi");int atcMode=s.optInt("atcMode",0);if(atcMode<1||atcMode>3||navi==null||!navi.optBoolean("active",false)||!navi.optBoolean("guidanceLive",false))return;int dist=navi.optInt("turnDist",-1);if(dist<0)return;RectF box=new RectF(604,249,752,368);p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(31,35,38));c.drawRoundRect(box,10,10,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2);p.setColor(Color.rgb(238,241,243));c.drawRoundRect(box,10,10,p);String title=navi.optString("title","경로 안내");if(title.length()>12)title=title.substring(0,11)+"…";text(c,p,title,678,269,14,Color.rgb(248,249,250),Paint.Align.CENTER);boolean blink=dist>350||((SystemClock.elapsedRealtime()/500L)&1L)==0L;if(blink)drawAtcArrow(c,p,678,305,navi.optInt("turnType",0));text(c,p,distanceText(dist),678,346,22,Color.rgb(248,249,250),Paint.Align.CENTER);int remain=navi.optInt("remainDist",0);if(remain>0)text(c,p,"남은 "+distanceText(remain),678,363,11,Color.rgb(180,188,194),Paint.Align.CENTER);}
    private void drawAtcArrow(Canvas c,Paint p,float cx,float cy,int type){int direction=0;if(type==12||type==16||type==20||type==3||type==5)direction=-1;else if(type==13||type==18||type==21||type==4||type==6)direction=1;p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(6);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);p.setColor(Color.WHITE);Path path=new Path();if(type==14){path.moveTo(cx+10,cy+18);path.lineTo(cx+10,cy-8);path.quadTo(cx+10,cy-23,cx-8,cy-23);path.quadTo(cx-26,cy-23,cx-26,cy-5);path.moveTo(cx-26,cy-5);path.lineTo(cx-35,cy-14);path.moveTo(cx-26,cy-5);path.lineTo(cx-17,cy-14);}else if(direction!=0){path.moveTo(cx,cy+20);path.lineTo(cx,cy-7);path.lineTo(cx+direction*25,cy-7);path.moveTo(cx+direction*25,cy-7);path.lineTo(cx+direction*14,cy-17);path.moveTo(cx+direction*25,cy-7);path.lineTo(cx+direction*14,cy+3);}else{path.moveTo(cx,cy+20);path.lineTo(cx,cy-22);path.moveTo(cx,cy-22);path.lineTo(cx-9,cy-11);path.moveTo(cx,cy-22);path.lineTo(cx+9,cy-11);}c.drawPath(path,p);}
    private void drawCard(Canvas c,Paint p,RectF box){p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(232,235,237));c.drawRoundRect(box,9,9,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2);p.setColor(Color.rgb(158,166,171));c.drawRoundRect(box,9,9,p);}
    private String distanceText(int meters){if(meters>=1000)return String.format(Locale.US,"%.1f km",meters/1000.0f);return meters+" m";}

    private void drawWorld(Canvas c,Paint p,JSONObject s,boolean enabled){JSONObject l=layout(s);final float top=217,bottom=454,cx=DRIVE_RIGHT/2.0f;p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(lc(l,"driveBg",Color.rgb(239,241,242)));c.drawRect(0,top,DRIVE_RIGHT-3,bottom,p);p.setShader(new LinearGradient(0,top,0,bottom,lc(l,"roadTop",Color.rgb(226,229,231)),lc(l,"roadBottom",Color.rgb(216,220,223)),Shader.TileMode.CLAMP));Path road=new Path();road.moveTo(cx-40,top);road.lineTo(cx+40,top);road.lineTo(DRIVE_RIGHT-20,bottom);road.lineTo(20,bottom);road.close();c.drawPath(road,p);p.setShader(null);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(1);p.setColor(Color.rgb(198,203,206));for(int i=1;i<=5;i++){float t=i/6.0f,y=top+(bottom-top)*t,half=40+(DRIVE_RIGHT/2.0f-60)*t;c.drawLine(cx-half,y,cx+half,y,p);}JSONArray lanes=s.optJSONArray("lanes");if(lanes!=null)for(int i=0;i<lanes.length();i++){JSONObject lane=lanes.optJSONObject(i);if(lane==null)continue;JSONArray pts=lane.optJSONArray("p");if(pts!=null)drawWorldLine(c,p,pts,cx,top,bottom,Color.rgb(248,249,249),2.5f,true);}JSONArray path=s.optJSONArray("path");if(enabled&&path!=null&&path.length()>1)drawPath(c,p,path,cx,top,bottom,lc(l,"pathColor",Color.rgb(24,126,224)));JSONObject lead2=s.optJSONObject("lead2");if(lead2!=null)drawLeadVehicle(c,p,lead2,cx,top,bottom,false);JSONObject lead=s.optJSONObject("lead");if(lead!=null)drawLeadVehicle(c,p,lead,cx,top,bottom,true);drawVehicle(c,p,egoCar,cx,414,78,0,255);if(s.optBoolean("leftBsd",false))drawVehicle(c,p,otherCar,cx-88,425,42,-14,235);if(s.optBoolean("rightBsd",false))drawVehicle(c,p,otherCar,cx+88,425,42,14,235);if(s.optBoolean("leftBlinker",false)&&((SystemClock.elapsedRealtime()/500)&1)==0)drawBlinker(c,p,245,386,true);if(s.optBoolean("rightBlinker",false)&&((SystemClock.elapsedRealtime()/500)&1)==0)drawBlinker(c,p,523,386,false);}
    private float[] project(float x,float y,float cx,float top,float bottom){float d=Math.max(0,x),sy=bottom-((bottom-top)*d/(13+d)),scale=66/(1+d/17);return new float[]{cx-y*scale,sy};}
    private void drawWorldLine(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color,float width,boolean dashed){p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(width);p.setStrokeCap(Paint.Cap.ROUND);p.setColor(color);for(int i=0;i<pts.length()-1;i++){JSONArray a=pts.optJSONArray(i),b=pts.optJSONArray(i+1);if(a==null||b==null)continue;float x1=(float)a.optDouble(0),x2=(float)b.optDouble(0);if(dashed&&(((int)((x1+x2)*0.5/5))&1)!=0)continue;float[] pa=project(x1,(float)a.optDouble(1),cx,top,bottom),pb=project(x2,(float)b.optDouble(1),cx,top,bottom);c.drawLine(pa[0],pa[1],pb[0],pb[1],p);}}
    private void drawPath(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color){Path left=new Path(),right=new Path();boolean first=true;for(int i=0;i<pts.length();i++){JSONArray a=pts.optJSONArray(i);if(a==null)continue;float x=(float)a.optDouble(0),y=(float)a.optDouble(1);float[] l=project(x,y+0.75f,cx,top,bottom),r=project(x,y-0.75f,cx,top,bottom);if(first){left.moveTo(l[0],l[1]);right.moveTo(r[0],r[1]);first=false;}else{left.lineTo(l[0],l[1]);right.lineTo(r[0],r[1]);}}p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(4);p.setStrokeCap(Paint.Cap.ROUND);p.setColor(color);c.drawPath(left,p);c.drawPath(right,p);}
    private void drawLeadVehicle(Canvas c,Paint p,JSONObject lead,float cx,float top,float bottom,boolean primary){float d=(float)lead.optDouble("d",0),y=(float)lead.optDouble("y",0);if(d<=0||d>120)return;float[] pt=project(d,y,cx,top,bottom);float size=Math.max(25,primary?52-d*0.20f:44-d*0.16f);drawVehicle(c,p,otherCar,pt[0],pt[1]-10,size,0,primary?245:205);}
    private void drawVehicle(Canvas c,Paint p,Bitmap b,float cx,float cy,float width,float angle,int alpha){if(b==null||b.isRecycled())return;float h=b.getHeight()*width/b.getWidth();int save=c.save();c.translate(cx,cy);c.rotate(angle);p.setAlpha(alpha);p.setFilterBitmap(true);c.drawBitmap(b,null,new RectF(-width/2,-h,width/2,0),p);p.setAlpha(255);c.restoreToCount(save);}
    private void drawBlinker(Canvas c,Paint p,float x,float y,boolean left){p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(72,226,118));Path q=new Path();if(left){q.moveTo(x-16,y);q.lineTo(x+10,y-13);q.lineTo(x+10,y+13);}else{q.moveTo(x+16,y);q.lineTo(x-10,y-13);q.lineTo(x-10,y+13);}q.close();c.drawPath(q,p);}

    private String systemValue(JSONObject system,String key,String unit){if(system==null||system.isNull(key))return "--";double value=system.optDouble(key,Double.NaN);if(Double.isNaN(value)||Double.isInfinite(value))return "--";return String.format(Locale.US,"%.0f%s",value,unit);}
    private void drawSystemMetric(Canvas c,Paint p,float top,String label,String value){float left=SYSTEM_LEFT+14,right=SYSTEM_RIGHT-14,bottom=top+72;p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(16,23,32));c.drawRoundRect(new RectF(left,top,right,bottom),10,10,p);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2);p.setColor(Color.rgb(55,68,80));c.drawRoundRect(new RectF(left,top,right,bottom),10,10,p);text(c,p,label,left+14,top+44,20,Color.rgb(145,158,168),Paint.Align.LEFT);text(c,p,value,right-14,top+47,30,Color.rgb(235,240,245),Paint.Align.RIGHT);}
    private void drawSystem(Canvas c,Paint p,JSONObject s){p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(7,12,18));c.drawRect(SYSTEM_LEFT,0,SYSTEM_RIGHT,HEIGHT,p);text(c,p,"SYSTEM",960,31,24,Color.rgb(235,240,245),Paint.Align.CENTER);JSONObject system=s.optJSONObject("system");if(system==null)system=s;drawSystemMetric(c,p,54,"CPU",systemValue(system,"cpu","%"));drawSystemMetric(c,p,132,"TEMP",systemValue(system,"temp","°C"));drawSystemMetric(c,p,210,"ENGINE",systemValue(system,"engineTemp","°C"));drawSystemMetric(c,p,288,"COOLANT",systemValue(system,"coolantTemp","°C"));JSONArray cores=system.optJSONArray("cores");if(cores!=null&&cores.length()>0){StringBuilder sb=new StringBuilder();int count=Math.min(8,cores.length());for(int i=0;i<count;i++){if(i>0)sb.append("  ");sb.append('C').append(i).append(' ').append(Math.round(cores.optDouble(i,0))).append('%');}text(c,p,sb.toString(),960,430,12,Color.rgb(145,158,168),Paint.Align.CENTER);}}

    private void drawMap(Canvas c,Paint p,JSONObject s,Bitmap map,Bitmap tbtCurrent,Bitmap tbtNext,Bitmap lane){Rect dst=new Rect(MAP_LEFT,0,WIDTH,HEIGHT);if(map==null||map.isRecycled()){p.setStyle(Paint.Style.FILL);p.setColor(Color.BLACK);c.drawRect(dst,p);text(c,p,"TMAP 화면 대기",1536,240,34,Color.GRAY,Paint.Align.CENTER);return;}p.setFilterBitmap(true);c.drawBitmap(map,null,dst,p);JSONObject l=layout(s);int save=beginElement(c,l,"tbt1",MAP_LEFT+199,52);drawNativeOverlay(c,p,tbtCurrent,new RectF(MAP_LEFT+8,8,MAP_LEFT+390,96),Paint.Align.LEFT);c.restoreToCount(save);save=beginElement(c,l,"tbt2",MAP_LEFT+146,127);drawNativeOverlay(c,p,tbtNext,new RectF(MAP_LEFT+8,98,MAP_LEFT+285,156),Paint.Align.LEFT);c.restoreToCount(save);save=beginElement(c,l,"lane",(MAP_LEFT+WIDTH)/2.0f,HEIGHT-62);drawNativeOverlay(c,p,lane,new RectF(MAP_LEFT+185,HEIGHT-104,WIDTH-90,HEIGHT-20),Paint.Align.CENTER);c.restoreToCount(save);}
    private void drawNativeOverlay(Canvas c,Paint p,Bitmap bitmap,RectF bounds,Paint.Align align){if(bitmap==null||bitmap.isRecycled()||bitmap.getWidth()<=0||bitmap.getHeight()<=0)return;float scale=Math.min(bounds.width()/bitmap.getWidth(),bounds.height()/bitmap.getHeight()),w=bitmap.getWidth()*scale,h=bitmap.getHeight()*scale,x;if(align==Paint.Align.RIGHT)x=bounds.right-w;else if(align==Paint.Align.CENTER)x=bounds.centerX()-w/2;else x=bounds.left;float y=bounds.top+(bounds.height()-h)/2;p.setAlpha(255);p.setFilterBitmap(true);c.drawBitmap(bitmap,null,new RectF(x,y,x+w,y+h),p);}
    private static void text(Canvas c,Paint p,String value,float x,float y,float size,int color,Paint.Align align){p.setStyle(Paint.Style.FILL);p.setTypeface(Typeface.create("sans",Typeface.BOLD));p.setTextSize(size);p.setTextAlign(align);p.setColor(color);c.drawText(value,x,y,p);}
    private void recycleRef(AtomicReference<Bitmap> ref){Bitmap old=ref.getAndSet(null);if(old!=null)old.recycle();}

    @Override public void onDestroy(){running.set(false);starter.removeCallbacksAndMessages(null);workersStarted=false;serviceRunning=false;mapConnected=false;usbConnected=false;usbError=false;usbStatus="서비스 중지됨";measuredFps=0;lastJpegBytes=0;if(display!=null)display.close();if(egoCar!=null){egoCar.recycle();egoCar=null;}if(otherCar!=null){otherCar.recycle();otherCar=null;}synchronized(assetLock){recycleRef(mapFrame);recycleRef(tbtCurrentFrame);recycleRef(tbtNextFrame);recycleRef(laneFrame);}if(usbReceiverRegistered){try{unregisterReceiver(usbReceiver);}catch(Exception ignored){}usbReceiverRegistered=false;}releaseWakeLock();super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
