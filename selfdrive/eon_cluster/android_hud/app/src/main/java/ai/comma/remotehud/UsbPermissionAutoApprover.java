package ai.comma.remotehud;

import android.content.Context;
import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Root-only fallback for vendor ROMs that reject a direct IUsbManager grant. */
final class UsbPermissionAutoApprover {

    private static final String UI_DUMP = "/data/local/tmp/remote_hud_usb_permission.xml";
    private static final int WATCH_ATTEMPTS = 60;
    private static final long WATCH_INTERVAL_MS = 250L;
    private static final long ROOT_TIMEOUT_SECONDS = 8L;
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
                for (int attempt = 0; attempt < WATCH_ATTEMPTS; attempt++) {
                    String xml = dumpUi();
                    if (isTargetDialog(xml, appName) && approve(xml, appName)) {
                        return;
                    }
                    SystemClock.sleep(WATCH_INTERVAL_MS);
                }
            } finally {
                runRoot("rm -f " + UI_DUMP);
                RUNNING.set(false);
            }
        }, "hud-usb-permission-fallback").start();
    }

    private static boolean approve(String xml, String appName) {
        String checkNode = findCheckbox(xml);
        if (checkNode != null && !attributeIsTrue(checkNode, "checked")) {
            if (!tapNode(checkNode)) {
                return false;
            }
            SystemClock.sleep(150L);
            xml = dumpUi();
            if (!isTargetDialog(xml, appName)) {
                return false;
            }
        }
        String confirmNode = findConfirmButton(xml);
        return confirmNode != null && tapNode(confirmNode);
    }

    private static boolean isTargetDialog(String xml, String appName) {
        return xml != null && !xml.isEmpty() && xml.contains(appName)
                && (xml.contains("TURZX1.00") || xml.contains("TURZX")
                || xml.contains("1CBE"));
    }

    private static String findCheckbox(String xml) {
        Matcher matcher = NODE.matcher(xml);
        while (matcher.find()) {
            String node = matcher.group();
            if ((node.contains("class=\"android.widget.CheckBox\"")
                    || attributeIsTrue(node, "checkable")) && hasBounds(node)) {
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
        return runRoot("input tap " + ((left + right) / 2)
                + " " + ((top + bottom) / 2)) != null;
    }

    private static String dumpUi() {
        return runRoot("uiautomator dump --compressed " + UI_DUMP
                + " >/dev/null 2>&1; cat " + UI_DUMP + " 2>/dev/null");
    }

    private static String runRoot(String command) {
        Process process = null;
        Thread outputReader = null;
        try {
            process = new ProcessBuilder("su", "-c", command)
                    .redirectErrorStream(true).start();
            final Process runningProcess = process;
            final ByteArrayOutputStream output = new ByteArrayOutputStream();
            outputReader = new Thread(() -> copyOutput(runningProcess, output),
                    "hud-usb-permission-output");
            outputReader.setDaemon(true);
            outputReader.start();
            if (!process.waitFor(ROOT_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                outputReader.join(1000L);
                return null;
            }
            outputReader.join(1000L);
            if (process.exitValue() != 0) return null;
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        } catch (Exception ignored) {
            return null;
        } finally {
            if (process != null) {
                if (process.isAlive()) process.destroyForcibly();
            }
            if (outputReader != null && outputReader.isAlive()) outputReader.interrupt();
        }
    }

    private static void copyOutput(Process process, ByteArrayOutputStream output) {
        try (InputStream input = process.getInputStream()) {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = input.read(buffer)) >= 0) {
                output.write(buffer, 0, count);
            }
        } catch (Exception ignored) {
        }
    }
}
