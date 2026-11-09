# 선택 사항: Arduino Uno 모터 제어 예제

이 폴더는 **실제 모터 제어 보드가 아직 정해지지 않았을 때 사용할 수 있는 참고 구현**이다. 라즈베리파이에서는 카메라·예측·게임을 처리하고, Arduino Uno에서는 STEP/DIR 펄스와 원점 복귀를 처리한다. 기존 모터 제어 담당자가 다른 보드를 사용한다면 아래 프로토콜을 구현하거나 `airhockey/motor.py`의 인터페이스를 교체하면 된다.

`airhockey_motor/airhockey_motor.ino`는 Arduino IDE의 Library Manager에서 **AccelStepper**를 설치한 뒤 Uno 보드에 업로드한다. 이 전달본은 호스트 프로토콜을 가상 장치로 검증했으며, 실제 Arduino/드라이버/기구에서의 컴파일·구동·제동 검증은 별도로 해야 한다.

## 전제 및 핀

예제는 STEP/DIR 입력과 **LOW일 때 활성화되는 ENABLE** 입력이 있는 외부 스테퍼 드라이버를 전제로 한다. 모터를 Arduino 또는 Raspberry Pi GPIO에 직접 연결하지 않는다. 드라이버의 전원, 전류 제한, 로직 전압, 펄스 폭 조건은 실제 부품 사양에 맞춘다.

| Arduino 핀 | 역할 | 예제 연결 |
|---|---|---|
| D2 | STEP | 드라이버 STEP 입력 |
| D3 | DIR | 드라이버 DIR 입력 |
| D4 | ENABLE_N | 드라이버 활성화 입력, 외부 풀업 필요 |
| D7 | 왼쪽 원점 스위치 | NC 접점과 GND 사이, `INPUT_PULLUP` |
| D8 | 오른쪽 한계 스위치 | NC 접점과 GND 사이, `INPUT_PULLUP` |
| D9 | 비상 정지 감시 | NC 접점과 GND 사이, `INPUT_PULLUP` |
| USB | Raspberry Pi와 통신 | USB 데이터 케이블 |

NC는 정상 시 닫혀 있는 접점이다. 예제는 `LOW=정상`, `HIGH=스위치 눌림 또는 단선`으로 해석한다. 이 입력들이 연결되지 않은 상태에서는 원점 복귀가 거부된다. 비상 정지는 소프트웨어 입력만으로 구성하지 않고 **실제 드라이버의 동력 또는 활성화 회로를 끊는 회로**와 함께 구성한다. 드라이버 ENABLE 외부 풀업은 Arduino가 재시작하거나 부트로더가 실행되는 동안 출력을 비활성화한다. 해당 드라이버와 회로에서 이 동작을 확인해야 한다.

## 반드시 맞춰야 하는 기구 설정

| 상수 | 예제 값 | 의미 |
|---|---:|---|
| `STEPS_PER_MM` | 20 | 드라이버에 보내는 펄스 수 / 이동 mm |
| `TRAVEL_MM` | 540 | 원점 복귀 완료 후 허용하는 패들 중심 이동 거리 |
| `MAX_SPEED_MM_S` | 120 | 최대 이동 속도 |
| `ACCEL_MM_S2` | 600 | 가속·감속도 |
| `HOME_OFFSET_MM` | 5 | 왼쪽 스위치 검출 위치에서 안쪽으로 이동하는 거리 |
| `INVERT_DIRECTION` | false | DIR 극성 반전 여부 |

예를 들어 1회전 200스텝 모터, 1/4 마이크로스텝, 피치 2 mm·20톱니 풀리라면 `200 × 4 / (2 × 20) = 20 pulse/mm`이다. 이 계산은 예제일 뿐 실제 부품 설정을 확인해야 한다.

**양의 방향은 반드시 왼쪽 스위치에서 멀어지는 방향이어야 한다.** 반대로 연결되어 있으면 먼저 기구를 안전한 상태로 두고 `INVERT_DIRECTION`을 수정한다. 기본 원점 복귀 속도는 빠른 탐색 30 mm/s, 마지막 접촉 8 mm/s이며 전체 25초 안에 끝나야 한다. 이동 거리를 바꾸면 원점 복귀 제한 시간도 검토한다.

원점 복귀는 왼쪽 스위치 탐색 → 정지 → 스위치에서 빠져나오기 → 느린 재접촉 → 5 mm 안쪽 이동 순서이다. 마지막 위치를 펌웨어 좌표 `0 mm`로 정한다. 따라서 테이블 좌표에서 이때의 실제 패들 중심이 `x_min_mm`와 일치하도록 측정해야 한다. 기본 호스트 설정 `x_min_mm=30`, `x_max_mm=570`은 이동 거리 `540 mm`에 대응한다. 오른쪽 스위치는 최대 허용 위치보다 약간 바깥에 배치하여 정상 명령으로는 닿지 않게 한다.

