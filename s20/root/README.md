# 단계 1 — LineageOS + Magisk (SM-G781N, r8q)

출처: LineageOS 위키 r8q 설치 안내 (2026-11 기준). 지원 모델: SM-G781B, G781B/DS, **G781N**, G7810.
엑시노스(SM-G780F)는 불가.

## 준비
- 확인된 시험 기기 (2026-11-27): SM-G781N, RAM 6GB, One UI 5.1 (Android 13),
  펌웨어 G781NKSSDGYJ1 / G781NOKRDGYJ1, 보안 패치 2025-10-01 (최신), OEM 잠금 해제 메뉴 있음
- 원래 펌웨어: **안드로이드 13, 최신 보안 패치** (삼성 업데이트 끝까지)
- 원래 OS로 한 번 부팅해 카메라(메인·초광각), USB, Wi-Fi 확인
- **구글 계정 전부 삭제** (FRP 잠김 방지)
- 개발자 옵션 → USB 디버깅 켜기
- PC: adb(platform-tools), samloader-rs (Odin 대신 사용)
- 녹스 영구 해제됨 (삼성페이·보안폴더 불가) — 공기계 전제

## 부팅 모드
- 다운로드 모드: 전원 끈 상태에서 볼륨 위 + 볼륨 아래 누른 채 USB를 PC에 연결
- 리커버리: 전원 끈 상태에서 볼륨 위 + 전원 (USB 연결된 상태)

## 1) 부트로더 해제
1. Wi-Fi 연결 → 빌드번호 7번 → 개발자 옵션 → **OEM 잠금 해제** 켜기
   (메뉴가 없으면 KG 대기 상태일 수 있음, 멈추고 확인)
2. 위키에서 `vbmeta.img` 다운로드
3. 다운로드 모드 → "Unlock Bootloader" 선택(볼륨 위 길게) → 초기화·재부팅
4. 다시 OEM 잠금 해제가 켜져 있는지 확인
5. 다시 다운로드 모드 →
   ```
   samloader flash --partition VBMETA vbmeta.img
   ```
6. 재부팅 → 초기화 요구 시 진행 → 초기 설정 건너뛰고 Wi-Fi만 → OEM 잠금 해제 켜짐 재확인

## 2) LineageOS 리커버리
1. 위키 링크의 `recovery.img` (다른 리커버리 쓰지 말 것, zip이면 먼저 풀기)
2. 다운로드 모드 → "Continue"
3. ```
   samloader flash --partition RECOVERY recovery.img --no-reboot
   ```
4. 끝나도 `Downloading... Do not turn off target!!`가 계속 보임 (정상)
5. **바로 리커버리로**: 볼륨 아래 + 전원 8~10초 → 화면 꺼지는 순간 놓고 → 볼륨 위 + 전원
   - 일반 부팅이 되면 삼성 리커버리가 덮어씀 → 2번부터 다시
6. LineageOS 로고가 보이면 성공

## 3) LineageOS 설치
1. 리커버리: Factory reset → **Format data / factory reset**
2. Apply update → **Apply from ADB**
3. PC: `adb -d sideload lineage-XX.X-YYYYMMDD-nightly-r8q-signed.zip`
   - 47% 근처에서 멈춘 듯 보이거나 `Total xfer: 1.00x` 뒤 오류처럼 보여도 정상인 경우가 많음
4. GApps는 설치하지 않음 (오파 전용 기기)
5. Reboot system now → 첫 부팅은 몇 분 걸림

## 4) Magisk 루팅
1. Magisk apk 파일 이름을 `Magisk-vXX.zip`으로 바꿈
2. 리커버리 → Apply update → Apply from ADB → `adb -d sideload Magisk-vXX.zip`
   (서명 경고가 나오면 Yes)
3. 재부팅 → Magisk 앱 실행 → 추가 설정 요청 시 따름
4. 확인:
   ```
   adb shell su -c id                 # uid=0(root) 이면 통과
   adb shell su -c 'ls -l /dev/kgsl-3d0'
   ```
   폰에 뜨는 루트 허용 창에서 허용.

## 통과 기준 → 다음 단계
- `uid=0` 확인, `/dev/kgsl-3d0` 존재 → 단계 2 (`s20/chroot/setup_chroot.sh`)
