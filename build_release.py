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
        "--windowed",
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
        "--hidden-import", "dotenv",
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
    RELEASE_DIR.mkdir(exist_ok=True)

    (RELEASE_DIR / "data").mkdir(exist_ok=True)
    (RELEASE_DIR / "logs").mkdir(exist_ok=True)

    src_exe = DIST_DIR / "cc2go.exe"
    if src_exe.exists():
        shutil.copy2(src_exe, RELEASE_DIR / "cc2go.exe")
        print("已复制: cc2go.exe")
    else:
        print("警告: cc2go.exe 不存在于 dist/")

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

    readme = f"""# cc2go v{VERSION}

Claude Code → OpenCode Go 适配器

## 快速开始

1. 将本文件夹放到任意位置（建议不要放在 C:\\Program Files）
2. 复制 `.env.example` 为 `.env`，填入你的 OpenCode Go API Key
3. 双击 `cc2go.exe` 启动（系统托盘运行，自动打开管理页面）
4. 在管理页面选择模型即可使用
5. 右键托盘图标 → 退出 停止运行

## Claude Code 配置

| 配置项 | 值 |
|--------|-----|
| Base URL | `http://localhost:4000` |
| API Key | `sk-litellm-local` |

## 文件说明

| 文件/目录 | 说明 |
|-----------|------|
| `cc2go.exe` | 主程序（系统托盘运行，双击启动） |
| `.env` | 配置文件（需自行创建） |
| `data/` | 运行数据（PID 文件、统计、自定义模型配置，自动生成） |
| `logs/` | 日志目录（自动生成） |

## 如何停止

- 右键系统托盘图标 → 点击「退出」
- 或任务管理器结束 `cc2go.exe`

## 常见问题

- **托盘图标不显示**：检查 Windows 通知区域设置，确保 cc2go 未被隐藏
- **端口被占用**：在 `.env` 中修改 `ROUTER_PORT` 为其他端口
- **杀毒软件拦截**：PyInstaller 打包的程序可能被误报，请添加信任
"""
    (RELEASE_DIR / "README.md").write_text(readme, encoding="utf-8")
    print("已创建 README.md")


def create_zip():
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

    try:
        import PyInstaller
    except ImportError:
        print("错误: 请先安装 PyInstaller")
        print("  pip install pyinstaller")
        sys.exit(1)

    clean()

    build_exe("cc2go", "src/tray.py", icon="static/favicon.ico")

    collect_release()

    create_zip()

    print("\n" + "=" * 50)
    print("打包完成！")
    print(f"发布文件: {RELEASE_ZIP}")
    print(f"解压后大小约: {sum(f.stat().st_size for f in RELEASE_DIR.rglob('*') if f.is_file()) / (1024*1024):.1f} MB")


if __name__ == "__main__":
    main()
