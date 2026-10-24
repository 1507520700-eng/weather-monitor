# -*- coding: utf-8 -*-
"""Build Windows EXE with PyInstaller (ASCII-only output)."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")


def main():
    for pkg in ("PyQt6", "requests"):
        try:
            __import__(pkg)
        except ImportError:
            print("[ERROR] missing dependency:", pkg)
            print("        run: pip install -r requirements.txt")
            sys.exit(1)

    os.makedirs(DIST, exist_ok=True)

    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile",             # 单文件模式: 只产出一个 exe
        "--windowed",
        "--name", "WeatherMonitor",
        "--add-data", os.path.join(SRC, "china_cities.json") + ";.",
        "--paths", SRC,
        "--hidden-import", "weather_data",
        "--hidden-import", "history_store",
        "--hidden-import", "locator",
        os.path.join(SRC, "main.py"),
    ]
    print("Starting PyInstaller build...")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        exe = os.path.join(DIST, "WeatherMonitor.exe")
        if os.path.exists(exe):
            print("[OK] Build success:", exe)
        else:
            print("[WARN] Build done but exe not found in dist/")
    else:
        print("[ERROR] Build failed, exit code:", result.returncode)
        sys.exit(1)


if __name__ == "__main__":
    main()
