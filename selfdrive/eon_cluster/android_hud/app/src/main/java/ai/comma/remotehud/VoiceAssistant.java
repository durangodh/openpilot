package ai.comma.remotehud;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.speech.tts.TextToSpeech;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Remote-button voice assistant (2026-11-07).
 *
 * EON bt_remote "voice" press -> packet voiceReq -> VoiceActivity (speech, ko-KR) ->
 * Claude Messages API with three setting tools (answered by EON over HUDPRM1) and the
 * web search server tool -> a short card over the HUD map area.
 *
 * Safety: EON range-checks every write and blocks non-cruise/longitudinal settings while
 * moving. A value the AI proposes (not spoken by the driver) is only stored as pending and
 * applied when the driver presses the same remote button again within CONFIRM_MS.
 */
public final class VoiceAssistant {
    private static final String TAG = "HudVoice";
    private static final String MODEL = "claude-haiku-5-5";
    private static final String API_URL = "https://api.anthropic.com/v1/messages";
    private static final int MAX_ROUNDS = 6;
    private static final long CONTEXT_KEEP_MS = 60_000L;
    private static final long CONFIRM_MS = 12_000L;
    private static final long RESULT_MS = 7_000L;
    private static final long ERROR_MS = 5_000L;
    private static final long LISTEN_TIMEOUT_MS = 15_000L;
    private static final long EON_TIMEOUT_MS = 1_500L;

    enum State { IDLE, LISTENING, THINKING, RESULT, CONFIRM, ERROR }

    /** Sends one datagram to EON; returns false when EON is unknown. */
    interface EonLink {
        boolean send(byte[] data);
    }

    private static volatile VoiceAssistant instance;

    private final Context context;
    private final EonLink eon;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<String, JSONObject> eonReplies = new HashMap<>();
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF box = new RectF();

    private volatile State state = State.IDLE;
    private volatile String title = "";
    private volatile String body = "";
    private volatile long shownUntil;
    private volatile long stateSince;
    private int lastSeq = -2;
    private volatile int session;

    private JSONArray history = new JSONArray();
    private long historyAt;
    private JSONObject pending;   // {name,title,value}
    private long pendingAt;
    private TextToSpeech tts;
    private boolean ttsReady;

    VoiceAssistant(Context context, EonLink eon) {
        this.context = context.getApplicationContext();
        this.eon = eon;
        instance = this;
    }

    static VoiceAssistant get() {
        return instance;
    }

    // ── 트리거 ──────────────────────────────────────────────────────────

    /** Called for every EON packet: -1 when no fresh voice press is in the packet. */
    void onTelemetry(int seq) {
        int previous = lastSeq;
        if (seq < 0) {
            if (previous == -2) lastSeq = -1;
            return;
        }
        if (seq == previous) return;
        lastSeq = seq;
        if (previous == -2) return;   // 앱 시작 직전의 누름은 무시
        onButton();
    }

    /** Remote button: confirm a pending proposal, cancel a running request, or start listening. */
    synchronized void onButton() {
        long now = SystemClock.elapsedRealtime();
        if (state == State.CONFIRM && pending != null && now - pendingAt <= CONFIRM_MS) {
            final JSONObject p = pending;
            pending = null;
            final int mySession = ++session;
            show(State.THINKING, "적용 중", p.optString("title", p.optString("name")), 0);
            new Thread(() -> applyPending(p, mySession), "hud-voice-apply").start();
            return;
        }
        if (state == State.LISTENING || state == State.THINKING) {
            session++;
            VoiceActivity.cancelActive();
            show(State.ERROR, "취소됨", "", 1_500L);
            return;
        }
        pending = null;
        if (AppPrefs.getClaudeApiKey(context).isEmpty()) {
            show(State.ERROR, "AI 키 없음", "HUD 앱 설정에서 Claude API 키를 입력하세요", ERROR_MS);
            return;
        }
        session++;
        // 마이크가 실제로 열리기 전(투명 창 실행·인식기 준비)에는 "준비 중"으로 둔다.
        show(State.LISTENING, "준비 중", "잠시만요", LISTEN_TIMEOUT_MS);
        new Thread(this::launchListener, "hud-voice-launch").start();
    }

