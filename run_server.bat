@echo off
chcp 65001 > nul
cd /d "%~dp0"

set PORT=8000

if not exist ".venv\Scripts\python.exe" (
    echo [오류] 가상환경이 없습니다. README.md 의 설치 절차를 먼저 진행하세요.
    pause
    exit /b 1
)

echo 랙 재고 카운팅 서버를 시작합니다: http://localhost:%PORT%
echo 같은 네트워크의 스마트폰에서는 http://^<이 PC의 IP^>:%PORT% 로 접속하세요.
echo 종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo.

rem 서버가 뜰 시간을 준 뒤 브라우저 열기
start "" /b cmd /c "timeout /t 3 > nul & start http://localhost:%PORT%"

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%

pause
