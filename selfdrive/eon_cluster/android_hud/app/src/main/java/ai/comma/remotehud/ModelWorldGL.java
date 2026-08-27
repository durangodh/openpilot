package ai.comma.remotehud;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLSurface;
import android.opengl.GLES20;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.IntBuffer;

/**
 * First-stage modelV2-only road renderer.
 *
 * The renderer deliberately has no OSM, buildings, textures or lighting.
 * Geometry is projected with the same camera constants as World3D, then an
 * unlit OpenGL ES 2.0 shader draws road, observed boundaries, lane lines and
 * the final lateral path into an offscreen pbuffer.  Any EGL/GL failure returns
 * false so HudService can immediately draw the existing Canvas World3D.
 */
final class ModelWorldGL {
    private static final String TAG = "ModelWorldGL";
    private static final int WIDTH = 952;
    private static final int TOP = 217;
    private static final int BOTTOM = 454;
    private static final int HEIGHT = BOTTOM - TOP;
    private static final float CX = 476f;
    private static final float FOCAL = 520f;
    private static final float CAM_H = 4.6f;
    private static final float CAM_BACK = 13.0f;
    private static final float HORIZON = 249f;
    private static final float NEAR_DEPTH = 11.4f;
    private static final int MAX_POINTS = 80;
    private static final int MAX_VERTEX_FLOATS = 4096;

    private EGLDisplay display = EGL14.EGL_NO_DISPLAY;
    private EGLContext context = EGL14.EGL_NO_CONTEXT;
    private EGLSurface surface = EGL14.EGL_NO_SURFACE;
    private int program;
    private int positionHandle;
    private int colorHandle;
    private boolean failed;

    private final FloatBuffer vertexBuffer = ByteBuffer
            .allocateDirect(MAX_VERTEX_FLOATS * 4)
            .order(ByteOrder.nativeOrder()).asFloatBuffer();
    private final float[] vertices = new float[MAX_VERTEX_FLOATS];
    private final IntBuffer readBuffer = ByteBuffer
            .allocateDirect(WIDTH * HEIGHT * 4)
            .order(ByteOrder.nativeOrder()).asIntBuffer();
    private final int[] pixels = new int[WIDTH * HEIGHT];
    private final float[] projected = new float[2];
    private final Bitmap frame = Bitmap.createBitmap(WIDTH, HEIGHT, Bitmap.Config.ARGB_8888);

    private long lastTimestamp = Long.MIN_VALUE;
    private int lastStyle;
    private float horizonShift;
    private float roadZGain = 1f;

    private static final class Line {
        final float[] x = new float[MAX_POINTS];
        final float[] y = new float[MAX_POINTS];
        final float[] z = new float[MAX_POINTS];
        int count;
        float confidence = 1f;
    }

    boolean draw(Canvas canvas, Paint paint, JSONObject scene, boolean enabled,
                 int driveBg, int roadTop, int roadBottom, int pathColor,
                 boolean dark, float roadZPercent, float livePitch,
                 float pitchPercent, float calibPitch) {
        if (failed || scene == null) {
            return false;
        }
        try {
            if (!ensureGl()) {
                return false;
            }
            long timestamp = scene.optLong("t", 0L);
            int style = driveBg ^ roadTop ^ roadBottom ^ pathColor ^ (dark ? 1 : 0);
            if (timestamp != lastTimestamp || style != lastStyle) {
                if (!render(scene, enabled, driveBg, roadTop, roadBottom, pathColor,
                        dark, roadZPercent, livePitch, pitchPercent, calibPitch)) {
                    return false;
                }
                lastTimestamp = timestamp;
                lastStyle = style;
            }
            paint.setShader(null);
            paint.setAlpha(255);
            paint.setFilterBitmap(true);
            canvas.drawBitmap(frame, 0f, TOP, paint);
            return true;
        } catch (Throwable error) {
            failed = true;
            Log.e(TAG, "OpenGL renderer disabled; falling back to World3D", error);
            release();
            return false;
        }
    }