    private void launchListener() {
        try {
            if (context.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                    != PackageManager.PERMISSION_GRANTED) {
                Runtime.getRuntime().exec(new String[]{"su", "-c",
                        "pm grant " + context.getPackageName() + " android.permission.RECORD_AUDIO"}).waitFor();
            }
            // 서비스는 백그라운드라 액티비티를 직접 못 띄운다. 기존 내비 전환처럼 루트로 띄운다.
            int code = Runtime.getRuntime().exec(new String[]{"su", "-c",
                    "am start -n " + context.getPackageName() + "/.VoiceActivity"}).waitFor();
            if (code != 0) throw new IllegalStateException("am start " + code);
        } catch (Exception e) {
            try {
                Intent intent = new Intent(context, VoiceActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION);
                context.startActivity(intent);
            } catch (Exception second) {
                show(State.ERROR, "음성 인식 실행 실패", String.valueOf(second.getMessage()), ERROR_MS);
            }
        }
    }

    // ── VoiceActivity 콜백 ─────────────────────────────────────────────

    /** 인식기가 소리를 받기 시작함: 이때부터 말하면 된다. */
    void onReady() {
        if (state == State.LISTENING) {
            title = "듣는 중";
            body = "말씀하세요";
        }
    }

    void onPartial(String text) {
        if (state == State.LISTENING && text != null && !text.isEmpty()) {
            body = "\"" + text + "\"";
        }
    }

    void onSpeech(String text) {
        if (state != State.LISTENING) return;
        if (text == null || text.trim().isEmpty()) {
            show(State.ERROR, "못 알아들었어요", "버튼을 다시 누르고 말씀하세요", ERROR_MS);
            return;
        }
        final String said = text.trim();
        final int mySession = session;
        show(State.THINKING, "확인 중", "\"" + said + "\"", 0);
        new Thread(() -> converse(said, mySession), "hud-voice-ai").start();
    }

    void onSpeechError(String message) {
        if (state == State.LISTENING) show(State.ERROR, "음성 인식 실패", message, ERROR_MS);
    }

    // ── EON (HUDPRM1) ─────────────────────────────────────────────────

    /** Called from the UDP receive loop for a datagram that starts with HUDPRM1R. */
    void onEonReply(byte[] data, int offset, int length) {
        try {
            String raw = new String(data, offset, length, StandardCharsets.UTF_8);
            JSONObject reply = new JSONObject(raw.substring("HUDPRM1R ".length()));
            String id = reply.optString("id", "");
            synchronized (eonReplies) {
                eonReplies.put(id, reply);
                eonReplies.notifyAll();
            }
        } catch (Exception e) {
            Log.w(TAG, "bad EON reply", e);
        }
    }

    private JSONObject askEon(JSONObject request) throws JSONException {
        String id = UUID.randomUUID().toString().substring(0, 12);
        request.put("id", id);
        byte[] data = ("HUDPRM1 " + request).getBytes(StandardCharsets.UTF_8);
        long deadline = SystemClock.elapsedRealtime() + EON_TIMEOUT_MS * 2;
        long nextSend = 0;
        synchronized (eonReplies) {
            while (true) {
                long now = SystemClock.elapsedRealtime();
                JSONObject reply = eonReplies.remove(id);
                if (reply != null) return reply;
                if (now >= deadline) break;
                if (now >= nextSend) {
                    if (!eon.send(data)) break;
                    nextSend = now + 400L;   // UDP 손실 대비 재전송(요청은 멱등)
                }
                try {
                    eonReplies.wait(Math.max(10L, Math.min(nextSend, deadline) - now));
                } catch (InterruptedException e) {
                    break;
                }
            }
        }
        return new JSONObject().put("ok", false).put("error", "EON 응답 없음(연결 확인)");
    }

