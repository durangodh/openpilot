package ai.comma.kakaohud;

import android.app.Activity;
import android.content.Intent;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityNodeProvider;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/**
 * 핸들 음성 버튼 → 카카오 음성 검색.
 *
 * nMirror 는 핸들 음성 버튼을 누르면 접근성 서비스로 포커스된 네비 앱 화면에서
 * 정해진 리소스 ID(네이버 btn_speech_recognition 등)의 노드를 찾아 ACTION_CLICK 한다.
 * 카카오 마이크는 Compose 라 그런 ID 가 없다. 예전 시도(b89d48b 에서 제거)는 기기
 * 로그가 없어 어느 단계에서 실패했는지 알 수 없었으므로, 이번에는:
 *
 *  1) 진단: nMirror 가 카카오 프로세스에 보내는 접근성 요청(찾는 ID/글자, 실행한
 *     동작), 카카오가 받은 키 이벤트와 인텐트를 kakao_hud.log 에 남긴다. nMirror 가
 *     카카오에 아무것도 묻지 않는다면 그것도 로그로 드러난다.
 *  2) 연결: nMirror 가 찾는 이름이 음성 관련(speech/voice/clova/stt/mic)이면 그
 *     이름을 프록시 View 의 ID 로 응답한다(getIdentifier + viewIdResourceName).
 *  3) 실행: 프록시가 눌리면 카카오 화면의 Compose 접근성 노드 중 음성 검색 버튼
 *     (설명에 "음성" 등, "안내/볼륨/소리" 제외)을 찾아 그 노드를 클릭한다. 화면을
 *     직접 누르는 것과 같은 경로라 카카오 내부 클래스 이름에 기대지 않는다.
 */
final class KakaoVoice {
    private static final String KAKAO_PKG = "com.locnall.KimGiSa";
    private static final int PROXY_ID = 0x7f0bffff;
    private static final String[] VOICE_ID_WORDS = {"speech", "voice", "clova", "stt", "mic", "record"};
    private static final String[] VOICE_DESC_WORDS = {"음성", "마이크", "voice", "speech", "말하기"};
    private static final String[] VOICE_DESC_PREFERRED = {"검색", "명령", "인식", "말하기", "search"};
    private static final String[] VOICE_DESC_EXCLUDE = {"안내", "볼륨", "음량", "소리", "mute", "끄기", "켜기"};
    private static final int MAX_VIRTUAL_ID = 3000;
    // 2026-10-05 기기 로그: 핸들 음성 버튼을 누르면 nMirror 가 카카오 화면에서
    // adot_*_wake_up_button, nugu_*, *kakaoi*, btn_speech_recognition, v_clova_button
    // 등을 차례로 찾고, 못 찾으면 약 30초 동안 1~3초마다 다시 찾는다. 버튼을 누를
    // 때만 오는 요청이므로, 조용하다가 시작된 요청 묶음을 버튼 누름으로 본다.
    private static final String[] NMIRROR_VOICE_ID_WORDS = {
            "wake_up", "nugu", "kakaoi", "speech", "voice", "clova"};
    private static final long QUERY_BURST_GAP_MS = 8000;
    private static final long CLICK_DEBOUNCE_MS = 3000;
    private static final int LOG_LIMIT = 40;

