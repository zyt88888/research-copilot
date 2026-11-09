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

if exist "%~dp0research-copilot\web\package.json" (
  set "WEB=%~dp0research-copilot\web\"
) else (
  set "WEB=%~dp0web\"
)
cd /d "%WEB%"

if not exist node_modules (
  echo 首次运行，正在安装依赖，约 3-5 分钟...
  call npm install
)

echo 启动 Next.js（端口 3000），完成后浏览器打开 http://localhost:3000
call npm run dev
pause