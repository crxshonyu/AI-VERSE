#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'Raspberry Pi OS용 의존성 설치입니다. Ubuntu용 설치 스크립트가 아닙니다.'
if [[ -r /proc/device-tree/model ]]; then
    tr -d '\0' </proc/device-tree/model
    printf '\n'
fi
sudo apt update
sudo apt install -y --no-install-recommends \
    python3-picamera2 python3-opencv python3-numpy python3-serial \
    python3-gpiozero python3-lgpio
python3 - <<'PY'
import cv2
import numpy
import serial
from picamera2 import Picamera2
print('OpenCV:', cv2.__version__)
print('NumPy:', numpy.__version__)
print('pyserial:', serial.VERSION)
print('Picamera2 import: OK')
PY
printf '%s\n' '설치 완료. 프로젝트 폴더에서 python3 -m airhockey init-config 를 실행하세요.'
