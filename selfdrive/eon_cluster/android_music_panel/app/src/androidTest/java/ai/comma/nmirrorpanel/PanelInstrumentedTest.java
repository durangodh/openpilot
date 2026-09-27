package ai.comma.nmirrorpanel;

import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.provider.Settings;
import android.widget.TextView;
import android.view.View;
import android.view.ViewGroup;
import androidx.test.core.app.ActivityScenario;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.FileInputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class PanelInstrumentedTest {
 @Test public void navigationMusicAndDisplayMetadata() {
  String naver="com.nhn.android.nmap", bugs="com.neowiz.android.bugs";
  PlaybackState voice=new PlaybackState.Builder().setState(PlaybackState.STATE_PLAYING,0,1).setActions(PlaybackState.ACTION_PAUSE).build();
  assertFalse("Navigation with no song metadata is ignored",MediaInfo.eligible(naver,null,voice,""));
  MediaMetadata guidance=new MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_TITLE,"길안내").build();
  assertFalse("Title-only guidance is not automatically a song",MediaInfo.eligible(naver,guidance,voice,""));
  MediaMetadata song=new MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE,"연동 음악").putString(MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE,"연동 가수").build();
  assertEquals("연동 음악",MediaInfo.title(song).toString());
  assertEquals("연동 가수",MediaInfo.artist(song).toString());
  assertTrue("Naver integrated music must be selectable",MediaInfo.eligible(naver,song,voice,""));
  assertTrue("Explicit navigation selection accepts title-only metadata",MediaInfo.eligible(naver,guidance,voice,naver));
  assertTrue("Standalone Bugs remains selectable",MediaInfo.eligible(bugs,null,voice,""));
  assertFalse("Losing metadata excludes guidance again",MediaInfo.eligible(naver,null,voice,naver));
  MediaMetadata blanks=new MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_TITLE,"  ").putString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE,"대체 곡명").putString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST,"앨범 가수").build();
  assertEquals("대체 곡명",MediaInfo.title(blanks).toString());
  assertEquals("앨범 가수",MediaInfo.artist(blanks).toString());
 }
 private void shell(String command) throws Exception {
  try(ParcelFileDescriptor fd=InstrumentationRegistry.getInstrumentation().getUiAutomation().executeShellCommand(command); FileInputStream in=new FileInputStream(fd.getFileDescriptor())) { byte[] b=new byte[1024]; while(in.read(b)!=-1) {} }
 }
 @Test public void sessionMetadataTransportAndSplitScreen() throws Exception {
  Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
  shell("cmd notification allow_listener ai.comma.nmirrorpanel/.MediaAccessService");
  Thread.sleep(1500);
  MediaSession session=new MediaSession(context,"panel-test");
  CountDownLatch paused=new CountDownLatch(1);
  session.setCallback(new MediaSession.Callback(){ @Override public void onPause(){paused.countDown();}},new Handler(Looper.getMainLooper()));
  Bitmap art=Bitmap.createBitmap(512,512,Bitmap.Config.ARGB_8888);
  Canvas canvas=new Canvas(art); canvas.drawColor(Color.rgb(28,88,92)); Paint p=new Paint(3); p.setColor(Color.rgb(105,207,184)); canvas.drawCircle(360,160,180,p);
  session.setMetadata(new MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_TITLE,"밤의 드라이브").putString(MediaMetadata.METADATA_KEY_ARTIST,"테스트 아티스트").putLong(MediaMetadata.METADATA_KEY_DURATION,240000).putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART,art).build());
  session.setPlaybackState(new PlaybackState.Builder().setState(PlaybackState.STATE_PLAYING,42000,1).setActions(PlaybackState.ACTION_PLAY|PlaybackState.ACTION_PAUSE|PlaybackState.ACTION_SKIP_TO_NEXT|PlaybackState.ACTION_SKIP_TO_PREVIOUS).build());
  session.setActive(true);
  try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(MainActivity.class)) {
   AtomicBoolean found=new AtomicBoolean();
   for(int n=0;n<30&&!found.get();n++){ Thread.sleep(200); scenario.onActivity(a->found.set(((TextView)a.findViewById(R.id.song_title)).getText().toString().equals("밤의 드라이브"))); }
   assertTrue("Active media title must appear",found.get());
   scenario.onActivity(a->{assertEquals("테스트 아티스트",((TextView)a.findViewById(R.id.song_artist)).getText().toString()); assertTrue(a.findViewById(R.id.play_pause).isEnabled()); a.findViewById(R.id.play_pause).performClick();});
   assertTrue("Pause must reach source media session",paused.await(3,TimeUnit.SECONDS));
   shell("screencap -p /sdcard/Download/navi-music-full.png");
   AtomicInteger fullWidth=new AtomicInteger();
   AtomicInteger fullButton=new AtomicInteger();
   final float[] fullTitle={0};
   scenario.onActivity(a->{fullButton.set(a.findViewById(R.id.play_pause).getWidth()); fullTitle[0]=((TextView)a.findViewById(R.id.song_title)).getTextSize();});
   scenario.onActivity(a->{fullWidth.set(a.getWindow().getDecorView().getWidth()); a.launchAdjacent(new Intent(Settings.ACTION_SETTINGS));});
   AtomicBoolean split=new AtomicBoolean();
   for(int n=0;n<30&&!split.get();n++){Thread.sleep(200); scenario.onActivity(a->split.set(a.isInMultiWindowMode() && a.getWindow().getDecorView().getWidth()<fullWidth.get()*0.8));}
   assertTrue("Android 16 must enter split screen",split.get());
   Thread.sleep(1500); // Capture after the system split-screen transition finishes.
   scenario.onActivity(a->{
    assertTrue("Title must shrink with split viewport",((TextView)a.findViewById(R.id.song_title)).getTextSize()<fullTitle[0]);
    assertTrue("Playback control must shrink with split viewport",a.findViewById(R.id.play_pause).getWidth()<fullButton.get());
   });
   shell("screencap -p /sdcard/Download/navi-music-split.png");
   final View[] viewport={null};
   final int[] originalSize={0,0};
   scenario.onActivity(a->{
    View root=(View)a.findViewById(R.id.song_title).getParent();
    viewport[0]=(View)root.getParent();
    ViewGroup.LayoutParams params=viewport[0].getLayoutParams();
    originalSize[0]=params.width; originalSize[1]=params.height;
    params.width=Math.round(240*a.getResources().getDisplayMetrics().density);
    params.height=Math.round(360*a.getResources().getDisplayMetrics().density);
    viewport[0].setLayoutParams(params);
   });
   Thread.sleep(800);
   AtomicInteger narrowButton=new AtomicInteger(); final float[] narrowTitle={0};
   scenario.onActivity(a->{
    View button=a.findViewById(R.id.play_pause); narrowButton.set(button.getWidth());
    narrowTitle[0]=((TextView)a.findViewById(R.id.song_title)).getTextSize();
    ViewGroup row=(ViewGroup)button.getParent();
    assertTrue("Controls must fit narrow panel",row.getChildAt(2).getRight()<=row.getWidth());
    assertTrue("Controls must not clip on left",row.getChildAt(0).getLeft()>=0);
    for(int n=0;n<3;n++) assertTrue("Minimum touch target",row.getChildAt(n).getWidth()>=48*a.getResources().getDisplayMetrics().density);
   });
   shell("screencap -p /sdcard/Download/navi-music-narrow.png");
   scenario.onActivity(a->{
    ViewGroup.LayoutParams params=viewport[0].getLayoutParams(); params.width=originalSize[0]; params.height=originalSize[1]; viewport[0].setLayoutParams(params);
   });
   Thread.sleep(800);
   scenario.onActivity(a->{
    assertTrue("Button must grow again",a.findViewById(R.id.play_pause).getWidth()>narrowButton.get());
    assertTrue("Title must grow again",((TextView)a.findViewById(R.id.song_title)).getTextSize()>narrowTitle[0]);
    assertEquals("Music survives resize","밤의 드라이브",((TextView)a.findViewById(R.id.song_title)).getText().toString());
   });
   session.setMetadata(new MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE,"표시용 곡명").putString(MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE,"표시용 가수").build());
   AtomicBoolean displayFound=new AtomicBoolean();
   for(int n=0;n<30&&!displayFound.get();n++){Thread.sleep(200); scenario.onActivity(a->displayFound.set(((TextView)a.findViewById(R.id.song_title)).getText().toString().equals("표시용 곡명")));}
   assertTrue("Live display-title updates must reach the panel",displayFound.get());
   scenario.onActivity(a->assertEquals("표시용 가수",((TextView)a.findViewById(R.id.song_artist)).getText().toString()));
   session.setMetadata(null);
   AtomicBoolean missing=new AtomicBoolean();
   for(int n=0;n<30&&!missing.get();n++){Thread.sleep(200); scenario.onActivity(a->missing.set(((TextView)a.findViewById(R.id.song_title)).getText().toString().equals("곡 정보가 전달되지 않습니다")));}
   assertTrue("Missing metadata must not retain the previous song",missing.get());
   session.setActive(false); session.release();
   AtomicBoolean cleared=new AtomicBoolean();
   for(int n=0;n<30&&!cleared.get();n++){Thread.sleep(200); scenario.onActivity(a->cleared.set(((TextView)a.findViewById(R.id.song_title)).getText().toString().equals("음악을 재생해 주세요")));}
   assertTrue("Destroyed session must clear stale metadata",cleared.get());
  } finally { session.release(); shell("cmd notification disallow_listener ai.comma.nmirrorpanel/.MediaAccessService"); }
 }
}