    private void applyPending(JSONObject p, int mySession) {
        try {
            JSONObject r = askEon(new JSONObject().put("op", "set")
                    .put("name", p.optString("name")).put("value", p.optInt("value")));
            if (mySession != session) return;
            if (r.optBoolean("ok")) {
                finish(State.RESULT, "적용됨", p.optString("title", p.optString("name"))
                        + "  " + r.optInt("before") + " → " + r.optInt("after"));
            } else {
                finish(State.ERROR, "적용 안 됨", r.optString("error"));
            }
        } catch (JSONException e) {
            finish(State.ERROR, "적용 실패", e.getMessage());
        }
    }

    // ── Claude ────────────────────────────────────────────────────────

    private static final String SYSTEM = "너는 제네시스 DH 차량의 오픈파일럿(EON) 외부 HUD 음성 비서다. "
            + "운전 중이므로 최종 답은 한국어 평문 2문장, 70자 안쪽으로 짧게. 마크다운·목록·이모지·출처 표기 금지. "
            + "오픈파일럿 설정 질문이면 search_settings 로 찾는다. 설정 제목은 영어, 설명은 한국어이므로 "
            + "한국어 키워드와 영어 키워드(예: 차간→follow, gap / 가속→accel / 정지→stop)를 함께 넣는다. "
            + "값을 알려줄 때는 현재값과 범위를 말한다. "
            + "운전자가 숫자를 직접 말했으면 set_setting 에 user_stated_value=true. "
            + "운전자가 '부드럽게' 같은 말만 했고 네가 값을 정했으면 user_stated_value=false 로 한 번만 제안한다. "
            + "그러면 앱이 운전자 확인을 받으니, 버튼을 누르면 적용된다고 짧게 말한다. "
            + "후보가 여러 개면 가장 맞는 것을 고르고, 애매하면 상위 2~3개 제목을 짧게 말해 다시 묻는다. "
            + "날씨·뉴스·장소 같은 일반 질문은 web_search 를 쓴다. 차량 조작(조향·가감속 직접 명령)은 하지 않는다.";

    private static JSONArray tools() throws JSONException {
        JSONArray t = new JSONArray();
        t.put(new JSONObject().put("name", "search_settings")
                .put("description", "오픈파일럿 설정(APM 목록)을 키워드로 검색. 이름·제목·설명·현재값·범위를 돌려준다.")
                .put("input_schema", new JSONObject().put("type", "object")
                        .put("properties", new JSONObject().put("keywords", new JSONObject()
                                .put("type", "array").put("items", new JSONObject().put("type", "string"))
                                .put("description", "한국어와 영어 키워드 2~6개")))
                        .put("required", new JSONArray().put("keywords"))));
        t.put(new JSONObject().put("name", "get_setting")
                .put("description", "설정 하나의 현재값·범위·기본값·설명.")
                .put("input_schema", new JSONObject().put("type", "object")
                        .put("properties", new JSONObject().put("name", new JSONObject().put("type", "string")))
                        .put("required", new JSONArray().put("name"))));
        t.put(new JSONObject().put("name", "set_setting")
                .put("description", "설정값 변경. EON 이 범위를 검사하고 주행 중에는 크루즈·종방향 그룹만 바꾼다.")
                .put("input_schema", new JSONObject().put("type", "object")
                        .put("properties", new JSONObject()
                                .put("name", new JSONObject().put("type", "string"))
                                .put("value", new JSONObject().put("type", "integer"))
                                .put("user_stated_value", new JSONObject().put("type", "boolean")
                                        .put("description", "운전자가 이 숫자를 직접 말했으면 true")))
                        .put("required", new JSONArray().put("name").put("value").put("user_stated_value"))));
        t.put(new JSONObject().put("type", "web_search_20250305").put("name", "web_search").put("max_uses", 2)
                .put("user_location", new JSONObject().put("type", "approximate")
                        .put("country", "KR").put("timezone", "Asia/Seoul")));
        return t;
    }

