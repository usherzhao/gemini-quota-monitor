"""
Gemini Quota Monitor - Build Script
Packages the application into a standalone Windows executable.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

def main():
    project_dir = Path(__file__).resolve().parent
    os.chdir(project_dir)

    print("========================================================")
    print("  Gemini Quota Monitor - PyInstaller 打包构建")
    print("========================================================")
    print()

    # 1. Terminate any running instances
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", "GeminiQuotaMonitor.exe"], capture_output=True)

    # 2. Clean previous build artifacts
    for folder in ["build", "dist"]:
        p = project_dir / folder
        if p.exists():
            print(f"[*] 清理历史目录: {folder}...")
            shutil.rmtree(p, ignore_errors=True)

    # 2. Build command arguments (Single Standalone Executable)
    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "GeminiQuotaMonitor",
        "--icon", "assets/icon.ico",
        "--splash", "assets/splash.png",
        "--add-data", f"config.py{os.pathsep}.",
        "--add-data", f"assets{os.pathsep}assets",
        "--hidden-import", "PyQt6.QtCore",
        "--hidden-import", "PyQt6.QtGui",
        "--hidden-import", "PyQt6.QtWidgets",
        "--hidden-import", "PyQt6.QtNetwork",
        "--hidden-import", "curl_cffi",
        "--hidden-import", "requests",
        "--hidden-import", "urllib3",
        "--hidden-import", "PIL",
        "--hidden-import", "psutil",
        "main.py"
    ]

    print("[*] 正在执行单文件打包，请稍候...")
    result = subprocess.run(pyinstaller_args)

    if result.returncode == 0:
        exe_path = project_dir / "dist" / "GeminiQuotaMonitor.exe"
        print()
        print("========================================================")
        print("  [SUCCESS] 单文件打包成功！")
        print(f"  独立单文件路径: {exe_path}")
        print("  说明: 该 exe 为完整独立单文件，无需 _internal 文件夹，可直接单独拷贝或发送给朋友使用！")
        print("========================================================")
    else:
        print()
        print("========================================================")
        print("  [ERROR] 构建失败，请检查上方日志。")
        print("========================================================")

if __name__ == "__main__":
    main()
