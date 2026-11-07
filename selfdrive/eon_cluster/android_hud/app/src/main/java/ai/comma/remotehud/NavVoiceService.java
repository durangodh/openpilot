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
                    if (click(node)) return null;
                }
            }
        }
        return "음성 버튼 없음(안내·주행 화면에서 다시)";
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
