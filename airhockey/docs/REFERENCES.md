# 참고 자료와 외부 구성 요소

아래 문서는 구현 API와 사용 방법을 확인한 1차 자료입니다. 이 프로젝트의 테스트 통과가 외부 라이브러리나 실제 하드웨어의 모든 동작을 검증했다는 의미는 아닙니다. 라이브러리 소스는 저장소에 복사해 포함하지 않습니다.

| 구성 요소 | 용도 | 공식 자료 |
|---|---|---|
| Raspberry Pi 카메라 환경 | OS 패키지 설치, rpicam/Picamera2 | [카메라 소프트웨어](https://www.raspberrypi.com/documentation/accessories/camera-software.html) |
| Picamera2 | 캡처 요청, RGB888 배열, 노출·초점·타임스탬프 | [매뉴얼](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf), [저장소](https://github.com/raspberrypi/picamera2) |
| Camera Module 3 Wide | 센서와 지원 영상 모드 | [하드웨어 사양](https://www.raspberrypi.com/documentation/accessories/camera.html) |
| OpenCV | HSV, contour, homography, camera calibration | [HSV](https://docs.opencv.org/4.x/da/d97/tutorial_threshold_inRange.html), [보정](https://docs.opencv.org/4.x/d4/d94/tutorial_camera_calibration.html) |
| NumPy | 영상 배열, 보정 행렬과 NPZ | [문서](https://numpy.org/doc/stable/) |
| pyserial | USB serial 연결과 읽기·쓰기 제한 시간 | [API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html) |
| GPIO Zero | 디지털 입력 이벤트와 RGB LED | [입력](https://gpiozero.readthedocs.io/en/stable/api_input.html), [출력](https://gpiozero.readthedocs.io/en/stable/api_output.html) |
| AccelStepper | 선택적 Uno 예제의 스텝·가감속 | [클래스 문서](https://www.airspayce.com/mikem/arduino/AccelStepper/classAccelStepper.html) |
| Python packaging | pyproject와 실행 명령, 선택 의존성 | [PyPA 가이드](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/) |
| Ruff | 정적 검사와 코드 포맷 | [문서](https://docs.astral.sh/ruff/) |
| GitHub Actions | Python 버전별 자동 검사 | [Python 테스트 가이드](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) |

Python 모듈은 기본 라이브러리와 NumPy/OpenCV/pyserial을 사용합니다. 실제 카메라·GPIO 기능만 Picamera2/libcamera와 gpiozero를 필요로 하며, PC의 합성 영상 검사는 이 하드웨어 모듈 없이 실행합니다. AccelStepper는 Arduino 쪽의 별도 선택 의존성입니다.

프로젝트 배포 라이선스는 아직 정하지 않았습니다. 외부 라이브러리를 포함해 배포하는 경우 해당 버전의 공식 배포본에 포함된 라이선스와 고지를 확인하세요. 이 목록은 라이선스 해석이나 외부 저작물의 권리 재지정 문서가 아닙니다.

확인 기준일: 2026-11-09.
