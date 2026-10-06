package ai.comma.kakaohud;

import android.app.Activity;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityNodeProvider;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;

/**
 * 핸들 음성 버튼 → 카카오 음성 검색. nMirror 는 건드리지 않는다.
 *
 * nMirror 는 핸들 음성 버튼을 누르면 접근성 서비스로 네비 화면에서 정해진 리소스 ID
 * (btn_speech_recognition 등)를 찾아 클릭한다. 카카오 음성 버튼은 Compose 라 ID 가 없다.
 *
 *  1) 네이버 모듈과 같은 방식: 카카오 주행 화면의 ComposeView 에 nMirror 가 찾는 ID 를
 *     붙이고, 접근성 클릭이 오면 화면의 "음성서비스" 버튼을 누른다. nMirror 가 찾아서
 *     누르면 재시도도 멈추므로 누를 때마다 동작한다.
 *  2) 예비: nMirror 가 그 ID 를 못 찾는 경우(예전 로그: 숨은 창을 조회), 조용하다가
 *     시작된 첫 ID 조회 묶음을 누름으로 본다. 두 경로가 같은 누름에 겹치면 3초 안의
 *     두 번째 클릭은 무시된다.
 *
 * 2026-10-06: nMirror 프로세스 후킹(b66~b68)은 S9 부팅 때 핫스팟 자동설정을 꺼뜨려
 * 제거했다. LSPosed 범위에는 카카오내비만 둔다.
 */
final class KakaoVoice {
    private static final String KAKAO_PKG = "com.locnall.KimGiSa";
    private static final String[] VOICE_DESC_WORDS = {"음성", "마이크", "voice", "speech", "말하기"};
    private static final String[] VOICE_DESC_PREFERRED = {"검색", "명령", "인식", "말하기", "search"};
    private static final String[] VOICE_DESC_EXCLUDE = {"안내", "볼륨", "음량", "소리", "mute", "끄기", "켜기"};
    private static final int MAX_VIRTUAL_ID = 3000;
    private static final long CLICK_DEBOUNCE_MS = 3000;

