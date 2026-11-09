# GitHub에 처음 올리기

이 안내는 **이 프로젝트 폴더를 새 저장소로 올리는 경우**를 대상으로 합니다. 이미 팀 저장소가 있다면 그 저장소를 clone한 뒤 브랜치에서 파일을 추가하는 기존 협업 흐름을 따르세요. 아래 명령은 사용자가 자신의 계정에서 실행하는 절차이며, 전달본에서 원격 저장소 생성이나 push를 수행한 것은 아닙니다.

## 1. 프로젝트 폴더 확인

압축을 풀고 `README.md`, `pyproject.toml`, `airhockey/`, `.github/`가 있는 폴더로 이동합니다.

```bash
cd ~/airhockey
git init -b main
git status --short
```

예제 설정은 `config/example.json`으로 공유합니다. 실제 치수와 카메라 보정이 들어 있는 `config/local.json`, `calibration/`, `runs/`, 녹화 영상은 `.gitignore`에 의해 제외됩니다. Git 추적 제외는 앞으로 추가되는 파일에 적용되며, 이미 커밋한 파일을 자동으로 지우지는 않습니다.

## 2. 로컬 검사

PC 개발 환경에서는 README의 가상환경을 활성화한 상태에서 실행합니다.

```bash
python -m pip install -e '.[headless,dev]'
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
python -m build
```

빌드된 `dist/`와 `.venv/`는 소스 저장소에 포함하지 않습니다. 원본 영상 대신 필요한 경우 개인 정보가 없는 작은 합성 예시와 재현 명령을 사용합니다.

## 3. 첫 커밋

처음 Git을 사용하는 컴퓨터에서 이름·이메일 설정을 요구하면, 다음 두 값을 본인의 커밋 작성자 정보로 바꿉니다. 아래는 현재 저장소에만 적용하는 설정입니다.

```bash
git config user.name "본인의 이름"
git config user.email "본인의 GitHub 커밋 이메일"
```

파일 목록을 확인하고 커밋합니다.

```bash
git add .
git diff --cached --stat
git status --short
git commit -m "feat: add camera-based air hockey defense prototype"
```

`.github/workflows/ci.yml`과 `.gitignore`도 포함해야 합니다. 파일 관리자의 일반 선택으로 숨겨진 파일이 빠지는 일을 피하려면 터미널에서 `git add .`를 사용하고 목록을 확인하세요.

## 4. 새 원격 저장소 연결

GitHub에서 빈 저장소를 만듭니다. 저장소 이름 예시는 `vision-air-hockey`입니다. 공개 범위는 팀의 사용 목적에 맞춰 선택합니다. 로컬에 README와 gitignore가 이미 있으므로 새 원격 저장소에서 README·gitignore·license로 초기화하지 않고 연결하는 절차를 사용합니다. [GitHub 공식 안내](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github).

아래 주소의 `YOUR_ACCOUNT`와 저장소 이름을 실제 값으로 바꿉니다.

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/vision-air-hockey.git
git remote -v
git push -u origin main
```

이미 `origin`이 있는 저장소라면 새 remote를 덮어쓰지 말고 `git remote -v`로 대상부터 확인하세요. 인증은 본인의 GitHub 설정을 사용합니다. 토큰을 코드·원격 URL·README에 적어 넣지 않습니다.

## 5. 업로드 후 확인

저장소 첫 화면에 README가 표시되는지, 문서와 그림 링크가 열리는지 확인합니다. Actions 탭에서 **Python checks**의 Python 3.11·3.13 작업 결과를 확인합니다. 이 워크플로는 테스트와 빌드만 수행하며 패키지 게시나 모터 구동은 하지 않습니다.

GitHub의 Description에 사용할 수 있는 문구:

> Camera-based puck tracking, wall-reflection trajectory prediction, and one-axis air-hockey defense for Raspberry Pi.

Topics 예시: `raspberry-pi`, `opencv`, `computer-vision`, `air-hockey`, `robotics`, `picamera2`, `embedded-systems`.

배포 라이선스는 아직 지정하지 않았습니다. 연구실·팀의 배포 방침과 포함하는 외부 구성 요소를 확인해 `LICENSE`를 추가하고 README의 라이선스 절도 함께 갱신합니다. 저장소 생성만으로 임의의 작성자나 권리자를 문서에 넣지 않습니다.
