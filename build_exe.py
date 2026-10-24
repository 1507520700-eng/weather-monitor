# -*- coding: utf-8 -*-
"""
全国天气监测 - Windows 打包脚本
在 Windows 上运行本脚本, 用 PyInstaller 打包成独立 exe

用法:
  1. 安装依赖:  pip install -r requirements.txt pyinstaller
  2. 运行打包:  python build_exe.py
  3. 产物:      dist/WeatherMonitor.exe
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")

def main():
    # 检查依赖
    for pkg in ("PyQt6", "requests"):
        try:
            __import__(pkg)
        except ImportError:
            print(f"[错误] 缺少依赖 {pkg}, 请先运行: pip install -r requirements.txt")
            sys.exit(1)

    os.makedirs(DIST, exist_ok=True)

    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--windowed",            # 无控制台窗口 (GUI程序)
        "--name", "WeatherMonitor",
        # 打包数据文件: 放根目录, 与 _resource_path("china_cities.json") 对应
        "--add-data", f"{os.path.join(SRC, 'china_cities.json')};.",
        "--paths", SRC,
        "--hidden-import", "weather_data",
        "--hidden-import", "history_store",
        "--hidden-import", "locator",
        os.path.join(SRC, "main.py"),
    ]
    print("开始打包...")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        exe = os.path.join(DIST, "WeatherMonitor.exe")
        if os.path.exists(exe):
            print(f"\n✅ 打包成功: {exe}")
        else:
            print("\n打包完成, 但未找到 exe, 请检查 dist 目录")
    else:
        print(f"\n❌ 打包失败, 退出码 {result.returncode}")
        sys.exit(1)

if __name__ == "__main__":
    main()
