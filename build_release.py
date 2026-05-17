"""
cc2go Release 打包脚本
使用 PyInstaller 打包为独立可执行文件，无需 Python 环境
"""

import os
import sys
import shutil
import zipfile
import subprocess
from pathlib import Path

from src.router import VERSION

PROJECT_DIR = Path(__file__).parent
DIST_DIR = PROJECT_DIR / "dist"
BUILD_DIR = PROJECT_DIR / "build"
RELEASE_DIR = PROJECT_DIR / "release"
RELEASE_ZIP = PROJECT_DIR / f"cc2go-v{VERSION}-windows.zip"

REQUIRED_FILES = [
    "static",
    ".env.example",
]


def clean():
    """清理旧的构建产物"""
    for d in [DIST_DIR, BUILD_DIR, RELEASE_DIR]:
        if d.exists():
            shutil.rmtree(d)
            print(f"已清理: {d}")
    if RELEASE_ZIP.exists():
        RELEASE_ZIP.unlink()
        print(f"已删除: {RELEASE_ZIP}")


def build_exe(name, entry_point, icon=None):
    """使用 PyInstaller 打包单个 exe"""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", name,
        "--onefile",
        "--windowed" if "tray" in name else "--console",
        "--add-data", f"static{os.pathsep}static",
        "--hidden-import", "httpx",
        "--hidden-import", "requests",
        "--hidden-import", "pystray",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL._imaging",
        "--hidden-import", "webbrowser",
        "--hidden-import", "fastapi",
        "--hidden-import", "uvicorn",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.auto",
        "--hidden-import", "python_dotenv",
    ]
    if icon:
        cmd.extend(["--icon", str(icon)])
    cmd.append(str(entry_point))

    print(f"\n>>> 打包 {name}...")
    result = subprocess.run(cmd, cwd=PROJECT_DIR)
    if result.returncode != 0:
        print(f"错误: {name} 打包失败")
        sys.exit(1)
    print(f"成功: {name}")


def collect_release():
    """收集所有文件到 release 目录"""
    RELEASE_DIR.mkdir(exist_ok=True)

    # 复制 exe
    for exe in ["cc2go.exe", "cc2go-tray.exe"]:
        src = DIST_DIR / exe
        if src.exists():
            shutil.copy2(src, RELEASE_DIR / exe)
            print(f"已复制: {exe}")
        else:
            print(f"警告: {exe} 不存在于 dist/")

    # 复制其他文件
    for item in REQUIRED_FILES:
        src = PROJECT_DIR / item
        dst = RELEASE_DIR / item
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            print(f"已复制目录: {item}")
        elif src.exists():
            shutil.copy2(src, dst)
            print(f"已复制: {item}")
        else:
            print(f"警告: 缺少文件 {item}，发布包可能不完整")

    # 创建快捷批处理
    (RELEASE_DIR / "start.bat").write_text(
        '@echo off\ncd /d "%~dp0"\nstart "" cc2go.exe\n',
        encoding="utf-8"
    )
    (RELEASE_DIR / "start_bg.bat").write_text(
        '@echo off\ncd /d "%~dp0"\nstart "" cc2go-tray.exe\n',
        encoding="utf-8"
    )
    (RELEASE_DIR / "stop.bat").write_text(
        '@echo off\r\n'
        'chcp 65001 > nul\r\n'
        'cd /d "%~dp0"\r\n'
        '\r\n'
        'if exist cc2go.pid (\r\n'
        '    set /p PID=<cc2go.pid\r\n'
        '    taskkill /f /pid %PID% 2>nul\r\n'
        '    del cc2go.pid 2>nul\r\n'
        '    echo cc2go stopped.\r\n'
        ') else (\r\n'
        '    taskkill /f /im cc2go.exe 2>nul\r\n'
        '    taskkill /f /im cc2go-tray.exe 2>nul\r\n'
        '    echo cc2go stopped.\r\n'
        ')\r\n'
        'pause\r\n',
        encoding="utf-8"
    )

    # 创建使用说明
    readme = f"""# cc2go v{VERSION}

Claude Code → OpenCode Go 适配器

## 快速开始

1. 将本文件夹放到任意位置（建议不要放在 C:\\Program Files）
2. 复制 `.env.example` 为 `.env`，填入你的 OpenCode Go API Key
3. 双击 `start_bg.bat` 启动（系统托盘运行，不弹窗口）
4. 双击托盘图标打开管理页面 `http://localhost:4000`
5. 在管理页面选择模型即可使用

## Claude Code 配置

| 配置项 | 值 |
|--------|-----|
| Base URL | `http://localhost:4000` |
| API Key | `sk-litellm-local` |

## 文件说明

| 文件 | 说明 |
|------|------|
| `cc2go.exe` | 前台模式（显示终端窗口，适合调试） |
| `cc2go-tray.exe` | 托盘模式（后台运行，推荐） |
| `start.bat` | 启动前台模式 |
| `start_bg.bat` | 启动托盘模式 |
| `stop.bat` | 停止运行 |
| `.env` | 配置文件（需自行创建） |

## 常见问题

- **托盘图标不显示**：检查 Windows 通知区域设置，确保 cc2go-tray.exe 未被隐藏
- **端口被占用**：在 `.env` 中修改 `ROUTER_PORT` 为其他端口
- **杀毒软件拦截**：PyInstaller 打包的程序可能被误报，请添加信任
"""
    (RELEASE_DIR / "README.md").write_text(readme, encoding="utf-8")
    print("已创建 README.md")


def create_zip():
    """创建发布 ZIP"""
    print(f"\n>>> 创建 {RELEASE_ZIP.name}...")
    with zipfile.ZipFile(RELEASE_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(RELEASE_DIR):
            for f in files:
                full = Path(root) / f
                arcname = full.relative_to(RELEASE_DIR.parent)
                zf.write(full, arcname)
                print(f"  已打包: {arcname}")

    size = RELEASE_ZIP.stat().st_size / (1024 * 1024)
    print(f"\n成功: {RELEASE_ZIP.name} ({size:.1f} MB)")


def main():
    print(f"cc2go Release Builder v{VERSION}")
    print("=" * 50)

    # 检查依赖
    try:
        import PyInstaller
    except ImportError:
        print("错误: 请先安装 PyInstaller")
        print("  pip install pyinstaller")
        sys.exit(1)

    clean()

    # 打包两个 exe
    build_exe("cc2go", "src/router.py")
    build_exe("cc2go-tray", "src/tray.py", icon="static/favicon.ico")

    # 收集发布文件
    collect_release()

    # 创建 ZIP
    create_zip()

    print("\n" + "=" * 50)
    print("打包完成！")
    print(f"发布文件: {RELEASE_ZIP}")
    print(f"解压后大小约: {sum(f.stat().st_size for f in RELEASE_DIR.rglob('*') if f.is_file()) / (1024*1024):.1f} MB")


if __name__ == "__main__":
    main()
