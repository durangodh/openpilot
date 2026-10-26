package ai.comma.remotekey;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/** stdin/stdout harness for tests/test_relay_interop.py: drives the real RelayProtocol. */
public final class RelayDriver {
    public static void main(String[] args) throws Exception {
        String key = args[0];
        RelayProtocol p = new RelayProtocol();
        long now = 1000;
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.US_ASCII));
        String line;
        while ((line = in.readLine()) != null) {
            String[] cmd = line.split(" ", 2);
            switch (cmd[0]) {
                case "advance": now += Long.parseLong(cmd[1]); System.out.println("ok"); break;
                case "hello": case "hello0": { byte[] b = p.hello(key, now, cmd[0].equals("hello")); System.out.println(b == null ? "-" : new String(b, StandardCharsets.US_ASCII)); break; }
                case "submit": System.out.println(p.submit(cmd[1], now) ? "queued" : "refused " + p.result); break;
                case "command": { byte[] b = p.command(key, now); System.out.println(b == null ? "-" : new String(b, StandardCharsets.US_ASCII)); break; }
                case "receive": System.out.println(p.receive(key, cmd[1], now) ? "eon" : "ignored"); break;
                case "state": System.out.println((p.pending() ? "pending" : "idle") + "|" + p.result); break;
                default: System.out.println("?");
            }
            System.out.flush();
        }
    }
}
