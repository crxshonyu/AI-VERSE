# 카메라 기반 자동 방어 에어하키 — Raspberry Pi 소프트웨어

Raspberry Pi 4B 2GB + Camera Module 3 Wide를 대상으로 만든 **1축 자동 방어 프로토타입**입니다. 색상 기반 퍽 검출, 실제 테이블 좌표 변환, 위치·속도 추정, 측면 벽 반사 예측, USB 모터 제어, 골대 센서 득점과 RGB LED 상태 표시를 연결합니다.

**실행 가능한 소프트웨어와 선택적 Arduino 참고 펌웨어를 포함합니다. 실제 카메라·드라이버·기구에서 검증된 완성품은 아닙니다.** 기본 실행은 `dry-run`으로, 실제 모터를 움직이지 않습니다. 처음에는 시뮬레이션 → 실제 카메라만 → 실제 모터 순서로 확인합니다.

## 1. 이번 구현의 전제

| 항목 | 구현 전제 | 실제 제작 시 변경할 부분 |
|---|---|---|
| 운영체제 | Raspberry Pi OS의 현재 libcamera/Picamera2 환경 | Ubuntu라면 설치 방법을 별도 확인 |
| 카메라 | 테이블 상단에 고정한 Camera Module 3 Wide | 위치·초점·센서 모드·노출 |
| 퍽 | 테이블·패들과 색이 구분되는 단색 퍽 | `vision.hsv_ranges` |
| 자동 패들 | 로봇 측에서 좌우로만 움직이는 1축 패들 | 이동 범위·높이·반지름 |
| 모터 연결 | Pi → USB serial → 별도 모터 제어 보드 | 기존 보드에 프로토콜 구현 또는 `motor.py` 교체 |
| 골대 센서 | 두 개의 3.3V 디지털 신호, 기본 active-low | 입력 극성·핀·유효 펄스 길이 |
| LED | 일반 RGB LED의 세 채널 | 공통 캐소드/애노드와 핀 |

Arduino Uno는 주어진 구매 목록에 포함됐다고 가정하지 않았습니다. `firmware/`는 담당자 간 연결 기준으로 사용할 수 있는 **선택적 예제**입니다. 실제 보드가 ESP32/STM32 등이라면 Pi의 영상·예측 코드는 유지하고 모터 인터페이스를 맞추면 됩니다.

## 2. 실행 구조와 파일

| 파일 | 역할 |
|---|---|
| `airhockey/app.py` | 영상·추적·예측·게임·모터를 연결하는 주 루프 |
| `airhockey/vision.py` | Picamera2, 영상 재생, 렌즈/평면 보정, HSV 퍽 검출 |
| `airhockey/calibration.py` | 사진 저장, 테이블 네 점, 퍽 색상, 렌즈 보정 CLI |
| `airhockey/tracking.py` | 측정 시각 기반 속도 회귀, 반사·타격 후 재획득 |
| `airhockey/physics.py` | 다중 측면 반사·방어선 도착 위치·남은 시간 계산 |
| `airhockey/motor.py` | 모의 패들 또는 watchdog이 있는 USB 제어 인터페이스 |
| `airhockey/game.py`, `airhockey/io.py` | 경기 상태, 골대 입력, 중복 득점 방지, RGB LED |
| `airhockey/simulation.py` | 실제 영상 처리 코드를 통과하는 합성 카메라 입력 |
| `config/example.json` | 예제 설정. 모든 치수와 속도는 실측값이 아닌 시작값 |
| `firmware/` | 선택적 Uno STEP/DIR 모터 제어 예제와 통신 설명 |
| `tests/` | 물리·영상·통신·득점의 회귀 테스트 |
| `docs/VALIDATION_KO.md` | 이 전달본에서 확인한 범위와 실기 확인 항목 |

물리 계산 단위는 **mm, mm/s, s**입니다. 화면 픽셀을 그대로 모터 스텝으로 보내지 않습니다. Pi에서 픽셀을 테이블 mm 좌표로 바꾼 뒤, 모터 제어 보드가 자신의 `STEPS_PER_MM`로 변환합니다.