    private final Map<String, Integer> logged = new HashMap<>();
    private volatile String proxyName = KAKAO_PKG + ":id/btn_speech_recognition";
    private volatile Activity resumed;
    private View proxy;
    private boolean candidatesLogged;
    private volatile long lastVoiceQueryMs;
    private volatile long lastClickMs;
    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());

    void install(LoadPackageParam lpparam) {
        hookAccessibilityRequests();
        hookResourceIds();
        hookActivities();
        KakaoHudLog.line("voice: diagnostics + proxy hooks installed");
    }

    /** 같은 내용은 LOG_LIMIT 번까지만 남긴다. */
    private void once(String key, String message) {
        synchronized (logged) {
            int n = logged.containsKey(key) ? logged.get(key) : 0;
            if (n >= LOG_LIMIT) return;
            logged.put(key, n + 1);
        }
        KakaoHudLog.line(message);
    }

    private static boolean voiceLike(String name) {
        if (name == null) return false;
        String n = name.toLowerCase(Locale.ROOT);
        int slash = n.lastIndexOf('/');
        if (slash >= 0) n = n.substring(slash + 1);
        for (String w : VOICE_ID_WORDS) if (n.contains(w)) return true;
        return false;
    }

    // ---- 1) nMirror 접근성 요청 기록 ----

    private void hookAccessibilityRequests() {
        try {
            Class<?> aic = XposedHelpers.findClass("android.view.AccessibilityInteractionController", null);
            int n = 0;
            n += XposedBridge.hookAllMethods(aic, "findAccessibilityNodeInfosByViewIdClientThread", new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    String id = firstString(param.args);
                    once("q:" + id, "voice: a11y find by id \"" + id + "\"" + (voiceLike(id) ? " (voice-like → proxy)" : ""));
                    if (voiceLike(id)) proxyName = id.contains(":") ? id : KAKAO_PKG + ":id/" + id;
                    onVoiceQuery(id);
                }
            }).size();
            n += XposedBridge.hookAllMethods(aic, "findAccessibilityNodeInfosByTextClientThread", new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    String text = firstString(param.args);
                    once("t:" + text, "voice: a11y find by text \"" + text + "\"");
                }
            }).size();
            n += XposedBridge.hookAllMethods(aic, "performAccessibilityActionClientThread", new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    long node = param.args.length > 0 && param.args[0] instanceof Long ? (Long) param.args[0] : -1L;
                    int action = param.args.length > 1 && param.args[1] instanceof Integer ? (Integer) param.args[1] : -1;
                    once("a:" + action, "voice: a11y perform action=" + action + " node=0x" + Long.toHexString(node));
                }
            }).size();
            n += XposedBridge.hookAllMethods(aic, "findAccessibilityNodeInfoByAccessibilityIdClientThread", new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    once("byid", "voice: a11y tree read (service is inspecting Kakao windows)");
                }
            }).size();
            KakaoHudLog.line("voice: a11y request hooks x" + n);
        } catch (Throwable t) {
            KakaoHudLog.ex("voice a11y hooks", t);
        }
    }

    /** nMirror 가 음성 버튼을 찾기 시작하면(= 핸들 음성 버튼 누름) 카카오 음성 버튼을 누른다. */
    private void onVoiceQuery(String id) {
        if (id == null) return;
        String l = id.toLowerCase(Locale.ROOT);
        boolean voice = false;
        for (String w : NMIRROR_VOICE_ID_WORDS) if (l.contains(w)) voice = true;
        if (!voice) return;
        long now = android.os.SystemClock.elapsedRealtime();
        boolean burstStart = now - lastVoiceQueryMs > QUERY_BURST_GAP_MS;
        lastVoiceQueryMs = now;
        if (burstStart) {
            KakaoHudLog.line("voice: steering button detected (nMirror lookup \"" + id + "\")");
            main.post(() -> clickVoice("steering button"));
        }
    }

    private static String firstString(Object[] args) {
        for (Object a : args) if (a instanceof String) return (String) a;
        return null;
    }

    // ---- 2) 음성 ID → 프록시 ----

    private void hookResourceIds() {
        try {
            Method getId = android.content.res.Resources.class.getMethod(
                    "getIdentifier", String.class, String.class, String.class);
            XposedBridge.hookMethod(getId, new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Object r = param.getResult();
                    if (!(r instanceof Integer) || (Integer) r != 0) return;
                    String name = (String) param.args[0];
                    String type = (String) param.args[1];
                    String pkg = (String) param.args[2];
                    boolean full = name != null && name.startsWith(KAKAO_PKG + ":id/");
                    boolean entry = "id".equals(type) && (pkg == null || KAKAO_PKG.equals(pkg));
                    if ((full || entry) && voiceLike(name)) {
                        param.setResult(PROXY_ID);
                        once("rid:" + name, "voice: resolved \"" + name + "\" to proxy");
                    }
                }
            });
        } catch (Throwable t) {
            KakaoHudLog.ex("voice getIdentifier hook", t);
        }
    }

    // ---- 키/인텐트 기록 + 프록시 부착 ----

    private void hookActivities() {
        try {
            XposedHelpers.findAndHookMethod(Activity.class, "onResume", new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Activity act = (Activity) param.thisObject;
                    if (!KAKAO_PKG.equals(act.getPackageName())) return;
                    resumed = act;
                    try {
                        View root = act.getWindow() == null ? null : act.getWindow().getDecorView();
                        if (root instanceof ViewGroup) attachProxy((ViewGroup) root);
                    } catch (Throwable t) {
                        KakaoHudLog.ex("voice proxy attach", t);
                    }
                }
            });
            XposedHelpers.findAndHookMethod(Activity.class, "dispatchKeyEvent", KeyEvent.class, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    KeyEvent e = (KeyEvent) param.args[0];
                    int code = e.getKeyCode();
                    if (e.getAction() != KeyEvent.ACTION_DOWN || code == KeyEvent.KEYCODE_VOLUME_UP
                            || code == KeyEvent.KEYCODE_VOLUME_DOWN) return;
                    once("k:" + code, "voice: key " + KeyEvent.keyCodeToString(code) + " in "
                            + param.thisObject.getClass().getSimpleName());
                }
            });
            XposedHelpers.findAndHookMethod(Activity.class, "onNewIntent", Intent.class, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    Intent i = (Intent) param.args[0];
                    if (i == null) return;
                    once("i:" + i.getAction() + i.getDataString(), "voice: new intent action=" + i.getAction()
                            + " data=" + i.getDataString() + " extras=" + (i.getExtras() == null ? "-" : i.getExtras().keySet()));
                }
            });
        } catch (Throwable t) {
            KakaoHudLog.ex("voice activity hooks", t);
        }
    }

    private void attachProxy(ViewGroup root) {
        if (proxy != null && proxy.getParent() == root) return;
        if (proxy != null && proxy.getParent() instanceof ViewGroup) {
            ((ViewGroup) proxy.getParent()).removeView(proxy);
        }
        View p = new View(root.getContext());
        p.setId(PROXY_ID);
        p.setContentDescription("음성 검색");
        p.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        p.setClickable(true);
        p.setFocusable(true);
        p.setAccessibilityDelegate(new View.AccessibilityDelegate() {
            @Override public void onInitializeAccessibilityNodeInfo(View host, AccessibilityNodeInfo info) {
                super.onInitializeAccessibilityNodeInfo(host, info);
                try { info.setViewIdResourceName(proxyName); } catch (Throwable ignored) { }
                android.graphics.Rect r = new android.graphics.Rect();
                info.getBoundsInScreen(r);
                once("node", "voice: proxy node built visible=" + info.isVisibleToUser()
                        + " clickable=" + info.isClickable() + " enabled=" + info.isEnabled()
                        + " bounds=" + r.toShortString());
            }
        });
        p.setOnClickListener(v -> {
            KakaoHudLog.line("voice: proxy clicked (as " + proxyName + ")");
            clickVoice("proxy");
        });
        root.addView(p, new ViewGroup.LayoutParams(2, 2));
        proxy = p;
        once("attach", "voice: proxy attached to " + root.getContext().getClass().getSimpleName());
    }

    // ---- 3) 카카오 화면의 음성 버튼 클릭 ----

    private void clickVoice(String why) {
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - lastClickMs < CLICK_DEBOUNCE_MS) {
            KakaoHudLog.line("voice: " + why + " ignored (just clicked)");
            return;
        }
        lastClickMs = now;
        Activity act = resumed;
        View root = act == null || act.getWindow() == null ? null : act.getWindow().getDecorView();
        if (root == null) {
            KakaoHudLog.line("voice: no resumed Kakao window");
            return;
        }
        List<View> hosts = new ArrayList<>();
        collectProviders(root, hosts);
        Candidate best = null;
        List<String> seen = new ArrayList<>();
        List<String> all = new ArrayList<>();
        for (View host : hosts) {
            AccessibilityNodeProvider provider = host.getAccessibilityNodeProvider();
            if (provider == null) continue;
            for (int id = 0; id <= MAX_VIRTUAL_ID; id++) {
                AccessibilityNodeInfo info;
                try {
                    info = provider.createAccessibilityNodeInfo(id);
                } catch (Throwable t) {
                    continue;
                }
                if (info == null) continue;
                String label = label(info);
                if (label.isEmpty()) continue;
                int score = score(label, info);
                if (all.size() < 80) all.add(label + (info.isClickable() ? "*" : "") + "#" + id);
                if (score > 0) seen.add(label + "#" + id + "(" + score + ")");
                if (score > 0 && (best == null || score > best.score)) best = new Candidate(provider, id, label, score);
            }
        }
        if (!candidatesLogged || best == null) {
            candidatesLogged = true;
            KakaoHudLog.line("voice: compose hosts=" + hosts.size() + " candidates=" + seen);
        }
        if (best == null) {
            KakaoHudLog.line("voice: no voice button on the current Kakao screen; labels(*=clickable)=" + all);
            return;
        }
        boolean ok = best.provider.performAction(best.id, AccessibilityNodeInfo.ACTION_CLICK, null);
        KakaoHudLog.line("voice: clicked \"" + best.label + "\" id=" + best.id + " ok=" + ok);
    }

    private static final class Candidate {
        final AccessibilityNodeProvider provider;
        final int id;
        final String label;
        final int score;

        Candidate(AccessibilityNodeProvider provider, int id, String label, int score) {
            this.provider = provider;
            this.id = id;
            this.label = label;
            this.score = score;
        }
    }

    private static void collectProviders(View v, List<View> out) {
        if (v.getAccessibilityNodeProvider() != null) out.add(v);
        if (v instanceof ViewGroup) {
            ViewGroup g = (ViewGroup) v;
            for (int i = 0; i < g.getChildCount(); i++) collectProviders(g.getChildAt(i), out);
        }
    }

    private static String label(AccessibilityNodeInfo info) {
        StringBuilder sb = new StringBuilder();
        if (info.getContentDescription() != null) sb.append(info.getContentDescription());
        if (info.getText() != null) {
            if (sb.length() > 0) sb.append(' ');
            sb.append(info.getText());
        }
        return sb.toString().trim();
    }

    /** 0: 음성 버튼 아님. 클릭 가능 + 우선 단어가 있으면 점수가 높다. */
    static int score(String label, AccessibilityNodeInfo info) {
        String l = label.toLowerCase(Locale.ROOT);
        boolean voice = false;
        for (String w : VOICE_DESC_WORDS) if (l.contains(w)) voice = true;
        if (!voice) return 0;
        for (String w : VOICE_DESC_EXCLUDE) if (l.contains(w)) return 0;
        int s = 1;
        for (String w : VOICE_DESC_PREFERRED) if (l.contains(w)) s += 2;
        if (info != null && info.isClickable()) s += 1;
        if (info != null && info.isVisibleToUser()) s += 1;
        return s;
    }
}
