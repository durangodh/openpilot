package ai.comma.remotehud;

import android.app.Activity;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.view.WindowManager;

import java.lang.ref.WeakReference;
import java.util.ArrayList;

/**
 * Fallback for the HUD voice assistant when the service cannot open the microphone.
 *
 * Normally VoiceAssistant listens inside the HUD service. nMirror mirrors the phone's
 * display 0 to the car, so this transparent activity shows over the navigation app and
 * may pause its map for a few seconds; it is used only when the service path is blocked. A USB microphone, when attached, is
 * picked by Android as the input device automatically.
 */
public class VoiceActivity extends Activity implements RecognitionListener {
    private static final long TIMEOUT_MS = 14_000L;
    private static volatile WeakReference<VoiceActivity> active = new WeakReference<>(null);

    private final Handler handler = new Handler(Looper.getMainLooper());
    private SpeechRecognizer recognizer;
    private boolean done;

    static void cancelActive() {
        VoiceActivity activity = active.get();
        if (activity != null) activity.runOnUiThread(() -> activity.end(null, null));
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        } else {
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                    | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        }
        active = new WeakReference<>(this);
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            end(null, "음성 인식 서비스 없음(Google 앱 확인)");
            return;
        }
        recognizer = SpeechRecognizer.createSpeechRecognizer(this);
        recognizer.setRecognitionListener(this);
        recognizer.startListening(VoiceAssistant.recognizerIntent(this));
        handler.postDelayed(() -> end(null, "시간 초과"), TIMEOUT_MS);
    }

    private void end(String text, String error) {
        if (done) return;
        done = true;
        handler.removeCallbacksAndMessages(null);
        VoiceAssistant assistant = VoiceAssistant.get();
        if (assistant != null) {
            if (text != null) {
                assistant.onSpeech(text);
            } else if (error != null) {
                assistant.onSpeechError(error);
            }
        }
        if (recognizer != null) {
            try {
                recognizer.cancel();
                recognizer.destroy();
            } catch (Exception ignored) {
            }
            recognizer = null;
        }
        finish();
        overridePendingTransition(0, 0);
    }

    @Override
    protected void onDestroy() {
        if (active.get() == this) active = new WeakReference<>(null);
        end(null, null);
        super.onDestroy();
    }

    static String first(Bundle results) {
        ArrayList<String> list = results == null ? null
                : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return list == null || list.isEmpty() ? "" : list.get(0);
    }

    @Override
    public void onResults(Bundle results) {
        end(first(results), null);
    }

    @Override
    public void onPartialResults(Bundle partialResults) {
        VoiceAssistant assistant = VoiceAssistant.get();
        if (assistant != null) assistant.onPartial(first(partialResults));
    }

    @Override
    public void onError(int error) {
        end(null, errorText(error));
    }

    static String errorText(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                return "말씀을 못 들었어요";
            case SpeechRecognizer.ERROR_AUDIO:
                return "마이크 오류";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                return "마이크 권한 없음(HUD 앱에서 허용)";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                return "음성 인식 네트워크 오류";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                return "음성 인식 사용 중";
            default:
                return "음성 인식 오류 " + error;
        }
    }

    @Override
    public void onReadyForSpeech(Bundle params) {
        VoiceAssistant assistant = VoiceAssistant.get();
        if (assistant != null) assistant.onReady();
    }
    @Override public void onBeginningOfSpeech() { }
    @Override public void onRmsChanged(float rmsdB) { }
    @Override public void onBufferReceived(byte[] buffer) { }
    @Override public void onEndOfSpeech() { }
    @Override public void onEvent(int eventType, Bundle params) { }
}
