package ai.comma.remotehud;

/** Shell builtins avoid spawning two cat processes for each S20 thermal zone. */
final class PhoneStatsCommand {
    static final String READ =
            "IFS= read -r stat < /proc/stat; printf 'S:%s\\n' \"$stat\"; "
            + "for z in /sys/class/thermal/thermal_zone*; do "
            + "IFS= read -r type < \"$z/type\" 2>/dev/null || continue; "
            + "IFS= read -r temp < \"$z/temp\" 2>/dev/null || continue; "
            + "printf 'T:%s:%s\\n' \"$type\" \"$temp\"; done";

    static String deviceLabel(String model) {
        if (model != null && model.startsWith("SM-G98")) return "S20";
        if (model != null && model.startsWith("SM-G96")) return "S9";
        return "PHONE";
    }
}
