package ai.comma.kakaohud;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityNodeProvider;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedHelpers;

/**
 * 핸들 음성 버튼 → 카카오 음성 검색.
 *
 * nMirror 는 핸들 음성 버튼을 누르면 네비 화면의 음성 버튼을 리소스 ID 로 찾아
 * 클릭하는데, 카카오 음성 버튼(Compose)에는 ID 가 없다. nMirror 프로세스 쪽
 * NmirrorVoiceHook 이 그 요청을 가로채 "press" 브로드캐스트를 보내면, 여기서 카카오
 * 화면의 Compose 접근성 노드 중 음성 버튼("음성서비스" 등)을 찾아 클릭한다. 화면을
 * 직접 누르는 것과 같은 경로라 카카오 내부 클래스 이름에 기대지 않는다.
 * (2026-10-05 b59~b68 기기 로그로 확인한 경로. 진단용 훅은 b69 에서 정리.)
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

    void install() {
        hookPressReceiver();
        hookResumedActivity();
    }

    /** nMirror 쪽 훅(NmirrorVoiceHook)이 보낸 누름/무시 알림을 받는다. */
    private void hookPressReceiver() {
        try {
            XposedHelpers.findAndHookMethod(android.app.Application.class, "onCreate", new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Context ctx = (Context) param.thisObject;
                    if (!KAKAO_PKG.equals(ctx.getPackageName())) return;
                    BroadcastReceiver r = new BroadcastReceiver() {
                        @Override public void onReceive(Context c, Intent i) {
                            String call = String.valueOf(i.getStringExtra("call"));
                            KakaoHudLog.line("voice: nMirror " + call);
                            if (call.startsWith(NmirrorVoiceHook.PRESS)) {
                                main.post(() -> clickVoice());
                            }
                        }
                    };
                    IntentFilter f = new IntentFilter(NmirrorVoiceHook.ACTION_VOICE);
                    if (android.os.Build.VERSION.SDK_INT >= 33) {
                        ctx.registerReceiver(r, f, Context.RECEIVER_EXPORTED);
                    } else {
                        ctx.registerReceiver(r, f);
                    }
                }
            });
        } catch (Throwable t) {
            KakaoHudLog.ex("voice nMirror receiver", t);
        }
    }

    private void hookResumedActivity() {
        try {
            XposedHelpers.findAndHookMethod(Activity.class, "onResume", new XC_MethodHook() {
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Activity act = (Activity) param.thisObject;
                    if (KAKAO_PKG.equals(act.getPackageName())) resumed = act;
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
