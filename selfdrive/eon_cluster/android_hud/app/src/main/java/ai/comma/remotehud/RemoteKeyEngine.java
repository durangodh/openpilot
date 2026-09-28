package ai.comma.remotehud;

import java.util.HashMap;
import java.util.Map;

/** Android-independent gestures adapted from carrot-wip Clicks/Decoder policy. */
final class RemoteKeyEngine {
    interface Sink { void send(String command); }
    interface Mapping { String get(int key, String gesture); }
    private static final long LONG_MS = 700, DOUBLE_MS = 350, MAX_HOLD_MS = 10000;
    private long lastTick = -1;
    private final Sink sink;
    private final Mapping mapping;
    private final Map<Integer, Hold> down = new HashMap<>();
    private final Map<Integer, Long> pending = new HashMap<>();
    private static final class Hold {
        long start, last; boolean fired, expired, longBlocked;
        Hold(long now) { start = now; }
    }
    RemoteKeyEngine(Mapping mapping, Sink sink) { this.mapping = mapping; this.sink = sink; }
    static boolean assigned(String action) { return action != null && !action.equals("none"); }
    void reset() { down.clear(); pending.clear(); lastTick = -1; }
    void press(int key, long now) { if (lastTick < 0) lastTick = now; down.putIfAbsent(key, new Hold(now)); }
    void release(int key, long now, boolean cancelled) {
        Hold held = down.remove(key);
        if (cancelled) { pending.remove(key); return; }
        if (held == null || held.fired || held.expired || now < held.start || now - held.start > MAX_HOLD_MS) return;
        flushSingles(now);
        if (now - held.start >= LONG_MS && assigned(mapping.get(key, "long"))) {
            pending.remove(key);
            if (!held.longBlocked) emit(key, "long");
        } else if (assigned(mapping.get(key, "double"))) {
            if (pending.remove(key) != null) emit(key, "double");
            else pending.put(key, now);
        } else emit(key, "short");
    }
    private void emit(int key, String gesture) {
        String action = mapping.get(key, gesture);
        if (assigned(action)) sink.send(action);
    }
    private void flushSingles(long now) {
        for (Integer key : pending.keySet().toArray(new Integer[0])) {
            long age = now - pending.get(key);
            if (age >= DOUBLE_MS || age < 0) {
                pending.remove(key);
                if (age >= 0 && age <= 400) emit(key, "short");
            }
        }
    }
    private static boolean driving(String action) {
        return action != null && (action.equals("res") || action.equals("set") || action.equals("gap") || action.startsWith("lane_"));
    }
    void blockDrivingHolds() {
        for (Map.Entry<Integer, Hold> entry : down.entrySet())
            if (driving(mapping.get(entry.getKey(), "long"))) entry.getValue().longBlocked = true;
        for (Integer key : pending.keySet().toArray(new Integer[0]))
            if (driving(mapping.get(key, "short"))) pending.remove(key);
    }
    void tick(long now) {
        if (lastTick >= 0 && (now < lastTick || now - lastTick > 400)) down.clear();
        lastTick = now;
        flushSingles(now);
        for (Map.Entry<Integer, Hold> entry : down.entrySet()) {
            int key = entry.getKey(); Hold held = entry.getValue();
            long age = now - held.start;
            if (age < 0 || age > MAX_HOLD_MS) held.expired = true;
            String action = mapping.get(key, "long");
            if (held.expired || held.longBlocked || age < LONG_MS || !assigned(action)) continue;
            boolean lane = action.startsWith("lane_");
            boolean repeat = lane || action.equals("res") || action.equals("set");
            if (held.fired && (!repeat || now - held.last < (lane ? 150 : 500))) continue;
            // A stalled callback never catches up accumulated repeats.
            if (held.fired && now - held.last > 600) { held.expired = true; continue; }
            held.fired = true; held.last = now;
            pending.remove(key);
            sink.send(action);
        }
    }
}
