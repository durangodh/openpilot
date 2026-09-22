package ai.comma.remotehud;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Bounded stdout drain: a blocked root command must not freeze sampling. */
final class BoundedProcessRead {
    static String read(Process process, long timeoutMs) {
        AtomicReference<String> output = new AtomicReference<>();
        Thread reader = new Thread(() -> {
            try (InputStream input = process.getInputStream();
                 ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[1024];
                int count;
                while ((count = input.read(buffer)) >= 0) {
                    if (bytes.size() + count > 32768) return;
                    bytes.write(buffer, 0, count);
                }
                if (bytes.size() > 0) output.set(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
            } catch (Exception ignored) { }
        }, "hud-stats-output");
        reader.setDaemon(true);
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        reader.start();
        try {
            if (!process.waitFor(timeoutMs, TimeUnit.MILLISECONDS) || process.exitValue() != 0) return null;
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) return null;
            reader.join(Math.max(1, TimeUnit.NANOSECONDS.toMillis(remaining)));
            return reader.isAlive() ? null : output.get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        } finally {
            process.destroy();
            if (process.isAlive()) process.destroyForcibly();
        }
    }
}
