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
import androidx.test.core.app.ActivityScenario;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.FileInputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class PanelInstrumentedTest {
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
   scenario.onActivity(a->a.launchAdjacent(new Intent(Settings.ACTION_SETTINGS)));
   AtomicBoolean split=new AtomicBoolean();
   for(int n=0;n<30&&!split.get();n++){Thread.sleep(200); scenario.onActivity(a->split.set(a.isInMultiWindowMode()));}
   assertTrue("Android 16 must enter split screen",split.get());
   shell("screencap -p /sdcard/Download/navi-music-split.png");
   session.setActive(false); session.release();
   AtomicBoolean cleared=new AtomicBoolean();
   for(int n=0;n<30&&!cleared.get();n++){Thread.sleep(200); scenario.onActivity(a->cleared.set(!((TextView)a.findViewById(R.id.song_title)).getText().toString().equals("밤의 드라이브")));}
   assertTrue("Destroyed session must clear stale metadata",cleared.get());
  } finally { session.release(); shell("cmd notification disallow_listener ai.comma.nmirrorpanel/.MediaAccessService"); }
 }
}
