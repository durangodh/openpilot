package ai.comma.nmirrorpanel;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ActivityOptions;
import android.app.ActivityManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ResolveInfo;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.PlaybackState;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.*;
import java.util.ArrayList;
import java.util.LinkedHashMap;

public final class MainActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private SharedPreferences preferences;
    private MediaRepository repository;
    private MediaController controller;
    private TextView title, artist, source, elapsed, total, hint;
    private ImageView artwork;
    private ProgressBar progress;
    private TransportButton play, previous, next;
    private Button navigation, settings;
    private ScrollView scroll;
    private View spacer, bottomSpacer;
    private int lastWidth, lastHeight;
    private float lastDensity, lastFontScale;
    private LinearLayout root;
    private boolean firstResume;
    private final Runnable ticker = new Runnable() {
        @Override public void run() { updateProgress(); handler.postDelayed(this, 500); }
    };
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setDecorFitsSystemWindows(false);
        preferences=getSharedPreferences("panel",MODE_PRIVATE);
        repository=new MediaRepository(this,this::render);
        firstResume=saved==null;
        buildUi();
    }
    @Override protected void onStart() { super.onStart(); repository.start(preferences.getString("player","")); handler.post(ticker); }
    @Override protected void onResume() {
        super.onResume();
        repository.start(preferences.getString("player",""));
        if(firstResume) { firstResume=false; if(preferences.getBoolean("autoSplit",false)) handler.post(this::openNavigation); }
    }
    @Override protected void onStop() { handler.removeCallbacks(ticker); repository.stop(); super.onStop(); }
    @Override public void onConfigurationChanged(Configuration configuration) { super.onConfigurationChanged(configuration); updateLayout(); }
    @Override public void onMultiWindowModeChanged(boolean mode, Configuration configuration) { super.onMultiWindowModeChanged(mode,configuration); updateLayout(); }
    private int dp(float value) { return Math.round(value*getResources().getDisplayMetrics().density); }
    private TextView text(String value,int size,int color) {
        TextView view=new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color); return view;
    }
    private Button button(String label) { Button b=new Button(this); b.setText(label); b.setAllCaps(false); b.setTextColor(0xffeaf3f4); b.setMinHeight(dp(44)); return b; }
    private void buildUi() {
        FrameLayout frame=new FrameLayout(this);
        artwork=new ImageView(this); artwork.setScaleType(ImageView.ScaleType.CENTER_CROP); artwork.setAlpha(1f);
        frame.addView(artwork,new FrameLayout.LayoutParams(-1,-1));
        View shade=new View(this); shade.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,new int[]{0x5510191e,0x9910191e,0xf510191e}));
        frame.addView(shade,new FrameLayout.LayoutParams(-1,-1));
        scroll=new ScrollView(this); scroll.setFillViewport(true); frame.addView(scroll,new FrameLayout.LayoutParams(-1,-1));
        root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(dp(20),dp(12),dp(20),dp(12)); scroll.addView(root,new ScrollView.LayoutParams(-1,-1));
        LinearLayout header=new LinearLayout(this); header.setGravity(Gravity.CENTER_VERTICAL);
        source=text("NAVI MUSIC",12,0xff70e2c8); source.setTypeface(null,Typeface.BOLD); source.setMaxLines(1);
        header.addView(source,new LinearLayout.LayoutParams(0,dp(48),1)); source.setGravity(Gravity.CENTER_VERTICAL);
        settings=button("설정"); header.addView(settings,new LinearLayout.LayoutParams(dp(72),dp(48))); settings.setOnClickListener(v->showSettings()); root.addView(header);
        spacer=new View(this); root.addView(spacer,new LinearLayout.LayoutParams(1,dp(12),1));
        title=text("음악을 재생해 주세요",28,Color.WHITE); title.setId(R.id.song_title); title.setTypeface(null,Typeface.BOLD); title.setMaxLines(3); root.addView(title);
        artist=text("같은 기기의 음악 앱과 연결됩니다",16,0xffb8c8cd); artist.setId(R.id.song_artist); artist.setMaxLines(2); artist.setPadding(0,dp(8),0,dp(18)); root.addView(artist);
        progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal); progress.setMax(1000); progress.setProgressTintList(android.content.res.ColorStateList.valueOf(0xff70e2c8)); root.addView(progress,new LinearLayout.LayoutParams(-1,dp(8)));
        LinearLayout times=new LinearLayout(this); elapsed=text("0:00",12,0xffb8c8cd); total=text("0:00",12,0xffb8c8cd); total.setGravity(Gravity.END); times.addView(elapsed,new LinearLayout.LayoutParams(0,dp(28),1)); times.addView(total,new LinearLayout.LayoutParams(0,dp(28),1)); root.addView(times);
        LinearLayout transport=new LinearLayout(this); transport.setGravity(Gravity.CENTER);
        previous=new TransportButton(this,0,"이전 곡"); play=new TransportButton(this,1,"재생"); play.setId(R.id.play_pause); next=new TransportButton(this,3,"다음 곡");
        transport.addView(previous,new LinearLayout.LayoutParams(dp(64),dp(64))); transport.addView(play,new LinearLayout.LayoutParams(dp(84),dp(84))); transport.addView(next,new LinearLayout.LayoutParams(dp(64),dp(64))); root.addView(transport);
        previous.setOnClickListener(v->transport(0)); play.setOnClickListener(v->transport(1)); next.setOnClickListener(v->transport(2));
        bottomSpacer=new View(this); root.addView(bottomSpacer,new LinearLayout.LayoutParams(1,dp(8),1));
        navigation=button("지도와 함께 열기"); navigation.setId(R.id.navigation_button); navigation.setOnClickListener(v->openNavigation()); root.addView(navigation,new LinearLayout.LayoutParams(-1,dp(50)));
        hint=text("",12,0xffb8c8cd); hint.setPadding(0,dp(8),0,0); hint.setOnClickListener(v->{ if(!repository.authorized()) openAccess(); else showSettings(); }); root.addView(hint);
        frame.setOnApplyWindowInsetsListener((v,insets)->{ Insets i=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout()); v.setPadding(i.left,i.top,i.right,i.bottom); return insets; });
        title.setEllipsize(TextUtils.TruncateAt.END); artist.setEllipsize(TextUtils.TruncateAt.END);
        source.setEllipsize(TextUtils.TruncateAt.END);
        navigation.setSingleLine(true); navigation.setEllipsize(TextUtils.TruncateAt.END);
        settings.setSingleLine(true);
        scroll.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->updateLayout());
        setContentView(frame); frame.requestApplyInsets(); updateLayout(); render(null,repository.authorized());
    }
    /** Use the inset-free viewport, never the scrolling content's measured height. */
    private void updateLayout() {
        if(scroll==null || title==null) return;
        int widthPx=scroll.getWidth(), heightPx=scroll.getHeight();
        if(widthPx<=0 || heightPx<=0) return;
        float density=getResources().getDisplayMetrics().density;
        float fontScale=getResources().getConfiguration().fontScale;
        if(widthPx==lastWidth && heightPx==lastHeight && density==lastDensity && fontScale==lastFontScale) return;
        lastWidth=widthPx; lastHeight=heightPx; lastDensity=density; lastFontScale=fontScale;
        float width=widthPx/density, height=heightPx/density;
        float scale=Math.max(.70f, Math.min(Math.min(1.65f,height/440f),
                (float)Math.sqrt(width/420f)*Math.max(.75f, Math.min(1.10f,height/480f))));
        // Keep three reachable controls even in the narrowest supported window.
        float horizontal=Math.min(20*scale,Math.max(8,(width-160)/2));
        root.setPadding(dp(horizontal),dp(12*scale),dp(horizontal),dp(12*scale));
        title.setTextSize(Math.max(18,28*scale));
        title.setMaxLines(height<440?2:3);
        artist.setTextSize(Math.max(12,16*scale)); artist.setMaxLines(height<360?1:2);
        artist.setPadding(0,dp(8*scale),0,dp(18*scale));
        source.setTextSize(Math.max(11,12*scale));
        elapsed.setTextSize(Math.max(11,12*scale)); total.setTextSize(Math.max(11,12*scale));
        hint.setTextSize(Math.max(11,12*scale)); hint.setPadding(0,dp(8*scale),0,0);
        settings.setTextSize(Math.max(12,14*scale)); navigation.setTextSize(Math.max(12,14*scale));
        settings.setPadding(dp(4),0,dp(4),0); navigation.setPadding(dp(8),0,dp(8),0);
        float side=Math.max(48,64*scale), center=Math.max(56,84*scale);
        resize(previous,dp(side),dp(side)); resize(play,dp(center),dp(center)); resize(next,dp(side),dp(side));
        resize(source,0,dp(Math.max(48,48*scale)));
        resize(settings,dp(Math.max(52,72*scale)),dp(Math.max(48,48*scale)));
        resize(elapsed,0,dp(Math.max(24,28*scale))); resize(total,0,dp(Math.max(24,28*scale)));
        resize(progress,-1,dp(Math.max(6,8*scale)));
        resize(navigation,-1,dp(Math.max(48,50*scale)));
        resize(spacer,1,dp(12*scale)); resize(bottomSpacer,1,dp(8*scale));
    }
    private void resize(View view,int width,int height) {
        android.view.ViewGroup.LayoutParams params=view.getLayoutParams();
        if(params.width!=width || params.height!=height) { params.width=width; params.height=height; view.setLayoutParams(params); }
    }
    private String appName(String pkg) { try { return getPackageManager().getApplicationLabel(getPackageManager().getApplicationInfo(pkg,0)).toString(); } catch(Exception e) { return pkg; } }
    private void render(MediaController current,boolean authorized) {
        controller=current;
        MediaMetadata m=current==null?null:current.getMetadata();
        CharSequence song=MediaInfo.title(m), singer=MediaInfo.artist(m);
        title.setText(song.length()>0?song:!authorized?"음악 연결을 허용해 주세요":current==null?"음악을 재생해 주세요":"곡 정보가 전달되지 않습니다");
        artist.setText(singer.length()>0?singer:current==null?"같은 기기의 음악 앱과 연결됩니다":"아티스트 정보 없음");
        source.setText(current==null?"NAVI MUSIC":appName(current.getPackageName()));
        Bitmap art=m==null?null:m.getBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART);
        if(art==null && m!=null) art=m.getBitmap(MediaMetadata.METADATA_KEY_ART);
        if(art==null && m!=null) art=m.getBitmap(MediaMetadata.METADATA_KEY_DISPLAY_ICON);
        artwork.setImageBitmap(art);
        hint.setText(!authorized?"여기를 눌러 알림 접근을 허용하세요":current==null||song.length()==0?"설정 → 연결 상태에서 곡 정보 수신을 확인하세요":"분할 위치·크기는 가운데 구분선으로 조정하세요");
        updateProgress();
    }
    private void updateProgress() {
        PlaybackState state=controller==null?null:controller.getPlaybackState();
        MediaMetadata m=controller==null?null:controller.getMetadata();
        boolean playing=state!=null&&state.getState()==PlaybackState.STATE_PLAYING;
        long duration=m==null?0:Math.max(0,m.getLong(MediaMetadata.METADATA_KEY_DURATION));
        long position=state==null?0:PlaybackMath.position(state.getPosition(),state.getLastPositionUpdateTime(),state.getPlaybackSpeed(),playing,SystemClock.elapsedRealtime(),duration);
        elapsed.setText(PlaybackMath.time(position)); total.setText(PlaybackMath.time(duration)); progress.setProgress(duration>0?(int)Math.min(1000,1000.0*position/duration):0);
        long actions=state==null?0:state.getActions();
        previous.setEnabled((actions&PlaybackState.ACTION_SKIP_TO_PREVIOUS)!=0); next.setEnabled((actions&PlaybackState.ACTION_SKIP_TO_NEXT)!=0);
        play.setEnabled((actions&(PlaybackState.ACTION_PLAY_PAUSE|(playing?PlaybackState.ACTION_PAUSE:PlaybackState.ACTION_PLAY)))!=0);
        play.setKind(playing?2:1); play.setContentDescription(playing?"일시정지":"재생");
    }
    private void transport(int action) {
        if(controller==null) return;
        MediaController.TransportControls controls=controller.getTransportControls();
        if(action==0) controls.skipToPrevious(); else if(action==2) controls.skipToNext(); else {
            PlaybackState state=controller.getPlaybackState();
            if(state!=null&&state.getState()==PlaybackState.STATE_PLAYING) controls.pause(); else controls.play();
        }
    }
    void launchAdjacent(Intent intent) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT);
        ActivityOptions options=ActivityOptions.makeBasic();
        if(getDisplay()!=null) options.setLaunchDisplayId(getDisplay().getDisplayId());
        startActivity(intent,options.toBundle());
    }
    private void openNavigation() {
        if(isFinishing()||isDestroyed()) return;
        String pkg=preferences.getString("navigation","com.nhn.android.nmap");
        Intent intent=getPackageManager().getLaunchIntentForPackage(pkg);
        if(intent==null) { message("지도 앱이 없습니다","선택한 지도 앱을 이 기기에 설치하거나 설정에서 다른 지도를 선택하세요."); return; }
        if(((ActivityManager)getSystemService(ACTIVITY_SERVICE)).isLowRamDevice()) { message("분할 화면 확인","이 시스템은 저메모리 기기로 설정되어 있습니다. Lineage의 멀티윈도 지원 여부를 확인하세요."); return; }
        try { launchAdjacent(intent); } catch(RuntimeException e) { message("지도를 열지 못했습니다","최근 앱 화면에서 Navi Music과 지도 앱을 분할 화면으로 선택해 주세요."); }
    }
    private void openAccess() { try { startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)); } catch(RuntimeException e) { message("설정 확인","Android 설정에서 Navi Music의 알림 접근을 허용해 주세요."); } }
    private void message(String title,String body) { new AlertDialog.Builder(this).setTitle(title).setMessage(body).setPositiveButton("확인",null).show(); }
    private void showSettings() {
        String[] rows={"음악 연결 권한", "지도 선택", "음악 앱 선택", "음악 앱 열기", "시작할 때 지도 함께 열기: "+(preferences.getBoolean("autoSplit",false)?"켜짐":"꺼짐"),"연결 도움말","연결 상태 · 1.2.0"};
        new AlertDialog.Builder(this).setTitle("Navi Music 설정").setItems(rows,(dialog,which)->{
            if(which==0) openAccess();
            else if(which==1) new AlertDialog.Builder(this).setTitle("지도 선택").setSingleChoiceItems(new String[]{"네이버 지도","TMAP"},preferences.getString("navigation","").equals("com.skt.tmap.ku")?1:0,(d,n)->{ preferences.edit().putString("navigation",n==0?"com.nhn.android.nmap":"com.skt.tmap.ku").apply(); d.dismiss(); }).show();
            else if(which==2) choosePlayer();
            else if(which==3) openPlayer();
            else if(which==4) { preferences.edit().putBoolean("autoSplit",!preferences.getBoolean("autoSplit",false)).apply(); showSettings(); }
            else if(which==6) message("음악 연결 상태",repository.diagnostics());
            else message("nMirror 연결","1. 이 기기의 음악 앱에서 재생\n2. Navi Music 알림 접근 허용\n3. 지도와 함께 열기\n4. nMirror에서 기본 화면을 미러링\n\n분할이 안 되면 최근 앱에서 수동으로 분할하세요. 좌우 순서와 비율은 시스템에서 조정합니다. 다른 휴대폰에서 재생하는 음악은 표시되지 않습니다. 차량 음성 버튼 연결은 nMirror·지도 앱의 별도 기능입니다.");
        }).setNegativeButton("닫기",null).show();
    }
    private void choosePlayer() {
        LinkedHashMap<String,String> choices=new LinkedHashMap<>(); choices.put("","자동 선택 · 재생 중인 앱 우선");
        String[] known={"com.neowiz.android.bugs","com.nhn.android.nmap","com.spotify.music","com.google.android.apps.youtube.music","com.sec.android.app.music","com.maxmpz.audioplayer","org.videolan.vlc"};
        for(String pkg:known) if(getPackageManager().getLaunchIntentForPackage(pkg)!=null) choices.put(pkg,appName(pkg));
        for(ResolveInfo r:getPackageManager().queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_APP_MUSIC),0)) choices.put(r.activityInfo.packageName,appName(r.activityInfo.packageName));
        for(String pkg:repository.activePackages()) choices.put(pkg,appName(pkg));
        if(controller!=null) choices.put(controller.getPackageName(),appName(controller.getPackageName()));
        ArrayList<String> packages=new ArrayList<>(choices.keySet());
        new AlertDialog.Builder(this).setTitle("음악 앱 선택").setItems(choices.values().toArray(new String[0]),(d,n)->{ preferences.edit().putString("player",packages.get(n)).apply(); repository.start(packages.get(n)); }).show();
    }
    private void openPlayer() {
        String pkg=preferences.getString("player",""); if(pkg.isEmpty()&&controller!=null) pkg=controller.getPackageName();
        Intent intent=pkg.isEmpty()?new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_APP_MUSIC):getPackageManager().getLaunchIntentForPackage(pkg);
        try { if(intent==null) throw new IllegalStateException(); startActivity(intent); } catch(RuntimeException e) { message("음악 앱 실행","홈 화면에서 음악 앱을 열어 재생한 뒤 Navi Music으로 돌아오세요."); }
    }
}