    private boolean ensureGl() {
        if (program != 0) {
            return EGL14.eglMakeCurrent(display, surface, surface, context);
        }
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        if (display == EGL14.EGL_NO_DISPLAY) {
            fail("No EGL display");
            return false;
        }
        int[] version = new int[2];
        if (!EGL14.eglInitialize(display, version, 0, version, 1)) {
            fail("EGL initialize failed");
            return false;
        }

        EGLConfig config = chooseConfig(true);
        if (config == null) {
            config = chooseConfig(false);
        }
        if (config == null) {
            fail("No pbuffer EGL config");
            return false;
        }

        int[] contextAttrs = {
                EGL14.EGL_CONTEXT_CLIENT_VERSION, 2,
                EGL14.EGL_NONE
        };
        context = EGL14.eglCreateContext(display, config, EGL14.EGL_NO_CONTEXT,
                contextAttrs, 0);
        int[] surfaceAttrs = {
                EGL14.EGL_WIDTH, WIDTH,
                EGL14.EGL_HEIGHT, HEIGHT,
                EGL14.EGL_NONE
        };
        surface = EGL14.eglCreatePbufferSurface(display, config, surfaceAttrs, 0);
        if (context == EGL14.EGL_NO_CONTEXT || surface == EGL14.EGL_NO_SURFACE
                || !EGL14.eglMakeCurrent(display, surface, surface, context)) {
            fail("EGL context or pbuffer creation failed");
            return false;
        }

        String vertexShader =
                "attribute vec2 aPosition;\n" +
                "void main() { gl_Position = vec4(aPosition, 0.0, 1.0); }";
        String fragmentShader =
                "precision mediump float;\n" +
                "uniform vec4 uColor;\n" +
                "void main() { gl_FragColor = uColor; }";
        int vs = compile(GLES20.GL_VERTEX_SHADER, vertexShader);
        int fs = compile(GLES20.GL_FRAGMENT_SHADER, fragmentShader);
        program = GLES20.glCreateProgram();
        GLES20.glAttachShader(program, vs);
        GLES20.glAttachShader(program, fs);
        GLES20.glLinkProgram(program);
        int[] linked = new int[1];
        GLES20.glGetProgramiv(program, GLES20.GL_LINK_STATUS, linked, 0);
        GLES20.glDeleteShader(vs);
        GLES20.glDeleteShader(fs);
        if (linked[0] == 0) {
            fail("GL link failed: " + GLES20.glGetProgramInfoLog(program));
            return false;
        }
        positionHandle = GLES20.glGetAttribLocation(program, "aPosition");
        colorHandle = GLES20.glGetUniformLocation(program, "uColor");
        GLES20.glUseProgram(program);
        GLES20.glEnableVertexAttribArray(positionHandle);
        GLES20.glDisable(GLES20.GL_DEPTH_TEST);
        GLES20.glDisable(GLES20.GL_CULL_FACE);
        GLES20.glEnable(GLES20.GL_BLEND);
        GLES20.glBlendFunc(GLES20.GL_SRC_ALPHA, GLES20.GL_ONE_MINUS_SRC_ALPHA);
        return true;
    }

    private EGLConfig chooseConfig(boolean multisample) {
        int[] attrs = multisample ? new int[] {
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT,
                EGL14.EGL_RED_SIZE, 8,
                EGL14.EGL_GREEN_SIZE, 8,
                EGL14.EGL_BLUE_SIZE, 8,
                EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_SAMPLE_BUFFERS, 1,
                EGL14.EGL_SAMPLES, 4,
                EGL14.EGL_NONE
        } : new int[] {
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT,
                EGL14.EGL_RED_SIZE, 8,
                EGL14.EGL_GREEN_SIZE, 8,
                EGL14.EGL_BLUE_SIZE, 8,
                EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_NONE
        };
        EGLConfig[] configs = new EGLConfig[1];
        int[] count = new int[1];
        return EGL14.eglChooseConfig(display, attrs, 0, configs, 0, 1, count, 0)
                && count[0] > 0 ? configs[0] : null;
    }

    private int compile(int type, String source) {
        int shader = GLES20.glCreateShader(type);
        GLES20.glShaderSource(shader, source);
        GLES20.glCompileShader(shader);
        int[] compiled = new int[1];
        GLES20.glGetShaderiv(shader, GLES20.GL_COMPILE_STATUS, compiled, 0);
        if (compiled[0] == 0) {
            String log = GLES20.glGetShaderInfoLog(shader);
            GLES20.glDeleteShader(shader);
            throw new IllegalStateException("GL compile failed: " + log);
        }
        return shader;
    }