호스트와 펌웨어의 이동 거리, 속도, 가속도가 다르면 연결 단계에서 중단한다. 설정값이 잘못된 상태로 우연히 구동되는 것을 막기 위한 동작이다.

## 통신 프로토콜 v1

- 속도: USB serial, 115200 baud, ASCII, 줄 끝 `\n`.
- `<token>`: 호스트가 연결할 때 생성하는 8자리 소문자 16진 문자열.
- 모든 위치 단위는 mm, 속도는 mm/s, 가속도는 mm/s².
- 펌웨어 `0 mm`는 홈 위치이며 호스트가 테이블 좌표로 변환한다.

| 방향 | 메시지 | 의미 |
|---|---|---|
| Pi → MCU | `HELLO <token>` | 새 연결 시작. 새로운 token이면 즉시 비활성화하고 원점 상태 해제 |
| MCU → Pi | `READY 1 <token> <travel> <speed> <accel>` | 프로토콜 및 기구 설정 응답 |
| Pi → MCU | `PING <token> <seq>` | 50 ms 간격 동작 확인 |
| MCU → Pi | `PONG <token> <seq>` | 해당 PING의 응답 |
| Pi → MCU | `HOME <token>` | 명시적 원점 복귀 시작 |
| Pi → MCU | `MOVE <token> <target>` | 원점 복귀 후 목표 위치 갱신 |
| Pi → MCU | `HOLD <token>` | 현재 방향으로 감속하여 멈추고 홈 기준 유지 |
| Pi → MCU | `STOP` | 즉시 펄스 중단·드라이버 비활성화·홈 기준 해제 |
| MCU → Pi | `STATE <token> <seq> <x> <v> <homed> <mode> <fault>` | 약 20 Hz의 상태 통보 |

상태 `homed`는 0 또는 1이며, `mode`는 `IDLE`, `HOME`, `RUN`, `HOLD`, `FAULT` 중 하나다. 정상 `fault`는 `NONE`이다. 고장 예시는 `ESTOP`, `LEFT`, `RIGHT`, `WDT`, `RANGE`, `HTIME`, `HTRAVEL`, `SWITCH`, `PROTO`, `STOP`이다. 한 줄 상태는 시리얼 송신 버퍼에 여유가 있을 때 전송하며 모터 스텝을 위해 송신 대기로 루프를 막지 않는다.

정상 PING이 250 ms 동안 없으면 MCU는 정지하고 홈 기준을 해제한다. 원점 복귀를 시작할 때도 최근 100 ms 안에 정상 PING을 받아야 한다. Pi에서도 200 ms 이상 상태/PONG이 오래되면 중단한다. 이전 세션 token을 가진 명령·상태, 중복된 PING, 순서가 지난 상태는 수용하지 않는다. 장애 후 자동으로 재연결하거나 이동을 재개하지 않는다. 원인을 해결하고 프로그램을 다시 실행하여 명시적으로 원점 복귀한다.

`HOLD`는 가속도에 따른 감속이다. 비상 정지 명령이 아니다. `STOP`은 펄스를 즉시 끊기 때문에 실제 관성 이동 또는 스텝 유실을 배제할 수 없어 원점 상태를 해제한다. 표시되는 패들 위치는 **엔코더로 측정한 위치가 아니라 출력한 스텝 수에서 계산한 추정치**다. 빠른 퍽을 실제로 방어할 수 있는지는 카메라 지연과 함께 모터 속도·가속도·기구 강성·이동 거리로 결정된다.

## 자료

- AccelStepper 공식 문서: https://www.airspayce.com/mikem/arduino/AccelStepper/classAccelStepper.html
- Arduino AccelStepper 라이브러리: https://docs.arduino.cc/libraries/accelstepper/
- Arduino 입력과 풀업: https://docs.arduino.cc/language-reference/en/variables/constants/inputOutputPullup/

AccelStepper 문서는 Uno 같은 16 MHz Arduino에서 약 4,000 step/s를 참고 상한으로 설명하며, `run()`을 충분히 자주 호출해야 한다고 명시한다. 예제의 120 mm/s × 20 step/mm = 2,400 step/s도 실기에서 달성 여부를 검증해야 한다. USB 처리·상태 출력·인터럽트와 부하가 있는 기구의 실제 한계는 별도이다. 더 높은 방어 속도를 원할 때는 단순히 숫자를 크게 바꾸기 전에 제어 보드와 펄스 생성 방식도 검토한다.
