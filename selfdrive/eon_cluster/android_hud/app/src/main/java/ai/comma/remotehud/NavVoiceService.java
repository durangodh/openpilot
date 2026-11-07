package ai.comma.remotehud;

import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.util.ArrayList;
import java.util.List;

/**
 * Remote button -> the navigation app's own voice search (2026-11-07).
 *
 * Does exactly what nMirror does for the steering-wheel voice button
 * (NavigationButtonService, see naver_bridge/VOICE_BUTTON.md): find a visible node with
 * one of the known voice resource IDs in the navigation app's window and ACTION_CLICK it.
 * The Naver and Kakao LSPosed modules already attach those IDs to their Compose voice
 * buttons, so TMAP, Naver and Kakao all answer the same click. The navigation app stays
 * in front, so its map keeps running.
 */
public class NavVoiceService extends AccessibilityService {
    /** nMirror's voice-ID list (TMAP NUGU/A., Naver Clova, Kakao). */
    static final String[] VOICE_IDS = {
            "btn_speech_recognition", "v_clova_button", "search_voice_btn", "btn_kakaoi",
            "nugu_floating_action_button", "adot_navigation_wake_up_button"};

    /** ID 가 없을 때(네이버 미검증 버전·비안내 화면 등) 설명·글자로 찾는 단어. */
    private static final String[] VOICE_WORDS = {"음성", "클로바", "마이크", "말하기", "voice", "speech", "clova", "mic"};
    private static final String[] EXCLUDE_WORDS = {"안내", "볼륨", "음량", "소리", "끄기", "켜기", "mute", "volume"};
    private static final int MAX_NODES = 1500;

    private static volatile NavVoiceService instance;

    @Override
    protected void onServiceConnected() {
        instance = this;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    static boolean connected() {
        return instance != null;
    }

    /** Result text for the HUD card; null on success. */
    static String clickVoice(String navPackage) {
        NavVoiceService service = instance;
        if (service == null) return "접근성 서비스 꺼짐(HUD 앱 재시작)";
        List<AccessibilityNodeInfo> roots = new ArrayList<>();
        try {
            for (AccessibilityWindowInfo window : service.getWindows()) {
                AccessibilityNodeInfo root = window.getRoot();
                if (root != null && navPackage.contentEquals(String.valueOf(root.getPackageName()))) {
                    roots.add(root);
                }
            }
        } catch (Exception ignored) {
        }
        AccessibilityNodeInfo active = service.getRootInActiveWindow();
        if (active != null && navPackage.contentEquals(String.valueOf(active.getPackageName()))) {
            roots.add(active);
        }
        if (roots.isEmpty()) return "내비 화면이 앞에 없음";
        for (AccessibilityNodeInfo root : roots) {
            for (String name : VOICE_IDS) {
                List<AccessibilityNodeInfo> found = root.findAccessibilityNodeInfosByViewId(navPackage + ":id/" + name);
                if (found == null) continue;
                for (AccessibilityNodeInfo node : found) {
                    if (click(node)) {
                        HudDiagnostics.log("nav voice: clicked id " + name + " in " + navPackage);
                        return null;
                    }
                }
            }
        }
        // ID 로 못 찾으면 화면의 버튼 설명·글자("클로바 음성인식", "음성 검색" 등)로 찾는다.
        for (AccessibilityNodeInfo root : roots) {
            AccessibilityNodeInfo node = findByWords(root);
            if (node != null && click(node)) {
                HudDiagnostics.log("nav voice: clicked by label '" + label(node) + "' in " + navPackage);
                return null;
            }
        }
        // 원인 확인용: 보이는 클릭 가능 노드의 ID·설명을 진단 로그(EON 7714/trace 의 HUD 로그)에 남긴다.
        StringBuilder dump = new StringBuilder("nav voice: no button in " + navPackage + "; clickable:");
        for (AccessibilityNodeInfo root : roots) dumpClickable(root, dump, new int[]{0});
        HudDiagnostics.log(dump.toString());
        return "음성 버튼 없음(안내·주행 화면에서 다시)";
    }

    private static String label(AccessibilityNodeInfo node) {
        CharSequence d = node.getContentDescription();
        CharSequence t = node.getText();
        return ((d == null ? "" : d.toString()) + " " + (t == null ? "" : t.toString())).trim();
    }

    private static boolean matches(String label) {
        String l = label.toLowerCase(java.util.Locale.ROOT);
        if (l.isEmpty() || l.length() > 30) return false;
        for (String x : EXCLUDE_WORDS) if (l.contains(x)) return false;
        for (String w : VOICE_WORDS) if (l.contains(w)) return true;
        return false;
    }

    private static AccessibilityNodeInfo findByWords(AccessibilityNodeInfo root) {
        java.util.ArrayDeque<AccessibilityNodeInfo> queue = new java.util.ArrayDeque<>();
        queue.add(root);
        int seen = 0;
        while (!queue.isEmpty() && seen++ < MAX_NODES) {
            AccessibilityNodeInfo node = queue.poll();
            if (node.isVisibleToUser() && matches(label(node))) return node;
            for (int i = 0; i < node.getChildCount(); i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) queue.add(child);
            }
        }
        return null;
    }

    private static void dumpClickable(AccessibilityNodeInfo node, StringBuilder out, int[] count) {
        if (node == null || count[0] > MAX_NODES || out.length() > 3000) return;
        count[0]++;
        if (node.isClickable() && node.isVisibleToUser()) {
            out.append(" [").append(node.getViewIdResourceName()).append(" '").append(label(node)).append("']");
        }
        for (int i = 0; i < node.getChildCount(); i++) dumpClickable(node.getChild(i), out, count);
    }

    /** nMirror rule: a visible, enabled target that is clickable itself, or via its parent/child. */
    private static boolean click(AccessibilityNodeInfo node) {
        if (node == null || !node.isVisibleToUser() || !node.isEnabled()) return false;
        if (node.isClickable() && node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true;
        AccessibilityNodeInfo parent = node.getParent();
        for (int depth = 0; parent != null && depth < 3; depth++) {
            if (parent.isClickable() && parent.isEnabled()) {
                return parent.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            }
            parent = parent.getParent();
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null && child.isClickable() && child.isEnabled()
                    && child.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                return true;
            }
        }
        // Compose 호스트(ID 를 붙인 뷰)는 클릭 가능 표시가 없어도 접근성 클릭을 받는다.
        return node.performAction(AccessibilityNodeInfo.ACTION_CLICK);
    }

    /**
     * Turns the service on with root, keeping every service that is already enabled
     * (nMirror's own navigation-button service must stay on).
     */
    static void ensureEnabled(Context context) {
        if (instance != null) return;
        String ours = context.getPackageName() + "/" + NavVoiceService.class.getName();
        String script = "cur=$(settings get secure enabled_accessibility_services); "
                + "case \":$cur:\" in *\":" + ours + ":\"*) ;; "
                + "*) if [ -z \"$cur\" ] || [ \"$cur\" = null ]; then new=\"" + ours + "\"; "
                + "else new=\"$cur:" + ours + "\"; fi; "
                + "settings put secure enabled_accessibility_services \"$new\";; esac; "
                + "settings put secure accessibility_enabled 1";
        try {
            Runtime.getRuntime().exec(new String[]{"su", "-c", script}).waitFor();
        } catch (Exception ignored) {
        }
    }
}