    private void converse(String said, int mySession) {
        try {
            JSONArray messages;
            synchronized (this) {
                long now = SystemClock.elapsedRealtime();
                messages = now - historyAt <= CONTEXT_KEEP_MS ? history : new JSONArray();
            }
            messages.put(new JSONObject().put("role", "user").put("content", said));
            JSONObject proposal = null;
            String answer = "";
            for (int round = 0; round < MAX_ROUNDS; round++) {
                if (mySession != session) return;
                JSONObject response = callClaude(messages);
                JSONArray content = response.optJSONArray("content");
                if (content == null) content = new JSONArray();
                messages.put(new JSONObject().put("role", "assistant").put("content", content));
                String stop = response.optString("stop_reason");
                StringBuilder text = new StringBuilder();
                JSONArray results = new JSONArray();
                for (int i = 0; i < content.length(); i++) {
                    JSONObject block = content.getJSONObject(i);
                    String type = block.optString("type");
                    if ("text".equals(type)) {
                        text.append(block.optString("text"));
                    } else if ("tool_use".equals(type)) {
                        if (mySession == session) show(State.THINKING, "확인 중", toolLabel(block), 0);
                        JSONObject[] proposed = new JSONObject[1];
                        String result = runTool(block, proposed);
                        if (proposed[0] != null) proposal = proposed[0];
                        results.put(new JSONObject().put("type", "tool_result")
                                .put("tool_use_id", block.optString("id")).put("content", result));
                    } else if ("server_tool_use".equals(type) && mySession == session) {
                        show(State.THINKING, "검색 중", block.optJSONObject("input") == null ? ""
                                : block.optJSONObject("input").optString("query"), 0);
                    }
                }
                answer = text.toString().trim();
                if ("tool_use".equals(stop) && results.length() > 0) {
                    messages.put(new JSONObject().put("role", "user").put("content", results));
                    continue;
                }
                if ("pause_turn".equals(stop)) continue;   // 서버 검색이 길어진 경우 그대로 이어서 보낸다
                break;
            }
            synchronized (this) {
                history = messages;
                historyAt = SystemClock.elapsedRealtime();
            }
            if (mySession != session) return;
            if (answer.isEmpty()) answer = "답을 만들지 못했어요";
            if (proposal != null) {
                synchronized (this) {
                    pending = proposal;
                    pendingAt = SystemClock.elapsedRealtime();
                }
                finish(State.CONFIRM, "적용할까요?  버튼 = 적용", answer);
            } else {
                finish(State.RESULT, "AI", answer);
            }
        } catch (ApiError e) {
            if (mySession == session) finish(State.ERROR, "AI 오류", e.getMessage());
        } catch (Exception e) {
            Log.w(TAG, "converse failed", e);
            if (mySession == session) finish(State.ERROR, "AI 연결 실패", "인터넷(LTE) 연결을 확인하세요");
        }
    }

    private static String toolLabel(JSONObject block) {
        String name = block.optString("name");
        if ("search_settings".equals(name)) return "설정 찾는 중";
        if ("get_setting".equals(name)) return "현재값 읽는 중";
        if ("set_setting".equals(name)) return "설정 바꾸는 중";
        return name;
    }

