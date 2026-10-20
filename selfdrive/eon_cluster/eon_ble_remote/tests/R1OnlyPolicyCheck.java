package ai.comma.eonbleremote;

public final class R1OnlyPolicyCheck {
  private static int count;
  private static void check(boolean ok) {
    if (!ok) throw new AssertionError("policy check " + count);
    count++;
  }
  public static void main(String[] args) {
    String r1 = "AA:BB:CC:11:22:33";
    String other = "AA:BB:CC:11:22:34";
    check(r1.equals(R1OnlyPolicy.address("ble:aa:bb:cc:11:22:33")));
    check(r1.equals(R1OnlyPolicy.address(r1)));
    check(!R1OnlyPolicy.rejects("ble:" + r1, r1));
    check(!R1OnlyPolicy.rejects(r1, r1.toLowerCase()));
    check(R1OnlyPolicy.rejects(r1, other));
    check(R1OnlyPolicy.rejects("ble:" + r1, other));
    for (String missing : new String[]{"", null, "Magicsee R1", "ble:", "AA:BB", "ble:GG:BB:CC:11:22:33"}) {
      check(R1OnlyPolicy.address(missing).isEmpty());
      check(!R1OnlyPolicy.rejects(missing, other));
    }
    check(!R1OnlyPolicy.rejects(r1, null));
    check(!R1OnlyPolicy.rejects(r1, ""));
    check(R1OnlyPolicy.rejects(other, r1)); // Re-learning changes the exact allowlist.
    System.out.println(count + " R1 address policy checks passed");
  }
}