    private boolean render(JSONObject scene, boolean enabled, int driveBg,
                           int roadTop, int roadBottom, int pathColor,
                           boolean dark, float roadZPercent, float livePitch,
                           float pitchPercent, float calibPitch) {
        if (!EGL14.eglMakeCurrent(display, surface, surface, context)) {
            return false;
        }
        Line path = decode(scene.optJSONArray("path"), 1f);
        if (path.count < 2) {
            return false;
        }

        roadZGain = clamp(roadZPercent * 0.01f, -3f, 3f);
        float dynamicPitch = clamp(livePitch * clamp(pitchPercent * 0.01f, 0f, 2f),
                -0.05f, 0.05f);
        float pitch = clamp(calibPitch + dynamicPitch, -0.15f, 0.15f);
        horizonShift = clamp(FOCAL * (float) Math.tan(pitch), -46f, 46f);

        GLES20.glViewport(0, 0, WIDTH, HEIGHT);
        GLES20.glClearColor(Color.red(driveBg) / 255f, Color.green(driveBg) / 255f,
                Color.blue(driveBg) / 255f, 1f);
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT);

        int sky = dark ? blend(driveBg, Color.BLACK, 0.35f)
                : blend(driveBg, Color.WHITE, 0.35f);
        int ground = dark ? blend(driveBg, Color.BLACK, 0.15f)
                : blend(driveBg, Color.BLACK, 0.10f);
        drawRect(0f, 0f, WIDTH, Math.max(0f, HORIZON + horizonShift - TOP), sky);
        drawRect(0f, Math.max(0f, HORIZON + horizonShift - TOP), WIDTH, HEIGHT, ground);

        Line leftEdge = null;
        Line rightEdge = null;
        JSONArray edges = scene.optJSONArray("edges");
        if (edges != null) {
            for (int i = 0; i < edges.length(); i++) {
                JSONObject edgeObject = edges.optJSONObject(i);
                if (edgeObject == null || edgeObject.optDouble("c", 0d) < 0.40d) {
                    continue;
                }
                Line edge = decode(edgeObject.optJSONArray("p"),
                        (float) edgeObject.optDouble("c", 0d));
                if (edge.count < 2) {
                    continue;
                }
                if (edge.y[0] > 0f && leftEdge == null) {
                    leftEdge = edge;
                } else if (edge.y[0] <= 0f && rightEdge == null) {
                    rightEdge = edge;
                }
            }
        }

        drawRoad(path, leftEdge, rightEdge, scene, roadBottom);
        if (leftEdge != null) {
            drawWorldLine(leftEdge, path, dark ? Color.rgb(126, 139, 153)
                    : Color.rgb(152, 161, 168), 2.0f, 0.92f);
        }
        if (rightEdge != null) {
            drawWorldLine(rightEdge, path, dark ? Color.rgb(126, 139, 153)
                    : Color.rgb(152, 161, 168), 2.0f, 0.92f);
        }

        JSONArray lanes = scene.optJSONArray("lanes");
        if (lanes != null) {
            for (int i = 0; i < lanes.length(); i++) {
                JSONObject laneObject = lanes.optJSONObject(i);
                if (laneObject == null) {
                    continue;
                }
                float confidence = (float) laneObject.optDouble("c", 0d);
                if (confidence < 0.45f) {
                    continue;
                }
                Line lane = decode(laneObject.optJSONArray("p"), confidence);
                int laneColor = (i == 1 || i == 2)
                        ? (dark ? Color.rgb(246, 206, 92) : Color.rgb(238, 196, 70))
                        : (dark ? Color.rgb(220, 226, 232) : Color.rgb(249, 250, 250));
                drawWorldLine(lane, path, laneColor, i == 1 || i == 2 ? 3.0f : 2.2f,
                        clamp(0.35f + confidence * 0.65f, 0f, 1f));
            }
        }