    private String runTool(JSONObject block, JSONObject[] proposed) throws JSONException {
        JSONObject input = block.optJSONObject("input");
        if (input == null) input = new JSONObject();
        String name = block.optString("name");
        JSONObject reply;
        if ("search_settings".equals(name)) {
            reply = askEon(new JSONObject().put("op", "search")
                    .put("keywords", input.optJSONArray("keywords") == null ? new JSONArray()
                            : input.optJSONArray("keywords")));
        } else if ("get_setting".equals(name)) {
            reply = askEon(new JSONObject().put("op", "get").put("name", input.optString("name")));
        } else if ("set_setting".equals(name)) {
            if (!input.optBoolean("user_stated_value", false)) {
                // 운전자가 말하지 않은 값: 먼저 이름·범위를 확인만 하고, 적용은 버튼 확인 뒤에.
                JSONObject check = askEon(new JSONObject().put("op", "get").put("name", input.optString("name")));
                if (!check.optBoolean("ok")) return check.toString();
                JSONObject s = check.optJSONObject("setting");
                int value = input.optInt("value");
                if (s != null && (value < s.optInt("min", Integer.MIN_VALUE) || value > s.optInt("max", Integer.MAX_VALUE))) {
                    return new JSONObject().put("ok", false).put("error", "범위 밖 값").put("setting", s).toString();
                }
                proposed[0] = new JSONObject().put("name", input.optString("name")).put("value", value)
                        .put("title", s == null ? input.optString("name") : s.optString("title"));
                return new JSONObject().put("ok", true).put("status", "운전자 확인 대기")
                        .put("note", "아직 적용 안 됨. 운전자가 리모컨 버튼을 다시 누르면 적용된다.")
                        .put("current", s == null ? JSONObject.NULL : s.opt("value")).toString();
            }
            reply = askEon(new JSONObject().put("op", "set").put("name", input.optString("name"))
                    .put("value", input.optInt("value")));
        } else {
            reply = new JSONObject().put("ok", false).put("error", "알 수 없는 도구");
        }
        return reply.toString();
    }

    static final class ApiError extends Exception {
        ApiError(String message) {
            super(message);
        }
    }

