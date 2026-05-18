@echo off
chcp 65001 >nul
echo ============================================
echo  Amazon 现金流模拟器 — 依赖安装 & 运行
echo ============================================
echo.

echo [1/2] 安装 Python 依赖...
py -3 -m pip install customtkinter matplotlib numpy
if %errorlevel% neq 0 (
    echo [错误] 安装失败，请确认 Python 3.x 已安装并在 PATH 中。
    pause
    exit /b 1
)

echo.
echo [2/2] 启动程序...
py -3 "D:\accio\e-commerce\amazon_cashflow\main_gui.py"

pause
