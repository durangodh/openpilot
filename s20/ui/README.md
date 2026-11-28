# S20 화면 (단계 7b)

- `s20uid.py` (chroot): openpilot 상태 + 모델 경로·차선·도로 경계·앞차를 도로 카메라 프레임(1928x1208) 좌표로
  투영해 `/s20share/ui.json` 에 초당 약 20번 기록
- `app/` (안드로이드 앱 `ai.s20.ui`, "s20 openpilot"): 카메라 링(`road.ring`)을 GL 셰이더로 그리고 그 위에 ui.json 을
  그림 — 경로(인게이지 시 초록), 차선, 도로 경계(빨강), 앞차, 현재/설정 속도, 경고, 상태 테두리
  - 빌드: `app/build.sh` (Gradle 없이 aapt2 + d8 + apksigner), 결과 `app/s20ui.apk`
  - 앱은 `/data/s20pilot/share` 를 읽으므로 SELinux permissive 필요 (enter_chroot.sh 가 설정)

설치·실행 (PC):
```
adb shell su -c "cp /data/s20pilot/rootfs/root/openpilot/s20/ui/app/s20ui.apk /data/local/tmp/ && pm install -r /data/local/tmp/s20ui.apk"
adb shell am start -n ai.s20.ui/.MainActivity
```