    private JSONObject callClaude(JSONArray messages) throws Exception {
        JSONObject body = new JSONObject()
                .put("model", MODEL)
                .put("max_tokens", 600)
                .put("system", SYSTEM)
                .put("tools", tools())
                .put("messages", messages);
        HttpURLConnection conn = (HttpURLConnection) new URL(API_URL).openConnection();
        try {
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(8_000);
            conn.setReadTimeout(25_000);
            conn.setDoOutput(true);
            conn.setRequestProperty("content-type", "application/json");
            conn.setRequestProperty("anthropic-version", "2023-06-01");
            conn.setRequestProperty("x-api-key", AppPrefs.getClaudeApiKey(context));
            try (OutputStream out = conn.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
            int code = conn.getResponseCode();
            InputStream in = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String raw = in == null ? "" : readAll(in);
            if (code == 401) throw new ApiError("API 키가 올바르지 않아요");
            if (code == 429) throw new ApiError("요청이 많아요. 잠시 후 다시");
            if (code == 529 || code >= 500) throw new ApiError("AI 서버 혼잡. 잠시 후 다시");
            if (code >= 400) {
                String message = "요청 오류 " + code;
                try {
                    message += ": " + new JSONObject(raw).getJSONObject("error").optString("message");
                } catch (JSONException ignored) {
                }
                throw new ApiError(message);
            }
            return new JSONObject(raw);
        } finally {
            conn.disconnect();
        }
    }

    private static String readAll(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int n;
        while ((n = in.read(chunk)) > 0) buffer.write(chunk, 0, n);
        in.close();
        return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
    }

    // ── 상태·음성 출력 ─────────────────────────────────────────────────

    private void show(State s, String t, String b, long durationMs) {
        state = s;
        title = t == null ? "" : t;
        body = b == null ? "" : b;
        stateSince = SystemClock.elapsedRealtime();
        shownUntil = durationMs > 0 ? stateSince + durationMs : Long.MAX_VALUE;
    }

    private void finish(State s, String t, String b) {
        show(s, t, b, s == State.CONFIRM ? CONFIRM_MS : s == State.ERROR ? ERROR_MS : RESULT_MS);
        if (s != State.ERROR) speak(b);
    }

    private void speak(final String text) {
        if (!AppPrefs.isVoiceTtsEnabled(context) || text == null || text.isEmpty()) return;
        main.post(() -> {
            if (tts == null) {
                tts = new TextToSpeech(context, status -> {
                    ttsReady = status == TextToSpeech.SUCCESS;
                    if (ttsReady) {
                        tts.setLanguage(Locale.KOREAN);
                        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "hud-voice");
                    }
                });
            } else if (ttsReady) {
                tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "hud-voice");
            }
        });
    }

    void shutdown() {
        session++;
        main.post(() -> {
            if (tts != null) tts.shutdown();
            tts = null;
            ttsReady = false;
        });
        if (instance == this) instance = null;
    }

    // ── HUD 카드 ──────────────────────────────────────────────────────

    /** Draws the card centred at cx near the top of the HUD; nothing when idle. */
    void draw(Canvas c, float cx, boolean dark) {
        State s = state;
        long now = SystemClock.elapsedRealtime();
        if (s == State.IDLE) return;
        if (now > shownUntil) {
            if (s == State.LISTENING) {
                VoiceActivity.cancelActive();
            }
            if (s == State.CONFIRM) {
                synchronized (this) {
                    pending = null;
                }
            }
            state = State.IDLE;
            return;
        }
        int accent;
        switch (s) {
            case LISTENING: accent = Color.rgb(64, 156, 255); break;
            case THINKING: accent = Color.rgb(160, 170, 180); break;
            case RESULT: accent = Color.rgb(60, 200, 120); break;
            case CONFIRM: accent = Color.rgb(255, 184, 48); break;
            default: accent = Color.rgb(235, 80, 80); break;
        }
        float width = 700f;
        Paint p = paint;
        p.setTypeface(Typeface.create("sans", Typeface.NORMAL));
        p.setTextSize(34f);
        List<String> lines = wrap(body, width - 48f, p, 3);
        float height = 74f + lines.size() * 44f + 18f;
        box.set(cx - width / 2f, 22f, cx + width / 2f, 22f + height);
        p.setShader(null);
        p.setStyle(Paint.Style.FILL);
        p.setColor(dark ? Color.argb(236, 12, 16, 22) : Color.argb(240, 248, 249, 250));
        c.drawRoundRect(box, 14f, 14f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(4f);
        p.setColor(accent);
        c.drawRoundRect(box, 14f, 14f, p);

        // 상태 점(듣는 중·확인 중은 깜빡임)
        boolean blink = (s == State.LISTENING || s == State.THINKING) && ((now - stateSince) / 450L) % 2 == 1;
        p.setStyle(Paint.Style.FILL);
        p.setColor(blink ? Color.argb(90, Color.red(accent), Color.green(accent), Color.blue(accent)) : accent);
        c.drawCircle(box.left + 34f, box.top + 42f, 11f, p);

        p.setTypeface(Typeface.create("sans", Typeface.BOLD));
        p.setTextSize(36f);
        p.setTextAlign(Paint.Align.LEFT);
        p.setColor(accent);
        String head = title;
        if (s == State.CONFIRM) {
            head += "  " + Math.max(0L, (shownUntil - now + 999L) / 1000L) + "초";
        }
        c.drawText(head, box.left + 58f, box.top + 54f, p);

        p.setTypeface(Typeface.create("sans", Typeface.NORMAL));
        p.setTextSize(34f);
        p.setColor(dark ? Color.WHITE : Color.rgb(25, 30, 34));
        float y = box.top + 54f + 48f;
        for (String line : lines) {
            c.drawText(line, box.left + 24f, y, p);
            y += 44f;
        }
    }

    /** Greedy character wrap (Korean has few spaces); the last line gets an ellipsis. */
    static List<String> wrap(String text, float maxWidth, Paint p, int maxLines) {
        List<String> out = new ArrayList<>();
        if (text == null) return out;
        String rest = text.replace('\n', ' ').trim();
        while (!rest.isEmpty() && out.size() < maxLines) {
            int n = p.breakText(rest, true, maxWidth, null);
            if (n <= 0) break;
            if (n < rest.length()) {
                int space = rest.lastIndexOf(' ', n);
                if (space > n / 2) n = space;
            }
            String line = rest.substring(0, n).trim();
            rest = rest.substring(n).trim();
            if (out.size() == maxLines - 1 && !rest.isEmpty()) {
                int cut = p.breakText(line + "…", true, maxWidth, null);
                line = (cut < line.length() + 1 ? line.substring(0, Math.max(0, cut - 1)) : line) + "…";
            }
            out.add(line);
        }
        return out;
    }
}
