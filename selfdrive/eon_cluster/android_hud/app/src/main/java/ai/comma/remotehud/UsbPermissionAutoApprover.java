package ai.comma.remotehud;

import android.content.Context;
import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Root-only helper for the Android USB default-app permission dialog. */
final class UsbPermissionAutoApprover {

    private static final String UI_DUMP = "/data/local/tmp/remote_hud_usb_permission.xml";
    private static final AtomicBoolean RUNNING = new AtomicBoolean(false);
    private static final Pattern NODE = Pattern.compile("<node\\s+[^>]*>");
    private static final Pattern BOUNDS = Pattern.compile(
            "bounds=\"\\[(\\d+),(\\d+)]\\[(\\d+),(\\d+)]\"");

    private UsbPermissionAutoApprover() {
    }

    static void watch(Context context) {
        if (!RUNNING.compareAndSet(false, true)) {
            return;
        }
        final String appName = context.getApplicationInfo().loadLabel(
                context.getPackageManager()).toString();
        new Thread(() -> {
            try {
                // The permission controller can appear late during boot. Keep the watcher
                // bounded so a missing dialog never leaves a permanent root process behind.
                for (int attempt = 0; attempt < 16; attempt++) {
                    String xml = dumpUi();
                    if (isTargetDialog(xml, appName) && approve(xml, appName)) {
                        return;
                    }
                    SystemClock.sleep(500L);
                }
            } finally {
                runRoot("rm -f " + UI_DUMP);
                RUNNING.set(false);
            }
        }, "hud-usb-permission").start();
    }

    private static boolean approve(String xml, String appName) {
        String checkNode = findCheckbox(xml);
        if (checkNode == null) {
            return false;
        }

        if (!attributeIsTrue(checkNode, "checked")) {
            if (!tapNode(checkNode)) {
                return false;
            }
            SystemClock.sleep(350L);
            xml = dumpUi();
            if (!isTargetDialog(xml, appName)) {
                return false;
            }
            checkNode = findCheckbox(xml);
            if (checkNode == null || !attributeIsTrue(checkNode, "checked")) {
                return false;
            }
        }

        String confirmNode = findConfirmButton(xml);
        return confirmNode != null && tapNode(confirmNode);
    }

    private static boolean isTargetDialog(String xml, String appName) {
        if (xml == null || xml.length() == 0) {
            return false;
        }
        // Require both our app and the TURZX product text so another system dialog can
        // never be accepted accidentally.
        return xml.contains(appName) && (xml.contains("TURZX1.00")
                || xml.contains("TURZX") || xml.contains("1CBE"));
    }

    private static String findCheckbox(String xml) {
        Matcher matcher = NODE.matcher(xml);
        while (matcher.find()) {
            String node = matcher.group();
            boolean checkboxClass = node.contains("class=\"android.widget.CheckBox\"");
            boolean checkable = attributeIsTrue(node, "checkable");
            // Some Android 13 PermissionController builds put the label in a
            // sibling TextView, leaving the CheckBox text empty. The whole XML was
            // already restricted to our app + TURZX dialog in isTargetDialog().
            if ((checkboxClass || checkable) && hasBounds(node)) {
                return node;
            }
        }
        return null;
    }

    private static String findConfirmButton(String xml) {
        Matcher matcher = NODE.matcher(xml);
        while (matcher.find()) {
            String node = matcher.group();
            boolean button = node.contains("class=\"android.widget.Button\"")
                    || node.contains("resource-id=\"android:id/button1\"");
            boolean confirm = node.contains("text=\"확인\"")
                    || node.contains("text=\"OK\"")
                    || node.contains("text=\"허용\"")
                    || node.contains("text=\"Allow\"");
            if (button && confirm && hasBounds(node)) {
                return node;
            }
        }
        return null;
    }

    private static boolean attributeIsTrue(String node, String attribute) {
        return node.contains(attribute + "=\"true\"");
    }

    private static boolean hasBounds(String node) {
        return BOUNDS.matcher(node).find();
    }

    private static boolean tapNode(String node) {
        Matcher bounds = BOUNDS.matcher(node);
        if (!bounds.find()) {
            return false;
        }
        int left = Integer.parseInt(bounds.group(1));
        int top = Integer.parseInt(bounds.group(2));
        int right = Integer.parseInt(bounds.group(3));
        int bottom = Integer.parseInt(bounds.group(4));
        if (right <= left || bottom <= top) {
            return false;
        }
        return runRoot("input tap " + ((left + right) / 2) + " " + ((top + bottom) / 2)) != null;
    }

    private static String dumpUi() {
        return runRoot("uiautomator dump --compressed " + UI_DUMP
                + " >/dev/null 2>&1; cat " + UI_DUMP + " 2>/dev/null");
    }

    private static String runRoot(String command) {
        Process process = null;
        try {
            process = Runtime.getRuntime().exec(new String[]{"su", "-c", command});
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            try (InputStream input = process.getInputStream()) {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.read(buffer)) >= 0) {
                    output.write(buffer, 0, count);
                }
            }
            if (process.waitFor() != 0) {
                return null;
            }
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        } catch (Exception ignored) {
            return null;
        } finally {
            if (process != null) {
                process.destroy();
            }
        }
    }
}
