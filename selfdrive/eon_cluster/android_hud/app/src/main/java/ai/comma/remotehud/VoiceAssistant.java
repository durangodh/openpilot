package ai.comma.remotehud;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
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

    /** Current cruise gap step (1..4) from the latest EON packet, 0 when unknown. */
    volatile java.util.function.IntSupplier gapReader = () -> 0;

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

    /** Called for every EON packet: seq -1 when no fresh voice press is in the packet. */
    void onTelemetry(int seq, String mode) {
        int previous = lastSeq;
        if (seq < 0) {
            if (previous == -2) lastSeq = -1;
            return;
        }
        if (seq == previous) return;
        lastSeq = seq;
        if (previous == -2) return;   // 앱 시작 직전의 누름은 무시
        if ("nav".equals(mode)) {
            onNavButton();
        } else {
            onButton();
        }
    }

    static String navPackage(int navApp) {
        if (navApp == 2) return "com.nhn.android.nmap";
        if (navApp == 3) return "com.locnall.KimGiSa";
        return "com.skt.tmap.ku";
    }

    /** Remote "내비 음성": press the selected navigation app's own voice button, like nMirror. */
    void onNavButton() {
        synchronized (this) {
            if (state == State.LISTENING || state == State.THINKING) {
                session++;
                cancelRecognition();
            }
            pending = null;
        }
        new Thread(() -> {
            String error = openNavVoice();
            if (error == null) {
                show(State.RESULT, "내비 음성", "목적지를 말씀하세요", 2_500L);
            } else {
                show(State.ERROR, "내비 음성 실패", error, ERROR_MS);
            }
        }, "hud-nav-voice").start();
    }

    /** null on success, otherwise a short reason for the card. */
    String openNavVoice() {
        int navApp = AppPrefs.getNavApp(context);
        if (navApp < 1 || navApp > 3) return "선택된 내비 없음";
        if (!NavVoiceService.connected()) {
            NavVoiceService.ensureEnabled(context);
            SystemClock.sleep(800L);
        }
        return NavVoiceService.clickVoice(navPackage(navApp));
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
            cancelRecognition();
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

    private static volatile boolean micOpsGranted;
    private SpeechRecognizer recognizer;   // main thread only

    /**
     * Listens inside the HUD service so nothing is drawn on the phone screen: nMirror
     * mirrors display 0 to the car, and a window there would pause the navigation map.
     * Root grants the microphone app-op as "allow" (not just while in use) for that.
     * If the recognizer still cannot open the microphone, the transparent VoiceActivity
     * is the fallback (the map may pause for those few seconds).
     */
    private void launchListener() {
        final int mySession = session;
        if (!micOpsGranted) {
            String pkg = context.getPackageName();
            try {
                Runtime.getRuntime().exec(new String[]{"su", "-c",
                        "pm grant " + pkg + " android.permission.RECORD_AUDIO; "
                                + "appops set " + pkg + " RECORD_AUDIO allow"}).waitFor();
                micOpsGranted = true;
            } catch (Exception ignored) {
            }
        }
        main.post(() -> startServiceRecognizer(mySession));
    }

    private void startServiceRecognizer(int mySession) {
        if (mySession != session || state != State.LISTENING) return;
        destroyRecognizer();
        try {
            if (!SpeechRecognizer.isRecognitionAvailable(context)) {
                show(State.ERROR, "음성 인식 서비스 없음", "Google 앱을 확인하세요", ERROR_MS);
                return;
            }
            recognizer = SpeechRecognizer.createSpeechRecognizer(context);
            recognizer.setRecognitionListener(new ServiceListener(mySession));
            recognizer.startListening(recognizerIntent(context));
        } catch (Exception e) {
            Log.w(TAG, "service recognizer failed", e);
            launchActivityFallback();
        }
    }

    static Intent recognizerIntent(Context context) {
        return new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ko-KR")
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
                .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.getPackageName());
    }

    private void destroyRecognizer() {
        if (recognizer != null) {
            try {
                recognizer.cancel();
                recognizer.destroy();
            } catch (Exception ignored) {
            }
            recognizer = null;
        }
    }

    private void cancelRecognition() {
        main.post(this::destroyRecognizer);
        VoiceActivity.cancelActive();
    }

    private void launchActivityFallback() {
        main.post(this::destroyRecognizer);
        new Thread(() -> {
            try {
                int code = Runtime.getRuntime().exec(new String[]{"su", "-c",
                        "am start -n " + context.getPackageName() + "/.VoiceActivity"}).waitFor();
                if (code != 0) throw new IllegalStateException("am start " + code);
            } catch (Exception e) {
                show(State.ERROR, "음성 인식 실행 실패", String.valueOf(e.getMessage()), ERROR_MS);
            }
        }, "hud-voice-fallback").start();
    }

    private final class ServiceListener implements RecognitionListener {
        private final int mySession;
        private boolean ready;

        ServiceListener(int mySession) {
            this.mySession = mySession;
        }

        private boolean current() {
            return mySession == session;
        }

        @Override
        public void onReadyForSpeech(Bundle params) {
            ready = true;
            if (current()) onReady();
        }

        @Override
        public void onPartialResults(Bundle partial) {
            if (current()) onPartial(VoiceActivity.first(partial));
        }

        @Override
        public void onResults(Bundle results) {
            destroyRecognizer();
            if (current()) onSpeech(VoiceActivity.first(results));
        }

        @Override
        public void onError(int error) {
            destroyRecognizer();
            if (!current()) return;
            boolean micBlocked = error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS
                    || error == SpeechRecognizer.ERROR_AUDIO || error == SpeechRecognizer.ERROR_CLIENT;
            if (!ready && micBlocked) {
                Log.w(TAG, "background microphone blocked (" + error + "), using VoiceActivity");
                launchActivityFallback();
            } else {
                onSpeechError(VoiceActivity.errorText(error));
            }
        }

        @Override public void onBeginningOfSpeech() { }
        @Override public void onRmsChanged(float rmsdB) { }
        @Override public void onBufferReceived(byte[] buffer) { }
        @Override public void onEndOfSpeech() { }
        @Override public void onEvent(int eventType, Bundle params) { }
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
            + "'10 내려', '5 올려'처럼 변화량을 말한 경우도 운전자가 정한 값이니 get_setting 으로 현재값을 읽고 "
            + "더하거나 뺀 값을 user_stated_value=true 로 바로 설정한다. "
            + "운전자가 '부드럽게' 같은 말만 했고 네가 값을 정했으면 user_stated_value=false 로 한 번만 제안한다. "
            + "그러면 앱이 운전자 확인을 받으니, 버튼을 누르면 적용된다고 짧게 말한다. "
            + "후보가 여러 개면 가장 맞는 것을 고르고, 애매하면 상위 2~3개 제목을 짧게 말해 다시 묻는다. "
            + "차간거리 몇 칸·단계 요청은 cruise_gap 을 쓴다(TFollowGap 설정이 아님). "
            + "'한 칸 늘려'는 현재 단계+1, '줄여'는 -1. 결과는 '차간 2→3단'처럼 말한다. "
            + "길안내·목적지·경유지 요청(예: 집으로 가자)은 open_nav_voice 로 내비 음성창을 열고 "
            + "'내비에 목적지를 말씀하세요'라고만 답한다. "
            + "날씨·뉴스 같은 일반 질문은 web_search 를 쓴다. 차량 조작(조향·가감속 직접 명령)은 하지 않는다.";

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
        t.put(new JSONObject().put("name", "cruise_gap")
                .put("description", "크루즈 차간 단계(1~4칸)를 목표 단계로 맞춘다. 차간 단계는 설정값이 아니라 GAP 버튼 상태이므로 "
                        + "'차간거리 한 칸 늘려줘' 같은 요청에 쓴다. 현재 단계는 current_gap 으로 먼저 확인할 수 있다.")
                .put("input_schema", new JSONObject().put("type", "object")
                        .put("properties", new JSONObject()
                                .put("target", new JSONObject().put("type", "integer")
                                        .put("description", "목표 단계 1~4. 0 이면 현재 단계만 읽는다")))
                        .put("required", new JSONArray().put("target"))));
        t.put(new JSONObject().put("name", "open_nav_voice")
                .put("description", "선택된 내비(티맵·네이버·카카오)의 자체 음성 검색창을 연다. 길안내·목적지·경유지 요청일 때 쓴다. "
                        + "열린 뒤 운전자가 내비에 목적지를 다시 말해야 한다.")
                .put("input_schema", new JSONObject().put("type", "object").put("properties", new JSONObject())));
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
        if ("open_nav_voice".equals(name)) return "내비 음성창 여는 중";
        if ("cruise_gap".equals(name)) return "차간 단계 맞추는 중";
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
        } else if ("cruise_gap".equals(name)) {
            reply = cruiseGap(input.optInt("target", 0));
        } else if ("open_nav_voice".equals(name)) {
            String error = openNavVoice();
            reply = error == null
                    ? new JSONObject().put("ok", true).put("note", "내비 음성창이 열림. 운전자에게 목적지를 내비에 말하라고 안내")
                    : new JSONObject().put("ok", false).put("error", error);
        } else {
            reply = new JSONObject().put("ok", false).put("error", "알 수 없는 도구");
        }
        return reply.toString();
    }

    /**
     * Presses GAP until the HUD packet shows the target step (max 4 presses), reading the
     * real step back after each press because the car decides the cycle order.
     */
    private JSONObject cruiseGap(int target) throws JSONException {
        int current = gapReader.getAsInt();
        if (current < 1 || current > 4) {
            return new JSONObject().put("ok", false).put("error", "현재 차간 단계를 알 수 없음(시동·크루즈 확인)");
        }
        if (target == 0 || target == current) {
            return new JSONObject().put("ok", true).put("gap", current);
        }
        if (target < 1 || target > 4) {
            return new JSONObject().put("ok", false).put("error", "차간 단계는 1~4").put("gap", current);
        }
        int before = current;
        for (int press = 0; press < 4; press++) {
            JSONObject r = askEon(new JSONObject().put("op", "press").put("cmd", "gap"));
            if (!r.optBoolean("ok")) {
                return r.put("gap", gapReader.getAsInt()).put("before", before);
            }
            // 버튼 → 차량 → 다음 패킷까지 기다렸다가 실제 단계를 다시 읽는다.
            int seen = current;
            for (int wait = 0; wait < 10 && seen == current; wait++) {
                SystemClock.sleep(120L);
                seen = gapReader.getAsInt();
            }
            current = seen;
            if (current == target) {
                return new JSONObject().put("ok", true).put("before", before).put("after", current);
            }
        }
        return new JSONObject().put("ok", false).put("error", "목표 단계에 도달하지 못함")
                .put("before", before).put("after", current);
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

    /**
     * 내비 안내 음성과 같은 용도(USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)로 낸다. 안드로이드오토는
     * 이 소리를 안내 채널로 보내 라디오·음악 중에도 차량 스피커에서 (배경음을 줄이고) 들린다.
     */
    private static final AudioAttributes SPEECH = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build();
    private AudioFocusRequest focus;

    private void speak(final String text) {
        if (!AppPrefs.isVoiceTtsEnabled(context) || text == null || text.isEmpty()) return;
        main.post(() -> {
            if (tts == null) {
                tts = new TextToSpeech(context, status -> {
                    ttsReady = status == TextToSpeech.SUCCESS;
                    if (ttsReady) {
                        tts.setLanguage(Locale.KOREAN);
                        tts.setAudioAttributes(SPEECH);
                        tts.setOnUtteranceProgressListener(new android.speech.tts.UtteranceProgressListener() {
                            @Override public void onStart(String id) { }
                            @Override public void onDone(String id) { main.post(VoiceAssistant.this::releaseFocus); }
                            @Override public void onError(String id) { main.post(VoiceAssistant.this::releaseFocus); }
                        });
                        say(text);
                    }
                });
            } else if (ttsReady) {
                say(text);
            }
        });
    }

    private void say(String text) {
        AudioManager audio = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        if (audio != null && focus == null) {
            focus = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                    .setAudioAttributes(SPEECH).build();
            audio.requestAudioFocus(focus);
        }
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "hud-voice");
    }

    private void releaseFocus() {
        AudioManager audio = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        if (audio != null && focus != null) audio.abandonAudioFocusRequest(focus);
        focus = null;
    }

    void shutdown() {
        session++;
        cancelRecognition();
        main.post(() -> {
            releaseFocus();
            if (tts != null) tts.shutdown();
            tts = null;
            ttsReady = false;
        });
        if (instance == this) instance = null;
    }

    // ── HUD 카드 ──────────────────────────────────────────────────────

    /** True while a card should replace the HUD info box. */
    boolean isActive() {
        State s = state;
        if (s == State.IDLE) return false;
        if (SystemClock.elapsedRealtime() <= shownUntil) return true;
        if (s == State.LISTENING) cancelRecognition();
        if (s == State.CONFIRM) {
            synchronized (this) {
                pending = null;
            }
        }
        state = State.IDLE;
        return false;
    }

    /**
     * Draws the card filling the HUD info box (local box coordinates, 0..w x 0..h).
     * Returns false when idle so the box shows its normal content.
     */
    boolean drawInBox(Canvas c, float w, float h) {
        if (!isActive()) return false;
        State s = state;
        long now = SystemClock.elapsedRealtime();
        int accent;
        switch (s) {
            case LISTENING: accent = Color.rgb(64, 156, 255); break;
            case THINKING: accent = Color.rgb(170, 180, 190); break;
            case RESULT: accent = Color.rgb(60, 200, 120); break;
            case CONFIRM: accent = Color.rgb(255, 184, 48); break;
            default: accent = Color.rgb(235, 80, 80); break;
        }
        Paint p = paint;
        p.setShader(null);
        // 정보 박스와 같은 불투명 어두운 카드(낮에도 글자가 보이게).
        box.set(0f, 0f, w, h);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.argb(240, 16, 22, 40));
        c.drawRoundRect(box, 10f, 10f, p);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(5f);
        p.setColor(accent);
        box.inset(2.5f, 2.5f);
        c.drawRoundRect(box, 10f, 10f, p);

        // 상태 점(준비·듣는 중·확인 중은 깜빡임)
        boolean blink = (s == State.LISTENING || s == State.THINKING) && ((now - stateSince) / 450L) % 2 == 1;
        p.setStyle(Paint.Style.FILL);
        p.setColor(blink ? Color.argb(80, Color.red(accent), Color.green(accent), Color.blue(accent)) : accent);
        c.drawCircle(34f, 44f, 14f, p);

        p.setTypeface(Typeface.create("sans", Typeface.BOLD));
        p.setTextSize(42f);
        p.setTextAlign(Paint.Align.LEFT);
        p.setColor(accent);
        String head = title;
        if (s == State.CONFIRM) {
            head += "  " + Math.max(0L, (shownUntil - now + 999L) / 1000L) + "초";
        }
        c.drawText(head, 60f, 58f, p);

        // 정보 박스는 HUD 에서 작게 보여(약 0.57배) 글씨를 크게 쓴다(2026-11-07 실차 사진 기준).
        p.setTypeface(Typeface.create("sans", Typeface.BOLD));
        p.setTextSize(44f);
        p.setColor(Color.WHITE);
        float lineH = 58f;
        int maxLines = Math.max(1, (int) ((h - 118f - 16f) / lineH) + 1);
        List<String> lines = wrap(body, w - 40f, p, maxLines);
        float y = 124f;
        for (String line : lines) {
            c.drawText(line, 20f, y, p);
            y += lineH;
        }
        return true;
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
