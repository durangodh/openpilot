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
 * Invisible activity that runs one speech recognition for the HUD voice assistant.
 *
 * Android only lets an app use the microphone while it is in the foreground, and the HUD
 * runs as a background service, so each remote press briefly brings this transparent
 * activity up on the phone's own (hidden) screen. A USB microphone, when attached, is
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
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ko-KR")
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
                .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, getPackageName());
        recognizer.startListening(intent);
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

    private static String first(Bundle results) {
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
        String message;
        switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                message = "말씀을 못 들었어요";
                break;
            case SpeechRecognizer.ERROR_AUDIO:
                message = "마이크 오류";
                break;
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                message = "마이크 권한 없음(HUD 앱에서 허용)";
                break;
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                message = "음성 인식 네트워크 오류";
                break;
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                message = "음성 인식 사용 중";
                break;
            default:
                message = "음성 인식 오류 " + error;
        }
        end(null, message);
    }

    @Override public void onReadyForSpeech(Bundle params) { }
    @Override public void onBeginningOfSpeech() { }
    @Override public void onRmsChanged(float rmsdB) { }
    @Override public void onBufferReceived(byte[] buffer) { }
    @Override public void onEndOfSpeech() { }
    @Override public void onEvent(int eventType, Bundle params) { }
}