    private volatile Activity resumed;
    private volatile long lastClickMs;
    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());

    private static final String[] NMIRROR_VOICE_ID_WORDS = {
            "wake_up", "nugu", "kakaoi", "speech", "voice", "clova"};
    private static final long QUERY_BURST_GAP_MS = 5000;
    // nMirror 는 버튼 없이도 매분 정각에 같은 조회를 한다. 정각 직후 시작한 묶음은 무시.
    private static final long MINUTE_TICK_WINDOW_MS = 2500;
    private volatile long lastVoiceQueryMs;

    // nMirror 가 카카오에서 찾는 ID 중 카카오 리소스에 실제로 있는 첫 이름을 쓴다.
    private static final String[] NMIRROR_KAKAO_IDS = {
            "btn_speech_recognition", "v_clova_button", "search_voice_btn", "btn_kakaoi",
            "nugu_floating_action_button", "adot_navigation_wake_up_button"};
    private int voiceViewId;
    private String voiceViewName;
    private final java.util.WeakHashMap<View, Boolean> bound = new java.util.WeakHashMap<>();

    void install() {
        hookVoiceLookups();
        hookResumedActivity();
    }

    /** 주행 화면의 ComposeView(접근성 노드 제공자의 부모)에 nMirror 가 찾는 ID 를 붙인다. */
    private void bindVoiceHost(Activity act) {
        try {
            if (voiceViewId == 0) {
                for (String n : NMIRROR_KAKAO_IDS) {
                    int id = act.getResources().getIdentifier(n, "id", KAKAO_PKG);
                    if (id != 0) { voiceViewId = id; voiceViewName = n; break; }
                }
                if (voiceViewId == 0) {
                    KakaoHudLog.line("voice: none of nMirror's ids exist in Kakao resources; bind skipped");
                    voiceViewId = -1;
                }
            }
            if (voiceViewId <= 0 || act.getWindow() == null) return;
            List<View> hosts = new ArrayList<>();
            collectProviders(act.getWindow().getDecorView(), hosts);
            for (View host : hosts) {
                Object parent = host.getParent();
                // Compose 자체(노드 제공자)의 접근성 위임은 건드리면 안 된다. 그 부모 ComposeView 에만 붙인다.
                if (!(parent instanceof View) || !parent.getClass().getName().contains("ComposeView")) continue;
                View target = (View) parent;
                if (target.getAccessibilityNodeProvider() != null) continue;
                if (target.getId() != View.NO_ID && target.getId() != voiceViewId) continue;  // 카카오가 쓰는 ID 는 유지
                synchronized (bound) {
                    if (bound.containsKey(target)) continue;
                    bound.put(target, Boolean.TRUE);
                }
                target.setId(voiceViewId);
                target.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
                target.setAccessibilityDelegate(new View.AccessibilityDelegate() {
                    @Override public void onInitializeAccessibilityNodeInfo(View v, AccessibilityNodeInfo info) {
                        super.onInitializeAccessibilityNodeInfo(v, info);
                        // 터치 동작은 그대로 두고(setClickable 안 함) 접근성에서만 누를 수 있게 보인다.
                        info.setClickable(true);
                        info.setEnabled(true);
                        info.addAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_CLICK);
                    }

                    @Override public boolean performAccessibilityAction(View v, int action, android.os.Bundle args) {
                        if (action == AccessibilityNodeInfo.ACTION_CLICK) {
                            KakaoHudLog.line("voice: nMirror clicked " + voiceViewName);
                            main.post(KakaoVoice.this::clickVoice);
                            return true;
                        }
                        return super.performAccessibilityAction(v, action, args);
                    }
                });
                KakaoHudLog.line("voice: " + voiceViewName + " bound to "
                        + target.getClass().getSimpleName() + " in " + act.getClass().getSimpleName());
            }
        } catch (Throwable t) {
            KakaoHudLog.ex("voice bind", t);
        }
    }

    private void hookVoiceLookups() {
        try {
            Class<?> aic = XposedHelpers.findClass("android.view.AccessibilityInteractionController", null);
            int n = XposedBridge.hookAllMethods(aic, "findAccessibilityNodeInfosByViewIdClientThread", new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    for (Object a : param.args) {
                        if (a instanceof String) { onVoiceQuery((String) a); break; }
                    }
                }
            }).size();
            if (n == 0) KakaoHudLog.line("voice: lookup hook not found on this Android version");
        } catch (Throwable t) {
            KakaoHudLog.ex("voice lookup hook", t);
        }
    }

    private void onVoiceQuery(String id) {
        String l = id.toLowerCase(Locale.ROOT);
        boolean voice = false;
        for (String w : NMIRROR_VOICE_ID_WORDS) if (l.contains(w)) voice = true;
        if (!voice) return;
        long now = android.os.SystemClock.elapsedRealtime();
        boolean burstStart = now - lastVoiceQueryMs > QUERY_BURST_GAP_MS;
        lastVoiceQueryMs = now;
        if (!burstStart) return;
        long msInMinute = System.currentTimeMillis() % 60000L;
        if (msInMinute < MINUTE_TICK_WINDOW_MS) {
            KakaoHudLog.line("voice: lookup at minute tick ignored (+" + msInMinute + "ms)");
        } else {
            KakaoHudLog.line("voice: steering button press (+" + msInMinute + "ms)");
            main.post(this::clickVoice);
        }
    }

    private void hookResumedActivity() {
        try {
            XposedHelpers.findAndHookMethod(Activity.class, "onResume", new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Activity act = (Activity) param.thisObject;
                    if (!KAKAO_PKG.equals(act.getPackageName())) return;
                    resumed = act;
                    // Compose 가 그려진 뒤에 붙인다.
                    main.postDelayed(() -> bindVoiceHost(act), 1500);
                }
            });
        } catch (Throwable t) {
            KakaoHudLog.ex("voice activity hook", t);
        }
    }

    // ---- 카카오 화면의 음성 버튼 클릭 ----

    private void clickVoice() {
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - lastClickMs < CLICK_DEBOUNCE_MS) {
            KakaoHudLog.line("voice: press ignored (just clicked)");
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
                if (all.size() < 80) all.add(label + (info.isClickable() ? "*" : "") + "#" + id);
                int score = score(label, info);
                if (score > 0 && (best == null || score > best.score)) best = new Candidate(provider, id, label, score);
            }
        }
        if (best == null) {
            KakaoHudLog.line("voice: no voice button on the current Kakao screen; labels(*=clickable)=" + all);
            return;
        }
        boolean ok = best.provider.performAction(best.id, AccessibilityNodeInfo.ACTION_CLICK, null);
        KakaoHudLog.line("voice: clicked \"" + best.label + "\" ok=" + ok);
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
