@echo off
chcp 65001 >nul
echo ========================================
echo   昔涟 QQ Bot - 环境安装
echo ========================================
echo.
echo 安装 Python 依赖...
D:\Python\python.exe -m pip install aiohttp httpx -q
echo.
echo ========================================
echo   配置步骤:
echo ========================================
echo.
echo 1. 编辑 qq_bot\config.json 填入 DeepSeek API Key
echo    (去 platform.deepseek.com 注册获取)
echo.
echo 2. 双击 napcat\start.bat 启动 NapCatQQ
echo    首次启动会弹出 QQ 登录窗口，扫码登录
echo.
echo 3. 将 napcat\napcat.json 复制到:
echo    %USERPROFILE%\.napcat\config\ 目录
echo    (如果目录不存在，先启动一次 NapCatQQ)
echo.
echo 4. 启动 QQ Bot:
echo    python qq_bot\run.py
echo.
echo ========================================
pause
