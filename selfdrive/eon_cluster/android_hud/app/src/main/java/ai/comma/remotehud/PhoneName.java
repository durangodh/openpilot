package ai.comma.remotehud;

/** HUD 에 표시하는 휴대폰 이름. S20(SM-G98x)이 기본이고, S9(SM-G96x)에서만 S9 로 보인다. */
final class PhoneName {
    static String of(String model) {
        if (model != null && model.startsWith("SM-G96")) return "S9";
        return "S20";
    }

    private PhoneName() { }
}
