# 개발 및 기여 안내

이 저장소의 작업은 영상 입력 → 퍽 검출·추적 → 반사 궤적 예측 → 목표 위치 전달 순서로 확인합니다. 시뮬레이션과 실제 장비 결과를 구분하여 기록해 주세요.

## 개발 환경

Python 3.10 이상을 사용합니다. 아래 명령은 저장소 최상위에서 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[headless,dev]'
```

OpenCV 창을 사용하려면 새 가상 환경에서 `.[desktop,dev]`를 선택합니다. `desktop`과 `headless`는 같은 `cv2` 모듈을 제공하므로 함께 설치하지 않습니다. Raspberry Pi의 실제 카메라 환경은 [README](README.md)의 APT/Picamera2 설치 순서를 따릅니다. 이 개발용 pip 설치는 Raspberry Pi 카메라 시스템 패키지를 대체하지 않습니다.

실제 테이블의 설정은 다음 명령으로 별도 생성합니다.

```bash
airhockey init-config config/local.json
```

`config/example.json`은 공통 예제입니다. 실제 치수·색 범위·모서리 좌표는 `config/local.json`에서 조정합니다. 개인 보정 파일, 촬영 영상, 실행 로그는 `.gitignore`에서 제외합니다. 문서에 필요한 작은 예제 그림은 `docs/` 아래에 둘 수 있습니다.

## 변경 확인

```bash
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
airhockey run --config config/example.json --source sim --start --seconds 3
python -m build
```

서식을 정리할 때는 `python -m ruff format .`을 사용합니다. import 정리는 `python -m ruff check --select I --fix .`로 수행할 수 있습니다. Ruff 버전은 `pyproject.toml`에 고정되어 있어 로컬과 CI에서 같은 기준을 적용합니다.

GitHub Actions에는 Python 3.11·3.13에서 위 검사와 패키지 빌드를 수행하는 워크플로가 포함되어 있습니다. 카메라·GPIO·실제 모터가 없는 환경에서 실행하며, CI 성공은 실제 장비의 지연·스텝 정확도·방어 성능을 검증했다는 의미가 아닙니다. 빌드 파일은 `dist/`에 생성되며 워크플로는 이를 외부에 게시하지 않습니다.

## 변경 시 함께 확인할 항목

- **추적·예측:** 좌표와 시간 단위를 유지하고, 벽 충돌 전후·다중 반사·검출 누락처럼 변경이 영향을 주는 경우를 테스트합니다.
- **카메라:** 카메라를 사용하지 않는 모드에서 Picamera2 설치를 요구하지 않도록 합니다. 프레임 시각과 처리 시각을 구분합니다.
- **모터:** 기본 실행은 `dry-run`을 유지합니다. 실제 구동 조건, 원점 복귀, stale 상태 처리와 watchdog 동작을 바꾸면 프로토콜 문서도 갱신합니다.
- **문서·설정:** CLI 인자나 JSON 항목이 바뀌면 예제 설정 및 설명을 함께 수정합니다. 실제로 측정하지 않은 FPS·지연·정확도를 성능 결과로 기재하지 않습니다.

## 이슈와 Pull Request

오류는 실행 명령, 코드 버전, OS/Python/OpenCV 버전, 입력 모드와 재현 순서를 포함하여 등록합니다. `summary.json` 또는 오류 메시지가 원인 확인에 도움이 됩니다. Pull Request에는 해결하려는 문제, 변경 후 동작, 실행한 검증 결과를 적습니다. 관련 없는 서식 변경이나 대규모 재구성은 별도 변경으로 분리하면 검토하기 쉽습니다.

## 배포 메타데이터

패키지 버전을 바꿀 때는 `pyproject.toml`과 `airhockey/__init__.py`를 함께 갱신합니다. 개발 도구·GitHub Actions 버전 변경은 설치와 CI 명령을 다시 확인한 뒤 반영합니다. 저장소 주소·작성자·라이선스는 소유자가 확정한 정보만 추가합니다.
