package ai.comma.remotehud;

public final class PhoneStatsCommandCheck {
    private static void check(boolean ok) { if (!ok) throw new AssertionError(); }
    public static void main(String[] args) {
        if (args.length > 0 && args[0].equals("command")) {
            System.out.print(PhoneStatsCommand.READ);
            return;
        }
        check(PhoneStatsCommand.READ.indexOf("/proc/stat") < PhoneStatsCommand.READ.indexOf("thermal_zone"));
        check(!PhoneStatsCommand.READ.contains("$(cat"));
        check(!PhoneStatsCommand.READ.contains("$(head"));
        check(PhoneStatsCommand.deviceLabel("SM-G981N").equals("S20"));
        check(PhoneStatsCommand.deviceLabel("SM-G986N").equals("S20"));
        check(PhoneStatsCommand.deviceLabel("SM-G960N").equals("S9"));
        check(PhoneStatsCommand.deviceLabel(null).equals("PHONE"));
        check(PhoneStatsCommand.deviceLabel("other").equals("PHONE"));
        System.out.println("Phone stats command: 8 checks passed");
    }
}
