@echo off
chcp 65001 >nul
echo ============================================
echo  Amazon 现金流模拟器 — 打包 EXE
echo ============================================
echo.

set PYTHON=py -3
set CTK_PATH=D:\codex\tools\Python313\Lib\site-packages\customtkinter

echo [1/3] 安装 PyInstaller...
%PYTHON% -m pip install pyinstaller -q

echo.
echo [2/3] 执行打包（单文件模式，无控制台窗口）...
%PYTHON% -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --name "Amazon现金流模拟器" ^
    --add-data "%CTK_PATH%;customtkinter" ^
    --hidden-import customtkinter ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    "D:\accio\e-commerce\amazon_cashflow\main_gui.py"

if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败，请检查上方错误信息。
    pause
    exit /b 1
)

echo.
echo [3/3] 打包完成！
echo EXE 文件路径：dist\Amazon现金流模拟器.exe
echo.
pause
