package ai.comma.nmirrorpanel;
import org.junit.Test;
import static org.junit.Assert.*;
public class PlaybackMathTest {
 @Test public void projectsPlaybackAndClampsToDuration() {
  assertEquals(3500,PlaybackMath.position(2000,1000,1.5f,true,2000,10000));
  assertEquals(3000,PlaybackMath.position(2000,1000,1.5f,true,2000,3000));
  assertEquals(2000,PlaybackMath.position(2000,1000,1.5f,false,2000,3000));
 }
 @Test public void handlesInvalidAndLiveTimeline() {
  assertEquals(0,PlaybackMath.position(-1,0,1,true,2000,0));
  assertEquals(2000,PlaybackMath.position(2000,3000,1,true,2000,0));
  assertEquals(2000,PlaybackMath.position(2000,1000,Float.NaN,true,2000,0));
  assertEquals(5000,PlaybackMath.position(2000,1000,1,true,4000,0));
 }
 @Test public void playingWinsOverPaused() { assertTrue(PlaybackMath.score(3,false)>PlaybackMath.score(2,true)); }
 @Test public void formatsTime() { assertEquals("0:00",PlaybackMath.time(-20)); assertEquals("3:05",PlaybackMath.time(185000)); assertEquals("1:01:01",PlaybackMath.time(3661000)); }
}