        if (enabled) {
            drawPath(path, pathColor);
        }
        GLES20.glFinish();
        copyPixels();
        return true;
    }

    private void drawRoad(Line path, Line left, Line right, JSONObject scene, int color) {
        int count;
        if (left != null && right != null) {
            count = Math.min(left.count, right.count);
            int v = 0;
            for (int i = 0; i < count && v + 4 <= vertices.length; i++) {
                float x = (left.x[i] + right.x[i]) * 0.5f;
                float z = zAt(path, x) * roadZGain;
                float ly = Math.max(left.y[i], right.y[i]);
                float ry = Math.min(left.y[i], right.y[i]);
                if (!project(x, ly, z, projected)) continue;
                vertices[v++] = ndcX(projected[0]);
                vertices[v++] = ndcY(projected[1] - TOP);
                if (!project(x, ry, z, projected)) {
                    v -= 2;
                    continue;
                }
                vertices[v++] = ndcX(projected[0]);
                vertices[v++] = ndcY(projected[1] - TOP);
            }
            drawVertices(GLES20.GL_TRIANGLE_STRIP, v / 2, color, 1f);
            return;
        }

        float laneWidth = clamp((float) scene.optDouble("laneWidth", 3.5d), 2.2f, 4.2f);
        JSONObject lanePosition = scene.optJSONObject("lanePosition");
        int laneCount = lanePosition == null ? 1 : Math.max(1, lanePosition.optInt("n", 1));
        float half = clamp(laneWidth * laneCount * 0.5f + 0.65f, 2.2f, 10f);
        int v = 0;
        for (int i = 0; i < path.count && v + 4 <= vertices.length; i++) {
            float z = path.z[i] * roadZGain;
            if (!project(path.x[i], path.y[i] + half, z, projected)) continue;
            vertices[v++] = ndcX(projected[0]);
            vertices[v++] = ndcY(projected[1] - TOP);
            if (!project(path.x[i], path.y[i] - half, z, projected)) {
                v -= 2;
                continue;
            }
            vertices[v++] = ndcX(projected[0]);
            vertices[v++] = ndcY(projected[1] - TOP);
        }
        drawVertices(GLES20.GL_TRIANGLE_STRIP, v / 2, color, 1f);
    }

    private void drawPath(Line path, int color) {
        int v = 0;
        for (int i = 0; i < path.count && v + 4 <= vertices.length; i++) {
            float half = 0.52f + Math.min(0.22f, path.x[i] * 0.002f);
            float z = path.z[i] * roadZGain + 0.035f;
            if (!project(path.x[i], path.y[i] + half, z, projected)) continue;
            vertices[v++] = ndcX(projected[0]);
            vertices[v++] = ndcY(projected[1] - TOP);
            if (!project(path.x[i], path.y[i] - half, z, projected)) {
                v -= 2;
                continue;
            }
            vertices[v++] = ndcX(projected[0]);
            vertices[v++] = ndcY(projected[1] - TOP);
        }
        drawVertices(GLES20.GL_TRIANGLE_STRIP, v / 2, color, 0.88f);
    }

    private void drawWorldLine(Line line, Line roadHeight, int color,
                               float widthPx, float alpha) {
        if (line == null || line.count < 2) {
            return;
        }
        float[] sx = new float[MAX_POINTS];
        float[] sy = new float[MAX_POINTS];
        int count = 0;
        for (int i = 0; i < line.count && count < MAX_POINTS; i++) {
            float z = zAt(roadHeight, line.x[i]) * roadZGain + 0.045f;
            if (project(line.x[i], line.y[i], z, projected)) {
                sx[count] = projected[0];
                sy[count] = projected[1] - TOP;
                count++;
            }
        }
        int v = 0;
        for (int i = 0; i + 1 < count && v + 12 <= vertices.length; i++) {
            float dx = sx[i + 1] - sx[i];
            float dy = sy[i + 1] - sy[i];
            float length = (float) Math.sqrt(dx * dx + dy * dy);
            if (length < 0.01f) continue;
            float nx = -dy / length * widthPx * 0.5f;
            float ny = dx / length * widthPx * 0.5f;
            v = addTriangle(v, sx[i] + nx, sy[i] + ny,
                    sx[i] - nx, sy[i] - ny, sx[i + 1] + nx, sy[i + 1] + ny);
            v = addTriangle(v, sx[i + 1] + nx, sy[i + 1] + ny,
                    sx[i] - nx, sy[i] - ny, sx[i + 1] - nx, sy[i + 1] - ny);
        }
        drawVertices(GLES20.GL_TRIANGLES, v / 2, color, alpha);
    }

    private int addTriangle(int index, float ax, float ay, float bx, float by,
                            float cx, float cy) {
        vertices[index++] = ndcX(ax);
        vertices[index++] = ndcY(ay);
        vertices[index++] = ndcX(bx);
        vertices[index++] = ndcY(by);
        vertices[index++] = ndcX(cx);
        vertices[index++] = ndcY(cy);
        return index;
    }

    private void drawRect(float left, float top, float right, float bottom, int color) {
        int v = 0;
        v = addTriangle(v, left, top, left, bottom, right, top);
        v = addTriangle(v, right, top, left, bottom, right, bottom);
        drawVertices(GLES20.GL_TRIANGLES, v / 2, color, 1f);
    }

    private void drawVertices(int mode, int count, int color, float alpha) {
        if (count < 3) {
            return;
        }
        vertexBuffer.clear();
        vertexBuffer.put(vertices, 0, count * 2);
        vertexBuffer.position(0);
        GLES20.glUseProgram(program);
        GLES20.glVertexAttribPointer(positionHandle, 2, GLES20.GL_FLOAT,
                false, 0, vertexBuffer);
        GLES20.glUniform4f(colorHandle, Color.red(color) / 255f,
                Color.green(color) / 255f, Color.blue(color) / 255f,
                clamp(alpha, 0f, 1f));
        GLES20.glDrawArrays(mode, 0, count);
    }

    private boolean project(float x, float y, float z, float[] out) {
        float depth = x + CAM_BACK;
        if (!Float.isFinite(depth) || depth < NEAR_DEPTH) {
            return false;
        }
        float scale = FOCAL / depth;
        out[0] = CX - y * scale;
        out[1] = HORIZON + horizonShift + (CAM_H - z) * scale;
        return Float.isFinite(out[0]) && Float.isFinite(out[1]);
    }

    private static Line decode(JSONArray points, float confidence) {
        Line line = new Line();
        line.confidence = confidence;
        if (points == null) {
            return line;
        }
        int limit = Math.min(points.length(), MAX_POINTS);
        for (int i = 0; i < limit; i++) {
            JSONArray point = points.optJSONArray(i);
            if (point == null || point.length() < 2) {
                continue;
            }
            double x = point.optDouble(0, Double.NaN);
            double y = point.optDouble(1, Double.NaN);
            double z = point.length() >= 3 ? point.optDouble(2, 0d) : 0d;
            if (!Double.isFinite(x) || !Double.isFinite(y) || !Double.isFinite(z)) {
                continue;
            }
            int n = line.count++;
            line.x[n] = (float) x;
            line.y[n] = (float) y;
            line.z[n] = (float) z;
        }
        return line;
    }

    private static float zAt(Line path, float x) {
        if (path == null || path.count == 0) {
            return 0f;
        }
        if (x <= path.x[0]) {
            return path.z[0];
        }
        for (int i = 1; i < path.count; i++) {
            if (x <= path.x[i]) {
                float span = path.x[i] - path.x[i - 1];
                float t = span > 0.001f ? (x - path.x[i - 1]) / span : 0f;
                return path.z[i - 1] + (path.z[i] - path.z[i - 1]) * t;
            }
        }
        return path.z[path.count - 1];
    }

    private void copyPixels() {
        readBuffer.position(0);
        GLES20.glReadPixels(0, 0, WIDTH, HEIGHT, GLES20.GL_RGBA,
                GLES20.GL_UNSIGNED_BYTE, readBuffer);
        for (int y = 0; y < HEIGHT; y++) {
            int source = (HEIGHT - 1 - y) * WIDTH;
            int target = y * WIDTH;
            for (int x = 0; x < WIDTH; x++) {
                int abgr = readBuffer.get(source + x);
                pixels[target + x] = (abgr & 0xff00ff00)
                        | ((abgr & 0x000000ff) << 16)
                        | ((abgr & 0x00ff0000) >>> 16);
            }
        }
        frame.setPixels(pixels, 0, WIDTH, 0, 0, WIDTH, HEIGHT);
    }

    private static float ndcX(float pixel) {
        return pixel * 2f / WIDTH - 1f;
    }

    private static float ndcY(float pixel) {
        return 1f - pixel * 2f / HEIGHT;
    }

    private static float clamp(float value, float minimum, float maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }

    private static int blend(int a, int b, float t) {
        t = clamp(t, 0f, 1f);
        return Color.rgb(
                (int) (Color.red(a) + (Color.red(b) - Color.red(a)) * t),
                (int) (Color.green(a) + (Color.green(b) - Color.green(a)) * t),
                (int) (Color.blue(a) + (Color.blue(b) - Color.blue(a)) * t));
    }

    private void fail(String message) {
        failed = true;
        Log.e(TAG, message + " EGL error=0x" + Integer.toHexString(EGL14.eglGetError()));
        release();
    }

    void release() {
        if (display != EGL14.EGL_NO_DISPLAY) {
            EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE,
                    EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
            if (surface != EGL14.EGL_NO_SURFACE) {
                EGL14.eglDestroySurface(display, surface);
            }
            if (context != EGL14.EGL_NO_CONTEXT) {
                EGL14.eglDestroyContext(display, context);
            }
            EGL14.eglTerminate(display);
        }
        display = EGL14.EGL_NO_DISPLAY;
        surface = EGL14.EGL_NO_SURFACE;
        context = EGL14.EGL_NO_CONTEXT;
        program = 0;
    }
}