## 3. 처음 실행하기

### 3-1. 프로젝트 위치

압축 파일을 라즈베리파이 홈 디렉터리에 놓고 아래처럼 해제합니다. 압축 안의 최상위 폴더는 `airhockey`입니다. 이미 같은 이름의 작업 폴더가 있다면 새 폴더에 해제해 기존 보정을 보존하세요.

```bash
cd ~
unzip airhockey_pi4_project.zip
cd ~/airhockey
```

### 3-2. Raspberry Pi OS 패키지 설치

```bash
bash tools/install_pi.sh
```

스크립트가 하는 핵심 설치는 아래와 같습니다.

```bash
sudo apt update
sudo apt install -y --no-install-recommends \
  python3-picamera2 python3-opencv python3-numpy python3-serial \
  python3-gpiozero python3-lgpio
```

Pi의 Picamera2는 시스템 libcamera와 버전을 맞추는 것이 중요하므로 공식 안내에 따라 apt를 사용합니다. 이 프로젝트는 설치한 폴더 안에서 `python3 -m ...`으로 실행하므로 `pip install`이 필요 없습니다. 별도 가상환경을 쓴다면 시스템 Picamera2를 볼 수 있도록 `python3 -m venv --system-site-packages .venv`로 만듭니다. 오래된 `picamera`·`raspistill` 기반 코드가 아닙니다. [공식 카메라 소프트웨어 문서](https://www.raspberrypi.com/documentation/accessories/camera-software.html), [Picamera2 저장소](https://github.com/raspberrypi/picamera2).

카메라 연결 확인:

```bash
rpicam-hello --list-cameras
rpicam-hello -n -t 3000
```

`rpicam-hello`가 없다면 `sudo apt install rpicam-apps`로 설치합니다. 카메라 리본 케이블 연결은 Pi 전원을 끈 상태에서 진행합니다. 카메라 목록에서 IMX708이 확인되는지 봅니다.

### 3-3. 개인 설정 생성

```bash
python3 -m airhockey init-config config/local.json
```

이미 있는 파일은 덮어쓰지 않습니다. 다음 작업부터는 `config/local.json`을 수정합니다.

### 3-4. 카메라·모터 없이 시뮬레이션

```bash
python3 -m airhockey run \
  --config config/local.json \
  --source sim --start --auto-resume --seconds 20
```

화면이 연결돼 있고 OpenCV GUI를 사용할 수 있다면:

```bash
python3 -m airhockey run \
  --config config/local.json \
  --source sim --start --auto-resume --seconds 20 --show --mask
```

이 모드는 가상의 주황색 퍽 영상을 만들고 **실제 카메라 모드와 같은 검출·추적·예측 함수**를 실행합니다. 코너와 HSV는 시뮬레이션용으로 메모리에서 설정하며 사용자의 보정 파일은 변경하지 않습니다. GPIO나 시리얼 장치는 열지 않습니다. `--auto-resume`은 시뮬레이션에서만 허용됩니다.

PC에서도 별도 Python 가상환경에 `requirements-desktop.txt`를 설치하면 시뮬레이션을 실행할 수 있습니다. PC의 처리 속도는 Pi 4 성능을 대표하지 않습니다.

## 4. 실제 테이블에 맞춰야 하는 설정

```bash
nano config/local.json
```

아래는 예제이며 실제 장치 치수가 아닙니다.

```json
"table": {
  "width_mm": 600.0,
  "length_mm": 1000.0,
  "puck_radius_mm": 15.0,
  "paddle_radius_mm": 30.0,
  "paddle_y_mm": 920.0,
  "goal_width_mm": 200.0
},
"motor": {
  "port": "/dev/ttyACM0",
  "baudrate": 115200,
  "x_min_mm": 30.0,
  "x_max_mm": 570.0,
  "max_speed_mm_s": 120.0,
  "max_accel_mm_s2": 600.0,
  "home_timeout_s": 25.0
}
```

- `width_mm`, `length_mm`: 테이블 바깥 치수가 아닌 **퍽이 부딪히는 안쪽 벽 사이 거리**입니다.
- `puck_radius_mm`, `paddle_radius_mm`: 지름의 절반입니다.
- `paddle_y_mm`: 사람 쪽 끝벽을 y=0으로 놓았을 때 자동 패들 중심의 고정 y입니다.
- `x_min_mm`, `x_max_mm`: 패들 **중심**이 갈 수 있는 실제 최소·최대 x입니다. 홈 복귀 완료 위치가 x_min과 일치해야 합니다.
- `max_speed_mm_s`, `max_accel_mm_s2`: 실제 기구에서 확인한 값과 펌웨어 설정을 맞춥니다.
- `goal_width_mm`: 시뮬레이션의 골대 폭입니다. 실제 득점은 영상이 아니라 골대 센서로 판단합니다.

좌표는 **사람 쪽이 y=0, 로봇 쪽이 y=length**, 왼쪽이 x=0, 오른쪽이 x=width입니다. 카메라 영상의 위·아래 방향과 무관하게 보정 점의 물리적 순서를 지킵니다.

기본 모터 120 mm/s와 600 mm/s²는 참고 펌웨어의 보수적인 시작값입니다. 빠른 공격을 방어할 수 있다고 가정한 성능값이 아닙니다. 예를 들어 200 mm를 0.2초 안에 이동해야 한다면 평균 속도만으로도 1,000 mm/s가 필요하고, 정지·가속까지 고려하면 요구 성능이 더 커집니다. 코드의 `defend_unreachable_estimate`는 이 차이를 드러내는 표시입니다.

## 5. 카메라 보정

### 5-1. 카메라를 고정하고 RAW 사진 저장

퍽을 테이블 위에 둔 뒤:

```bash
python3 -m airhockey.calibration snapshot \
  --config config/local.json --output calibration/table.png
```

`table.png`는 렌즈 보정 전 원본 BGR 영상의 PNG이며, 옆의 `table.png.json`에는 카메라 설정과 선택한 센서 모드가 기록됩니다. 실제 실행과 같은 해상도·초점·센서 모드로 촬영해야 합니다.

기본 요청값은 640×480, 60fps, 노출 2,000µs, 아날로그 게인 4, 수동 초점 1.0 디옵터입니다. **60fps는 요청값이고 실측 처리 FPS가 아닙니다.** 센서 모드·조명·보정·CPU 부하에 따라 달라집니다. 지원하지 않는 모드는 오류로 알립니다. Camera Module 3 Wide의 공식 고속 센서 모드가 존재해도 프로그램 전체 FPS는 별도로 측정해야 합니다. [공식 카메라 사양](https://www.raspberrypi.com/documentation/accessories/camera.html).

`lens_position`의 디옵터 값은 대략 거리(m)의 역수에서 시작해 정지 퍽이 선명해지는 값으로 맞춥니다. 예를 들어 0.5m 높이면 2.0부터 확인할 수 있지만 정확한 초점값은 실물에서 조정합니다. 초점을 바꾼 후에는 다시 보정합니다. 노출이 길면 빠른 퍽이 번지므로 조명을 보완하고 짧은 노출을 유지하는 방향으로 조정합니다. [Picamera2 매뉴얼](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).

### 5-2. 테이블 네 점 지정

네 점은 테이블 안쪽 벽의 교차점이며, 순서는 다음과 같습니다.

1. 사람 쪽 왼쪽 = (0, 0)
2. 사람 쪽 오른쪽 = (width, 0)
3. 로봇 쪽 오른쪽 = (width, length)
4. 로봇 쪽 왼쪽 = (0, length)

화면에서 클릭하려면:

```bash
python3 -m airhockey.calibration corners \
  --config config/local.json --image calibration/table.png
```

왼쪽 클릭으로 순서대로 선택하고 Enter로 저장합니다. 오른쪽 클릭/r로 취소할 수 있습니다. SSH처럼 창을 열 수 없다면 이미지를 열어 확인한 **원본 픽셀 좌표**를 터미널에서 넣습니다.

```bash
python3 -m airhockey.calibration corners \
  --config config/local.json --image calibration/table.png \
  --points 40,20 600,20 600,460 40,460
```

위 숫자는 명령 형식 예시입니다. 실제 사진의 네 점으로 바꾸세요. 출력되는 `config/calibration_preview.png`에서 테이블 윤곽과 1~4 순서를 확인합니다. `vision.image_points`가 null인 상태에서는 실제 카메라 제어가 시작되지 않습니다.

### 5-3. 퍽 색상 지정

기본 HSV는 주황색을 가정합니다. 사진에서 퍽의 밝은 반사광이 아닌 균일한 색 부분을 선택하세요.

```bash
python3 -m airhockey.calibration hsv \
  --config config/local.json --image calibration/table.png --save
```

터미널에서 좌표를 지정할 수도 있습니다.

```bash
python3 -m airhockey.calibration hsv \
  --config config/local.json --image calibration/table.png \
  --point 320,240 --save
```

여기서 320,240도 실제 퍽 위치로 바꿉니다. OpenCV HSV의 H 범위는 0~179이며 빨간색처럼 179와 0 경계를 넘으면 두 구간을 사용합니다. 테이블·패들·조명 반사와 퍽이 같은 색이면 오검출할 수 있으므로 마스크에서 퍽만 남는지 확인합니다. [OpenCV HSV 임계처리 문서](https://docs.opencv.org/4.x/da/d97/tutorial_threshold_inRange.html).

### 5-4. Wide 렌즈 왜곡 보정

**네 점 호모그래피와 렌즈 왜곡 보정은 다른 작업입니다.** 직선 벽이 영상 가장자리에서 휘어 보이면 네 점만으로 중간 위치 오차를 없애기 어렵습니다. 광각 렌즈로 가장자리까지 사용한다면 체커보드로 렌즈를 먼저 보정하고, 같은 설정으로 테이블 네 점을 지정합니다. [OpenCV 카메라 보정](https://docs.opencv.org/4.x/d4/d94/tutorial_camera_calibration.html).

내부 교차점이 가로9×세로6인 체커보드를 서로 다른 위치·기울기에서 12~20장 정도 촬영합니다. 모서리뿐 아니라 전체 영상 영역을 채우며, 모두 같은 카메라 모드·해상도·초점으로 촬영합니다. 정지한 체커보드를 옮겨 가며 다음 명령의 파일명만 바꿀 수 있습니다.

```bash
python3 -m airhockey.calibration snapshot \
  --config config/local.json --output calibration/board_01.png

python3 -m airhockey.calibration lens \
  --images 'calibration/board_*.png' \
  --cols 9 --rows 6 --square-mm 25 --output config/lens.npz
```

`--cols`와 `--rows`는 검은 칸 개수가 아닌 **내부 교차점 수**, `--square-mm`는 인쇄물의 실측 한 칸 길이입니다. 이후 `config/local.json`의 `vision.lens_file`을 `"lens.npz"`로 변경하고 네 점 보정을 다시 수행합니다. 상대 경로는 설정 JSON 파일이 있는 디렉터리를 기준으로 해석합니다.

영상과 코너 점에 동일한 렌즈 보정을 적용합니다. 원본 이미지에 찍은 코너를 그대로 설정하면 코드가 보정하며, 사용자가 이미 보정한 코너를 다시 입력하면 이중 보정되므로 안 됩니다. 센서 모드·크롭·해상도·초점·카메라 위치를 변경하면 렌즈/테이블 보정을 다시 확인합니다.

기본 렌즈 모델은 OpenCV의 방사·접선 왜곡 모델입니다. 보정 후에도 가장자리 오차가 크면 체커보드의 위치 분포·재투영 오차를 점검하고, 별도 어안 모델이나 상부 시야 구성을 검토해야 합니다. 이 코드가 카메라와 테이블의 높이 차이에 의한 3D 오차까지 제거하는 것은 아닙니다.

## 6. 실제 카메라만 실행

```bash
python3 -m airhockey run \
  --config config/local.json --source camera \
  --start --seconds 30 --show --mask
```

SSH/터미널에서는 `--show --mask`를 빼세요. 아직 `--motor serial`을 지정하지 않았으므로 모터에 연결하지 않습니다. 손으로 퍽을 천천히 움직이며 먼저 검출 좌표를 확인하고, 이어서 직선·반사 공격을 확인합니다.

화면의 표시:

- 초록 원: 색상과 형태 조건을 통과한 퍽.
- 노란 선: 현재 추정 속도로 계산한 측면 반사 경로.
- 십자: 패들 앞쪽 방어선의 예상 퍽 중심 위치.
- 초록 십자: 설정한 속도·가속도 모델상 제시간 도착 가능.
- 빨간 십자: 이동 범위 또는 시간상 도착이 어려울 것으로 추정.
- `age`: 현재 처리 시점에서 본 카메라 프레임의 나이.
- `paddle estimate`: 모의 위치 또는 MCU 스텝 수 추정 위치.

카메라 모드의 `last_frame.png`는 사용자 환경에서 캡처한 결과이고, 저장소의 `docs/simulation_preview.png`는 합성 시험 결과입니다. 실행 로그와 영상은 저장소에 넣지 않고 각 실행에서 다시 생성합니다.

### 터미널/화면 조작

| 명령 + Enter | 화면 키 | 동작 |
|---|---|---|
| `start` | s | 첫 경기 시작 |
| `pause` | p | 감속 정지·일시정지 |
| `resume` | r | 득점/일시정지 후 재개. 골대 센서가 해제돼 있어야 함 |
| `new` | n | 점수·시간을 초기화하고 대기. `start`로 다시 시작 |
| `quit` | q 또는 Esc | 프로그램 종료·모터 비활성화 |

Ctrl+C와 SIGTERM도 정리 과정에서 모터를 비활성화합니다. `pause`·퍽 회수 시간도 1인당 기본 150초에 포함됩니다. 득점 뒤에는 `GOAL` 상태에서 멈춰 있고, 운영자가 퍽을 회수한 뒤 `resume`합니다. `new`는 모터 원점을 다시 잡는 명령이 아니며, 장애 후에는 프로그램을 재시작하고 다시 홈 복귀해야 합니다.

## 7. 실제 모터 연결

실제 모터 제어 보드가 준비된 뒤 진행합니다. 참고 Uno 구성은 `firmware/README_KO.md`를 먼저 읽어 드라이버·스위치·원점·펄스/mm 값을 맞춥니다. 단순 시리얼 숫자만 받는 기존 코드와는 프로토콜이 다릅니다.

```bash
python3 -m serial.tools.list_ports
```

예제 장치가 `/dev/ttyACM0`라면:

```bash
python3 -m airhockey run \
  --config config/local.json --source camera \
  --motor serial --port /dev/ttyACM0 --arm --home --show
```

**이 명령은 실제 원점 복귀를 시작합니다.** 자동 패들 이동 공간을 비운 상태에서 실행하세요. 연결 시 이동 거리·속도·가속도·프로토콜이 일치하는지 확인한 다음 홈 복귀합니다. 완료 후 대기 상태이며, 운영자가 `start`를 입력하면 방어를 시작합니다. 헤드리스에서는 `--show`를 뺍니다.

다음 조건은 코드가 거부합니다.

- `--arm` 또는 `--home`이 없는 실모터 실행.
- 합성 영상 또는 녹화 영상으로 실모터를 움직이는 조합.
- 펌웨어와 Pi의 이동 범위·속도·가속도 불일치.
- 추적되지 않는 퍽, 오래된 프레임, 범위 밖 목표.
- 끊긴 통신, 오래된 상태 응답, MCU가 보고한 고장.

목표 명령은 최대 기본 30Hz, 동일 위치의 작은 흔들림은 기본 2mm deadband로 억제합니다. 통신은 50ms 간격 PING, 호스트 상태/PONG 200ms 제한, MCU 250ms watchdog을 사용합니다. 영상 처리가 멈췄는데 heartbeat 스레드만 계속 살아 있는 구조를 피하도록 heartbeat는 주 제어 루프에서 보냅니다.

퍽 미검출·재추정 중에는 `HOLD`로 제어 감속합니다. 통신 장애·종료의 `STOP`은 드라이버를 비활성화하고 홈 기준을 해제합니다. **소프트웨어 감속 정지와 실제 비상 정지 회로는 서로 다른 역할**입니다. 기구에는 물리적 한계 스위치, 가드, 하드웨어 차단을 갖추고 모터 담당자와 확인합니다.

포트 접근 권한 오류라면 현재 사용자의 `dialout` 그룹을 확인합니다. 필요한 경우 `sudo usermod -aG dialout "$USER"` 후 로그아웃/로그인합니다. GPIO·카메라 권한도 해당 OS의 `gpio`·`video` 그룹 구성을 확인합니다. 프로그램 전체를 습관적으로 sudo로 실행할 필요는 없습니다.

## 8. 골대 센서와 RGB LED

기본적으로 GPIO는 꺼져 있습니다. 이 전달본의 예제 BCM 번호는 아래와 같습니다.

| 설정 | BCM GPIO | 의미 |
|---|---:|---|
| `human_goal_pin` | 17 | 사람 쪽 골대. 감지되면 로봇 득점 |
| `robot_goal_pin` | 27 | 로봇 쪽 골대. 감지되면 사람 득점 |
| `rgb_pins[0]` | 22 | 빨강 |
| `rgb_pins[1]` | 23 | 초록 |
| `rgb_pins[2]` | 24 | 파랑 |

BCM 번호는 커넥터의 물리 핀 번호와 다릅니다. 센서 출력은 Pi의 3.3V 입력과 호환되어야 하며, 5V 출력은 그대로 연결하지 않습니다. 일반 RGB LED는 각 채널 전류 제한 저항이 필요합니다. `rgb_active_high=true`는 기본 공통 캐소드 구성을 가정합니다. WS2812 같은 주소 지정 LED 스트립은 이 코드의 직접 대상이 아닙니다. [Raspberry Pi GPIO 문서](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html), [GPIO Zero 입력](https://gpiozero.readthedocs.io/en/stable/api_input.html), [GPIO Zero 출력](https://gpiozero.readthedocs.io/en/stable/api_output.html).

`pull_up=true`는 LOW로 떨어질 때 감지하는 active-low입니다. active-high 센서라면 실제 회로에 맞게 `pull_up=false`를 설정합니다. 입력을 띄워 두지 않습니다. 기본 유효 펄스 길이는 2ms, 다시 무장하기 위한 연속 해제 시간은 150ms입니다. 빠른 센서 펄스는 GPIO 콜백이 시간과 함께 큐에 넣어 영상 프레임 사이에서도 유지합니다. 실제 센서의 펄스 폭·노이즈에 맞춰 조정해야 합니다.

실제 카메라 모드에 `--gpio`를 추가하면 적용됩니다.

```bash
python3 -m airhockey run \
  --config config/local.json --source camera --gpio --show
```

이 명령은 센서/LED만 실제 장치이고 모터는 여전히 dry-run입니다. 검증 후 이전 실모터 명령에도 `--gpio`를 추가합니다.

| 경기 상태 | LED |
|---|---|
| 시작 대기 | 흰색 |
| 경기 진행 | 초록 |
| 일시정지 | 파랑 |
| 사람 득점 | 빨강 |
| 로봇 득점 | 노랑 |
| 시간 종료 | 청록 |
| 오류 | 보라. 종료 정리 시 LED는 꺼짐 |

센서를 계속 가리고 있어도 한 번만 득점하며, 부팅 시부터 가려져 있던 센서는 득점으로 세지 않습니다. 센서 해제를 확인해야 게임을 시작/재개할 수 있습니다.

## 9. 궤적 예측의 의미

현재 측정한 퍽 중심을 `(x,y)`, 속도를 `(vx,vy)`, 테이블 폭을 W, 퍽 반지름을 r이라고 합니다. y는 로봇 방향으로 증가하므로 `vy>0`일 때 로봇으로 들어옵니다.

정면에서 부딪히는 경우 퍽 중심이 만날 방어선은 다음과 같습니다.

```text
defense_y = paddle_y - paddle_radius - puck_radius
T = (defense_y - y) / vy
```

패들 중심선까지 기다리면 실제 접촉 시점보다 늦게 계산하므로 앞쪽 접선 기준을 사용합니다. 좌우 벽 반사에서 퍽 중심은 `[r, W-r]` 사이에 있어야 합니다. 펼친 좌표 `x + vx*T`를 삼각파 형태로 접으면 좌·우 여러 번 반사한 최종 위치를 한 번에 구할 수 있습니다.

```python
effective_width = W - 2 * r
phase = (x + vx * T - r) % (2 * effective_width)
hit_x = r + (phase if phase <= effective_width else 2 * effective_width - phase)
```

남은 시간은 `T - (현재 시각 - 측정 시각) - extra_latency_s`입니다. 카메라 프레임의 나이를 한 번만 차감합니다. `extra_latency_s=0.020`은 직렬 전송·스케줄링 등에 대한 **임시 여유값**이며 실제 지연 측정 후 조정합니다. 모터 이동 시간은 속도·가속도·현재 이동 방향을 따로 고려합니다.

속도는 최근 기본 5개 위치를 실제 카메라 타임스탬프에 대해 회귀해서 구합니다. 측면 반사로 vx가 뒤집히거나 패들 타격으로 진행 방향이 바뀌면 이력을 버리고 최소3개 새 표본으로 재추정합니다. 반사 전과 후 표본을 같은 직선으로 평균하지 않기 위한 처리입니다. 최소3표본의 대기 때문에 반사 직후 약 두 프레임 간격 이상 반응이 늦어지는 대가가 있습니다.

이 모델은 짧은 구간의 등속 운동과 이상적인 측면 반사를 가정합니다. 마찰, 벽의 탄성 손실, 회전, 패들 충격을 미리 정확히 알지는 못합니다. 이를 영상의 다음 측정으로 계속 수정합니다. `vy<=0`이면 반대 끝벽을 거쳐 돌아오는 장기 경로를 추측하지 않고, 확인된 복귀 방향에서 중앙으로 돌아갑니다.

Picamera2의 이미지와 `SensorTimestamp`는 같은 capture request에서 함께 가져오고, `queue=False`로 보관된 이전 프레임 재사용을 피합니다. 타임스탬프를 Python monotonic 시계에 맞춥니다. Camera Module 3의 롤링 셔터·노출 시간 때문에 영상 전체를 단일한 정확한 촬영 순간으로 볼 수는 없습니다. `age`는 센서 타임스탬프 기준이며 정밀한 광학 지연 측정값이 아닙니다. [Picamera2 매뉴얼](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).

## 10. 로그와 검증

각 실행의 `runs/날짜_시각/`에 다음이 생깁니다.

| 파일 | 내용 |
|---|---|
| `effective_config.json` | 실행 시 실제 사용한 설정 |
| `telemetry.csv` | 프레임별 위치·속도·예상 위치·목표 명령·프레임 나이·상태·점수 |
| `summary.json` | 처리 FPS, 프레임 나이와 처리 시간의95백분위, 검출/예측 수, 오류 |
| `last_frame.png` | 마지막 확인용 화면 |

`motor_x_est_mm`는 이름 그대로 추정 위치이며 실제 패들 오차를 측정한 값이 아닙니다. `reachable_estimate`는 이상적 가속도 모델상 평가입니다. 시뮬레이션에는 실제 하드웨어 성능을 의미하지 않는다는 메타데이터를 남깁니다. 전체 CSV는 보존하고 긴 실행의 요약 백분위는 최근 최대20,000프레임으로 제한합니다.

테스트 실행:

```bash
python3 -m unittest discover -s tests -v
```

녹화가 필요하다면 dry-run 점검 중에 먼저 확인하세요. 디스크 기록은 처리 속도에 영향을 줄 수 있습니다.

```bash
python3 -m airhockey run \
  --config config/local.json --source camera --start --seconds 20 \
  --record recordings/check.avi
```

이 파일은 **표시가 그려진 점검용 영상**입니다. 프레임 누락과 가변 처리 간격을 완전히 표현하는 원본 센서 기록이 아니므로 CSV의 촬영 시각을 함께 봅니다. 정밀한 재생 검증에는 별도로 저장한 동일 해상도·동일 시야의 원본 고정 FPS 영상을 사용하세요. 녹화 영상 입력은 다음과 같습니다.

```bash
python3 -m airhockey run \
  --config config/local.json --source video --video recordings/raw_clip.mp4 --start
```

## 11. 문제가 있을 때

| 증상 | 우선 확인 |
|---|---|
| `No cameras available` | 전원 차단 후 CSI 케이블 방향·삽입 상태, rpicam 카메라 목록 |
| `No module named picamera2` | Raspberry Pi OS apt 설치, 잘못된 가상환경 여부 |
| 코너 보정 오류 | 사진 해상도와 설정, 점의 물리적 순서, 서로 교차하는 사각형 여부 |
| 퍽이 안 잡힘 | HSV 마스크, 노출·조명, 퍽의 색·면적, 테이블 ROI |
| 패들이 퍽으로 인식됨 | 색상을 구분, HSV 범위·원형도·면적 조정 |
| 가장자리에서 예측이 틀림 | 광각 왜곡 보정, 테이블 치수, 카메라 이동·초점 변경 |
| 벽 근처에서 잠깐 목표가 없어짐 | 반사 후 최소3표본 재획득 구간인지 로그 확인 |
| `defend_unreachable_estimate` | 모터의 실제 속도·가속도·남은 거리, 영상 지연 |
| `telemetry stale`, `WDT` | USB 상태, 지나친 영상/GUI/녹화 부하, 실제 MCU loop 성능 |
| `LEFT/RIGHT/ESTOP/SWITCH` | NC 스위치·배선·기구 방향. 원인 해결 후 재시작/홈 |
| `Firmware/config mismatch` | travel = x_max - x_min, 속도·가속도 양쪽 일치 여부 |
| 득점 후 안 움직임 | 정상 GOAL 대기인지 확인. 퍽 회수·센서 해제 후 resume |

문제를 재현할 때 `effective_config.json`, `summary.json`, `telemetry.csv`, 원본 정지 사진을 함께 확인하면 영상·예측·구동 중 어느 단계인지 분리하기 쉽습니다.

## 12. 참고한 1차 자료

- [Raspberry Pi 카메라 소프트웨어](https://www.raspberrypi.com/documentation/accessories/camera-software.html)
- [Picamera2 공식 매뉴얼](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [Camera Module 하드웨어 사양](https://www.raspberrypi.com/documentation/accessories/camera.html)
- [OpenCV 카메라 보정](https://docs.opencv.org/4.x/d4/d94/tutorial_camera_calibration.html)
- [OpenCV HSV 범위 검출](https://docs.opencv.org/4.x/da/d97/tutorial_threshold_inRange.html)
- [GPIO Zero 입력](https://gpiozero.readthedocs.io/en/stable/api_input.html), [출력](https://gpiozero.readthedocs.io/en/stable/api_output.html)
- [AccelStepper 공식 문서](https://www.airspayce.com/mikem/arduino/AccelStepper/classAccelStepper.html)

자료 확인일: 2026-11-09. 소프트웨어 의존성·카메라 모드·지원 OS는 실제 설치 버전에서도 확인하세요.
