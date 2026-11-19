import ai.comma.eonlink.Relay;
public final class RelayHarness {
  public static void main(String[] args) throws Exception {
    try (Relay relay = new Relay(text -> {}, Integer.parseInt(args[0]))) {
      System.out.println(relay.start(0)); System.out.flush();
      System.in.read();
    }
  }
}
