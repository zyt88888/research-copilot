@echo off
setlocal
cd /d "%~dp0"

set "NODE_DIR="
for %%D in ("D:\AI" "D:\zero\Application\components\Node" "C:\Program Files\nodejs" "C:\Program Files (x86)\nodejs") do (
  if exist "%%~D\npm.cmd" if not defined NODE_DIR set "NODE_DIR=%%~D"
)
if not defined NODE_DIR (
  echo [错误] 没找到 npm.cmd，请先安装 Node.js。
  pause
  exit /b 1
)
set "PATH=%NODE_DIR%;%PATH%"

if exist "%~dp0research-copilot\backend\app\main.py" (
  set "ROOT=%~dp0research-copilot\"
) else (
  set "ROOT=%~dp0"
)
if not exist "%ROOT%backend\app\main.py" (
  echo [错误] 没找到 backend\app\main.py，请确认本 bat 与 research-copilot 在同一目录。
  pause
  exit /b 1
)

echo 后端目录：%ROOT%
echo 正在启动后端（端口 8000）...
start "科研小助手-后端" cmd /k "cd /d %ROOT%backend && set PYTHONUTF8=1& python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 5 >nul

if not exist "%ROOT%web\node_modules" (
  echo 首次运行，正在安装前端依赖，约 3-5 分钟...
  start "科研小助手-前端" cmd /k "cd /d %ROOT%web && npm install && npm run dev"
) else (
  start "科研小助手-前端" cmd /k "cd /d %ROOT%web && npm run dev"
)

echo 等待前端编译...
timeout /t 12 >nul
start http://localhost:3000
echo.
echo ============================================================
echo  启动完成
echo    后端文档 : http://localhost:8000/docs
echo    前端页面 : http://localhost:3000
echo  请保持「科研小助手-后端」「科研小助手-前端」两个黑窗口开着，
echo  关掉它们服务就停了（浏览器会出现 2 errors、文库变 0 篇）。
echo ============================================================
pause