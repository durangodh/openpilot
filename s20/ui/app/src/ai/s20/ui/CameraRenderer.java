package ai.s20.ui;

import android.opengl.GLES20;
import android.opengl.GLSurfaceView;

import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;

import javax.microedition.khronos.egl.EGLConfig;
import javax.microedition.khronos.opengles.GL10;

/** Draws the newest NV12 frame of /data/s20pilot/share/road.ring (written by s20cam) with a YUV->RGB shader. */
final class CameraRenderer implements GLSurfaceView.Renderer {
  static final String RING = "/data/s20pilot/share/road.ring";
  private final Geometry geo;
  private MappedByteBuffer ring;
  private int w, h, frameSize, slots;
  private long lastId = -1;
  private ByteBuffer yBuf, uvBuf;
  private int prog, texY, texUV, aPos, aTex;
  private FloatBuffer quad;
  volatile float fps;
  private long fpsT0 = System.nanoTime(), fpsN;

  CameraRenderer(Geometry geo) { this.geo = geo; }

  private boolean openRing() {
    try {
      RandomAccessFile f = new RandomAccessFile(RING, "r");
      MappedByteBuffer m = f.getChannel().map(FileChannel.MapMode.READ_ONLY, 0, f.length());
      m.order(ByteOrder.LITTLE_ENDIAN);
      if (m.getInt(0) != 0x43303253) return false;
      w = m.getInt(8); h = m.getInt(12); frameSize = m.getInt(16); slots = m.getInt(20);
      yBuf = ByteBuffer.allocateDirect(w * h); uvBuf = ByteBuffer.allocateDirect(w * h / 2);
      ring = m;
      return true;
    } catch (Exception e) { return false; }
  }

  @Override public void onSurfaceCreated(GL10 gl, EGLConfig cfg) {
    String vs = "attribute vec2 p; attribute vec2 t; varying vec2 v; void main(){ v=t; gl_Position=vec4(p,0.,1.); }";
    String fs = "precision mediump float; varying vec2 v; uniform sampler2D Y; uniform sampler2D UV;"
        + "void main(){ float y=texture2D(Y,v).r; vec2 c=texture2D(UV,v).ra-0.5;"
        + " gl_FragColor=vec4(y+1.402*c.y, y-0.344*c.x-0.714*c.y, y+1.772*c.x, 1.); }";
    prog = GLES20.glCreateProgram();
    GLES20.glAttachShader(prog, shader(GLES20.GL_VERTEX_SHADER, vs));
    GLES20.glAttachShader(prog, shader(GLES20.GL_FRAGMENT_SHADER, fs));
    GLES20.glLinkProgram(prog);
    aPos = GLES20.glGetAttribLocation(prog, "p"); aTex = GLES20.glGetAttribLocation(prog, "t");
    int[] t = new int[2];
    GLES20.glGenTextures(2, t, 0);
    texY = t[0]; texUV = t[1];
    for (int id : t) {
      GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, id);
      GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
      GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
      GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
      GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
    }
    GLES20.glPixelStorei(GLES20.GL_UNPACK_ALIGNMENT, 1);
  }

  private static int shader(int type, String src) {
    int s = GLES20.glCreateShader(type);
    GLES20.glShaderSource(s, src);
    GLES20.glCompileShader(s);
    return s;
  }

  @Override public void onSurfaceChanged(GL10 gl, int width, int height) {
    GLES20.glViewport(0, 0, width, height);
    geo.update(width, height);
    // texture coords of the visible part of the frame
    float u0 = geo.sx / Geometry.FW, u1 = (geo.sx + geo.sw) / Geometry.FW;
    float v0 = geo.sy / Geometry.FH, v1 = (geo.sy + geo.sh) / Geometry.FH;
    float[] q = {-1, -1, u0, v1, 1, -1, u1, v1, -1, 1, u0, v0, 1, 1, u1, v0};
    quad = ByteBuffer.allocateDirect(q.length * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
    quad.put(q).position(0);
  }

  @Override public void onDrawFrame(GL10 gl) {
    if (ring == null && !openRing()) { GLES20.glClearColor(0, 0, 0, 1); GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT); return; }
    long id = ring.getLong(24) - 1;
    if (id >= 0 && id != lastId) {
      int slot = (int) (id % slots), off = 4096 + slot * frameSize;
      if (ring.getLong(64 + 32 * slot) == id + 1) {
        ByteBuffer src = ring.duplicate();
        src.position(off).limit(off + w * h); yBuf.position(0); yBuf.put(src); yBuf.position(0);
        src.limit(off + w * h * 3 / 2).position(off + w * h); uvBuf.position(0); uvBuf.put(src); uvBuf.position(0);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texY);
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES20.GL_LUMINANCE, w, h, 0, GLES20.GL_LUMINANCE, GLES20.GL_UNSIGNED_BYTE, yBuf);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texUV);
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES20.GL_LUMINANCE_ALPHA, w / 2, h / 2, 0, GLES20.GL_LUMINANCE_ALPHA, GLES20.GL_UNSIGNED_BYTE, uvBuf);
        lastId = id; fpsN++;
      }
    }
    long now = System.nanoTime();
    if (now - fpsT0 > 2_000_000_000L) { fps = fpsN * 1e9f / (now - fpsT0); fpsN = 0; fpsT0 = now; }
    GLES20.glUseProgram(prog);
    GLES20.glActiveTexture(GLES20.GL_TEXTURE0); GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texY);
    GLES20.glUniform1i(GLES20.glGetUniformLocation(prog, "Y"), 0);
    GLES20.glActiveTexture(GLES20.GL_TEXTURE1); GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texUV);
    GLES20.glUniform1i(GLES20.glGetUniformLocation(prog, "UV"), 1);
    quad.position(0); GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 16, quad); GLES20.glEnableVertexAttribArray(aPos);
    quad.position(2); GLES20.glVertexAttribPointer(aTex, 2, GLES20.GL_FLOAT, false, 16, quad); GLES20.glEnableVertexAttribArray(aTex);
    GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);
  }
}
