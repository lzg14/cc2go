"""
cc2go 系统托盘 - 后台静默运行，托盘图标管理
"""

import os
import sys
import json
import atexit
import threading
import webbrowser
import urllib.request
import urllib.error

import uvicorn
import pystray
from PIL import Image, ImageDraw, ImageFont


def get_base_dir():
    """项目根目录，兼容 PyInstaller onefile 打包"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.chdir(get_base_dir())

from router import app, config, logger, VERSION

PID_FILE = os.path.join(get_base_dir(), "data", "cc2go.pid")


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
    icon_path = os.path.join(get_base_dir(), "static", "favicon-32x32.png")
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
    webbrowser.open(url, new=0)


def _api_request(method, path, data=None, timeout=3):
    """简单的同步 HTTP 请求（标准库 urllib，无外部依赖）"""
    url = f"http://127.0.0.1:{config.router_port}{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError):
        return None


def get_current_model():
    cfg = _api_request("GET", "/api/config")
    return cfg.get("selected_model", "") if cfg else ""


def get_models_list():
    cfg = _api_request("GET", "/api/config")
    return cfg.get("models", []) if cfg else []


def switch_model(model_name):
    _api_request("PUT", "/api/config", data={"selected_model": model_name}, timeout=5)


def build_model_menu():
    """构建模型切换子菜单"""
    models = get_models_list()

    def make_callback(name):
        def cb():
            switch_model(name)
        return cb

    items = []
    for name in sorted(models):
        items.append(
            pystray.MenuItem(
                name,
                make_callback(name),
                checked=lambda item, n=name: n == get_current_model(),
            )
        )
    return items


def build_tray_menu():
    """构建完整托盘菜单"""
    model_items = build_model_menu()

    return pystray.Menu(
        pystray.MenuItem("切换模型", pystray.Menu(*model_items)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开管理页", lambda: open_admin(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )


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
    menu = build_tray_menu()

    icon = pystray.Icon("cc2go", image, f"cc2go v{VERSION}", menu)
    icon.run()


if __name__ == "__main__":
    main()
