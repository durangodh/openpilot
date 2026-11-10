package ai.comma.eonweb;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.text.InputType;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.webkit.HttpAuthHandler;
import android.webkit.URLUtil;
import android.webkit.WebResourceRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebViewDatabase;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Toast;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class MainActivity extends Activity {
    private static final int PORT = 7000;
    private static final String PREF_IP = "eon_ip";
    private static final String PASSWORD_KEY = "eon_web_password";
    private WebView web;
    private SharedPreferences prefs;
    private String ip;
    private String password;
    private boolean authAttempted;

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("eon_web", MODE_PRIVATE);
        ip = prefs.getString(PREF_IP, "192.168.0.17");
        password = readPassword(ip);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        LinearLayout toolbar = new LinearLayout(this);
        Button address = new Button(this);
        address.setText("이온 주소");
        address.setOnClickListener(view -> editAddress());
        Button refresh = new Button(this);
        refresh.setText("새로고침");
        refresh.setOnClickListener(view -> web.loadUrl(baseUrl()));
        toolbar.addView(address, new LinearLayout.LayoutParams(0, -2, 1));
        toolbar.addView(refresh, new LinearLayout.LayoutParams(0, -2, 1));
        root.addView(toolbar);
        web = new WebView(this);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !isEonUrl(request.getUrl());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (isEonUrl(Uri.parse(url))) authAttempted = false;
            }

            @Override
            public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
                if (!"EON Web".equals(realm) || (!ip.equals(host) && !(ip + ":" + PORT).equals(host))) {
                    handler.cancel();
                    return;
                }
                if (!authAttempted && password != null) {
                    authAttempted = true;
                    handler.proceed("eon", password);
                    return;
                }
                String loginIp = ip;
                if (authAttempted) prefs.edit().remove(passwordPreference(loginIp)).apply();
                password = null;
                EditText input = new EditText(MainActivity.this);
                input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
                CheckBox remember = new CheckBox(MainActivity.this);
                remember.setText("이 이온의 비밀번호 기억하기");
                LinearLayout fields = new LinearLayout(MainActivity.this);
                fields.setOrientation(LinearLayout.VERTICAL);
                fields.addView(input);
                fields.addView(remember);
                new AlertDialog.Builder(MainActivity.this)
                    .setTitle("이온 웹 로그인")
                    .setMessage("아이디 eon · " + ip)
                    .setView(fields)
                    .setPositiveButton("로그인", (dialog, which) -> {
                        if (!loginIp.equals(ip)) {
                            handler.cancel();
                            return;
                        }
                        password = input.getText().toString();
                        if (remember.isChecked()) rememberPassword(loginIp, password);
                        authAttempted = true;
                        handler.proceed("eon", password);
                    })
                    .setNegativeButton("취소", (dialog, which) -> handler.cancel())
                    .setOnCancelListener(dialog -> handler.cancel())
                    .show();
            }
        });
        web.setDownloadListener((url, agent, disposition, mime, length) -> {
            Uri uri = Uri.parse(url);
            if (!isEonUrl(uri)) return;
            String name = URLUtil.guessFileName(url, disposition, mime);
            if ("/api/params.json".equals(uri.getPath())) name = "g_web-params.json";
            try {
                DownloadManager.Request request = new DownloadManager.Request(uri)
                    .setTitle(name)
                    .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                if (password != null) {
                    String auth = Base64.encodeToString(("eon:" + password).getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
                    request.addRequestHeader("Authorization", "Basic " + auth);
                }
                getSystemService(DownloadManager.class).enqueue(request);
                Toast.makeText(this, "다운로드를 시작했습니다.", Toast.LENGTH_SHORT).show();
            } catch (RuntimeException error) {
                Toast.makeText(this, "다운로드를 시작하지 못했습니다.", Toast.LENGTH_SHORT).show();
            }
        });
        root.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        if (prefs.contains(PREF_IP)) web.loadUrl(baseUrl());
        else editAddress();
    }

    private String baseUrl() {
        return "http://" + ip + ":" + PORT + "/";
    }

    private boolean isEonUrl(Uri uri) {
        return "http".equals(uri.getScheme()) && ip.equals(uri.getHost())
            && uri.getPort() == PORT && uri.getUserInfo() == null;
    }

    private static String passwordPreference(String address) {
        return "password:" + address;
    }

    private static SecretKey passwordKey(boolean create) throws GeneralSecurityException, IOException {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        SecretKey key = (SecretKey) store.getKey(PASSWORD_KEY, null);
        if (key != null || !create) return key;
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(PASSWORD_KEY,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build());
        return generator.generateKey();
    }

    private String readPassword(String address) {
        String pref = passwordPreference(address);
        String saved = prefs.getString(pref, null);
        if (saved == null) return null;
        try {
            String[] parts = saved.split(":", 2);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, passwordKey(false),
                new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
            cipher.updateAAD(address.getBytes(StandardCharsets.UTF_8));
            return new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
        } catch (GeneralSecurityException | IOException | IllegalArgumentException | IndexOutOfBoundsException error) {
            prefs.edit().remove(pref).apply();
            return null;
        }
    }

    private void rememberPassword(String address, String value) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, passwordKey(true));
            cipher.updateAAD(address.getBytes(StandardCharsets.UTF_8));
            String iv = Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP);
            String encrypted = Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
            prefs.edit().putString(passwordPreference(address), iv + ":" + encrypted).apply();
        } catch (GeneralSecurityException | IOException error) {
            Toast.makeText(this, "비밀번호를 기억하지 못했습니다.", Toast.LENGTH_SHORT).show();
        }
    }

    private void editAddress() {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(ip);
        input.setSelectAllOnFocus(true);
        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("이온 IP 주소")
            .setMessage("이온 화면에 표시된 IP를 입력하세요. 포트는 7000입니다.")
            .setView(input)
            .setPositiveButton("연결", null)
            .setNegativeButton("취소", null)
            .setNeutralButton("저장된 비밀번호 지우기", (ignored, which) -> {
                prefs.edit().remove(passwordPreference(ip)).apply();
                password = null;
                authAttempted = false;
                WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();
                Toast.makeText(this, "현재 이온의 저장된 비밀번호를 지웠습니다.", Toast.LENGTH_SHORT).show();
            })
            .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String address = input.getText().toString().trim();
            if (!privateIpv4(address)) {
                input.setError("내부망 IPv4 주소를 입력하세요.");
                return;
            }
            if (!address.equals(ip)) password = readPassword(address);
            authAttempted = false;
            ip = address;
            prefs.edit().putString(PREF_IP, ip).apply();
            dialog.dismiss();
            web.loadUrl(baseUrl());
        }));
        dialog.show();
    }

    static boolean privateIpv4(String value) {
        String[] parts = value.split("\\.", -1);
        if (parts.length != 4) return false;
        int[] octets = new int[4];
        for (int i = 0; i < parts.length; i++) {
            if (!parts[i].matches("0|[1-9][0-9]{0,2}")) return false;
            octets[i] = Integer.parseInt(parts[i]);
            if (octets[i] > 255) return false;
        }
        return octets[0] == 10 || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
            || (octets[0] == 192 && octets[1] == 168);
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
