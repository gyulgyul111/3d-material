@echo off
chcp 65001 > nul
cd /d "%~dp0"

rem 처음 클론한 PC에서 한 번 실행: 가상환경을 만들고 필요한 패키지를 설치합니다.

where python > nul 2> nul
if errorlevel 1 (
    echo [오류] Python 이 없습니다. https://www.python.org 에서 Python 3.12 이상을 설치하세요.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo 가상환경을 만듭니다...
    python -m venv .venv || goto :fail
)

echo 패키지를 설치합니다. PyTorch 는 용량이 커서 몇 분 걸릴 수 있습니다.
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
rem NVIDIA GPU 용 PyTorch (GPU 가 없어도 CPU 로 동작합니다)
".venv\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128 || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

echo.
echo 설치가 끝났습니다. run_server.bat 을 실행하세요.
pause
exit /b 0

:fail
echo.
echo [오류] 설치에 실패했습니다. 위의 메시지를 확인하세요.
pause
exit /b 1
