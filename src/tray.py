"""
cc2go 系统托盘 - 后台静默运行，托盘图标管理
"""

import os
import sys
import time
import signal
import atexit
import threading
import webbrowser
import logging

import uvicorn
import pystray
from PIL import Image, ImageDraw, ImageFont


from router import app, config, logger, VERSION  # noqa: E402
from utils import get_base_dir

# get_static_dir 已在 router.py 定义，此处直接使用
def get_static_dir():
    """静态资源目录，对于 PyInstaller 优先使用打包内的资源"""
    if getattr(sys, 'frozen', False):
        meipass = os.path.join(sys._MEIPASS, "static")
        if os.path.exists(meipass):
            return meipass
    return os.path.join(get_base_dir(), "static")

PID_FILE = os.path.join(get_base_dir(), "data", "cc2go.pid")


def save_pid() -> None:
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def kill_old_process() -> None:
    """检查端口是否被占用，杀掉旧进程"""
    import socket
    port = config.router_port or 4001
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        return
    except OSError:
        sock.close()

    # 端口被占用，尝试杀旧进程
    print(f"[cc2go] 端口 {port} 被占用，尝试停止旧进程...")
    logger.info(f"端口 {port} 被占用，尝试停止旧进程")

    # 1. 先尝试 PID 文件里的进程
    old_pid = None
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
    except Exception:
        pass

    if old_pid:
        _try_kill_pid(old_pid)

    # 2. 通过 netstat 找到占用端口的进程
    import subprocess
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                if pid != os.getpid():
                    _try_kill_pid(pid)
    except Exception as e:
        logger.warning(f"端口扫描失败: {e}")
        pass

    # 等待端口释放
    for _ in range(10):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.close()
            print(f"[cc2go] 端口 {port} 已释放")
            logger.info(f"端口 {port} 已释放")
            return
        except OSError:
            sock.close()
        time.sleep(0.5)

    print(f"[cc2go] 警告: 端口 {port} 仍被占用，启动可能失败")
    logger.warning(f"端口 {port} 仍被占用，启动可能失败")


def _try_kill_pid(pid) -> None:
    """尝试杀掉指定 PID 的进程"""
    if pid == os.getpid():
        return
    print(f"[cc2go] 停止旧进程 PID={pid}")
    logger.info(f"停止旧进程 PID={pid}")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        try:
            import subprocess
            subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                          capture_output=True, timeout=5)
        except Exception as e:
            logger.warning(f"taskkill 失败 PID={pid}: {e}")


def load_icon():
    icon_path = os.path.join(get_static_dir(), "favicon-32x32.png")
    if os.path.exists(icon_path):
        try:
            img = Image.open(icon_path)
            return img.resize((64, 64), Image.LANCZOS)
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(0, 113, 227, 255))
    try:
        font = ImageFont.truetype("segoeui.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((14, 14), "c2", fill=(255, 255, 255, 255), font=font)
    return img


def open_admin() -> None:
    url = f"http://127.0.0.1:{config.router_port}"
    webbrowser.open(url, new=0)


def build_tray_menu():
    """构建托盘菜单（仅保留管理页入口和退出）"""
    return pystray.Menu(
        pystray.MenuItem("打开管理页", lambda: open_admin(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )


def quit_app(icon, item) -> None:
    icon.stop()
    remove_pid()
    sys.exit(0)


def run_server() -> None:
    host = config.router_host or "0.0.0.0"
    port = config.router_port or 4001
    logging.disable(logging.CRITICAL)
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning", log_config=None)
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


def _auto_open_admin() -> None:
    """延迟 2 秒后自动打开管理页"""
    time.sleep(2)
    try:
        url = f"http://127.0.0.1:{config.router_port}"
        webbrowser.open(url, new=0)
    except Exception:
        pass


def main() -> None:
    os.chdir(get_base_dir())
    kill_old_process()
    save_pid()
    atexit.register(remove_pid)

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # 启动后自动打开管理页
    threading.Thread(target=_auto_open_admin, daemon=True).start()

    image = load_icon()
    menu = build_tray_menu()

    icon = pystray.Icon("cc2go", image, f"cc2go v{VERSION}", menu)
    icon.run()


if __name__ == "__main__":
    main()