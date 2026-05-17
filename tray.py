"""
cc2go 系统托盘 - 后台静默运行，托盘图标管理
"""

import os
import sys
import atexit
import threading
import webbrowser

import uvicorn
import pystray
from PIL import Image, ImageDraw, ImageFont

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from router import app, config, logger, VERSION

PID_FILE = os.path.join(os.path.dirname(__file__), "tray.pid")


def save_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except:
        pass


def load_icon():
    icon_path = os.path.join(os.path.dirname(__file__), "static", "favicon-32x32.png")
    if os.path.exists(icon_path):
        try:
            img = Image.open(icon_path)
            return img.resize((64, 64), Image.LANCZOS)
        except:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(0, 113, 227, 255))
    try:
        font = ImageFont.truetype("segoeui.ttf", 28)
    except:
        font = ImageFont.load_default()
    draw.text((14, 14), "c2", fill=(255, 255, 255, 255), font=font)
    return img


def open_admin():
    url = f"http://127.0.0.1:{config.router_port}"
    webbrowser.open(url)


def quit_app(icon, item):
    icon.stop()
    remove_pid()
    os._exit(0)


def run_server():
    host = config.router_host or "0.0.0.0"
    port = config.router_port or 4000
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except Exception as e:
        logger.error(f"Server error: {e}")
        os._exit(1)


def main():
    save_pid()
    atexit.register(remove_pid)

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    image = load_icon()
    menu = pystray.Menu(
        pystray.MenuItem("打开管理页", lambda: open_admin(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )

    icon = pystray.Icon("cc2go", image, f"cc2go v{VERSION}", menu)
    icon.run()


if __name__ == "__main__":
    main()
