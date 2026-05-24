"""
RTK (Rust Token Killer) 集成模块
自动下载、安装、注入 Shell Hook，压缩 CLI 输出减少 token 消耗
跨平台支持：Windows/macOS(Intel+Apple Silicon)/Linux(x86_64+ARM64)
"""

from __future__ import annotations
import os
import sys
import shutil
import logging
import platform
import subprocess
from typing import Optional

# 保证从项目根可导入 src.utils
_src_dir = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.dirname(_src_dir)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from src.utils import get_base_dir  # noqa: E402

logger = logging.getLogger("llm_router")

RTK_VERSION = "0.41.0"
RTK_BIN_DIR = os.path.join(get_base_dir(), "bin")
RTK_EXE_NAME = "rtk.exe" if platform.system() == "Windows" else "rtk"
RTK_EXE_PATH = os.path.join(RTK_BIN_DIR, RTK_EXE_NAME)

# 各平台对应的归档文件名和内部二进制名
PLATFORM_TABLE = [
    ("Windows", "x86_64",    "rtk-x86_64-pc-windows-msvc.zip",      "rtk.exe"),
    ("Darwin",  "arm64",     "rtk-aarch64-apple-darwin.tar.gz",      "rtk"),
    ("Darwin",  "x86_64",    "rtk-x86_64-apple-darwin.tar.gz",       "rtk"),
    ("Linux",   "x86_64",    "rtk-x86_64-unknown-linux-musl.tar.gz", "rtk"),
    ("Linux",   "aarch64",   "rtk-aarch64-unknown-linux-gnu.tar.gz", "rtk"),
]

def _platform_info() -> tuple[str, str, str]:
    """返回 (系统, 架构, 版本号)"""
    sys_name = platform.system()
    arch = platform.machine().lower()
    # 统一架构名
    arch_map = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64", "arm64": "arm64"}
    norm_arch = arch_map.get(arch, arch)
    for s, a, archive, _ in PLATFORM_TABLE:
        if s == sys_name and a == norm_arch:
            return sys_name, norm_arch, archive
    raise RuntimeError(f"不支持的平台: {sys_name} {arch}")

def _archives_dir() -> str:
    return os.path.join(get_base_dir(), "bin", "archives")

def rtk_download_url(mirror: str = "") -> tuple[str, str]:
    """返回 (下载 URL, 压缩包内二进制文件名)"""
    sys_name, arch, archive_name = _platform_info()
    binary_name = "rtk.exe" if sys_name == "Windows" else "rtk"
    if mirror:
        mirror = mirror.rstrip("/")
        url = f"{mirror}/rtk-ai/rtk/v{RTK_VERSION}/{archive_name}"
    else:
        url = f"https://github.com/rtk-ai/rtk/releases/download/v{RTK_VERSION}/{archive_name}"
    return url, binary_name

def download_rtk(mirror_urls: list[str]) -> Optional[str]:
    """依次尝试镜像源下载 RTK，成功返回 exe 路径，失败返回 None"""
    os.makedirs(RTK_BIN_DIR, exist_ok=True)
    os.makedirs(_archives_dir(), exist_ok=True)

    dl_url, binary_name = rtk_download_url()
    candidates = mirror_urls + [""]

    for mirror in candidates:
        if mirror:
            url, _ = rtk_download_url(mirror)
        else:
            url = dl_url

        archive_name = os.path.basename(url)
        archive_path = os.path.join(_archives_dir(), archive_name)

        logger.info(f"[RTK] 下载: {url}")
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "cc2go/0.8.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                with open(archive_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
        except Exception as e:
            logger.warning(f"[RTK] 下载失败 ({url}): {e}")
            continue

        try:
            if archive_name.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extract(binary_name, RTK_BIN_DIR)
            else:
                import tarfile
                with tarfile.open(archive_path, "r:gz") as tf:
                    for member in tf.getmembers():
                        if member.name.endswith(f"/{binary_name}") or member.name == binary_name:
                            tf.extract(member, RTK_BIN_DIR)
                            extracted = os.path.join(RTK_BIN_DIR, member.name)
                            if extracted != RTK_EXE_PATH:
                                if os.path.exists(RTK_EXE_PATH):
                                    os.remove(RTK_EXE_PATH)
                                os.rename(extracted, RTK_EXE_PATH)
                            break

            if platform.system() != "Windows" and os.path.exists(RTK_EXE_PATH):
                os.chmod(RTK_EXE_PATH, 0o755)

            logger.info(f"[RTK] 安装成功: {RTK_EXE_PATH}")
            try:
                os.remove(archive_path)
            except Exception:
                pass
            return RTK_EXE_PATH

        except Exception as e:
            logger.warning(f"[RTK] 解压失败 ({archive_name}): {e}")
            continue

    return None

def find_rtk() -> Optional[str]:
    """查找本地 RTK 二进制：PATH 优先，其次本地 bin/"""
    rtk_path = shutil.which(RTK_EXE_NAME)
    if rtk_path:
        logger.debug(f"[RTK] 在 PATH 中发现: {rtk_path}")
        return rtk_path
    if os.path.exists(RTK_EXE_PATH):
        logger.debug(f"[RTK] 在本地目录发现: {RTK_EXE_PATH}")
        return RTK_EXE_PATH
    return None

def get_rtk_version(rtk_path: str) -> str:
    try:
        result = subprocess.run([rtk_path, "--version"], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or result.stderr.strip() or "unknown"
    except Exception:
        return "unknown"

def install_hook() -> bool:
    """注入 Shell alias，让 Claude Code 的命令自动走 RTK"""
    rtk_path = find_rtk()
    if not rtk_path:
        logger.warning("[RTK] 未找到 RTK 二进制，无法注入 Hook")
        return False
    rtk_dir = os.path.dirname(rtk_path)

    # 确保 RTK 在 PATH 中
    if platform.system() == "Windows":
        profile_path = os.path.expanduser("~\\Documents\\WindowsPowerShell\\Microsoft.PowerShell_profile.ps1")
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)
        add_line = f'\n$env:Path = "{rtk_dir};$env:Path"\n'
        if os.path.exists(profile_path):
            with open(profile_path, "r") as f:
                if add_line.strip() in f.read():
                    pass  # 已存在
                else:
                    with open(profile_path, "a") as f:
                        f.write(add_line)
        else:
            with open(profile_path, "a") as f:
                f.write(add_line)
        logger.info(f"[RTK] 已添加 PATH: {profile_path}")
    else:
        for rc in [os.path.expanduser("~/.bashrc"), os.path.expanduser("~/.zshrc")]:
            if os.path.exists(rc):
                add_line = f'\nexport PATH="$PATH:{rtk_dir}"\n'
                with open(rc, "r") as f:
                    if add_line.strip() in f.read():
                        break
                with open(rc, "a") as f:
                    f.write(add_line)
                logger.info(f"[RTK] 已添加 PATH: {rc}")

    logger.info(f"[RTK] Hook 注入完成 (rtk: {rtk_path})")
    return True

def setup_rtk(mirror_urls: list[str] | None = None) -> dict:
    """
    完整 RTK 安装流程
    返回: {"status": "ok"|"failed", "rtk_path": "...", "version": "...", "message": "..."}
    """
    if mirror_urls is None:
        mirror_urls = ["https://mirrors.tuna.tsinghua.edu.cn/github-release"]

    existing = find_rtk()
    if existing:
        version = get_rtk_version(existing)
        logger.info(f"[RTK] 已安装: {existing} ({version})")
        install_hook()
        return {"status": "ok", "rtk_path": existing, "version": version, "message": "RTK 已就绪"}

    logger.info("[RTK] 未找到本地 RTK，开始下载...")
    rtk_path = download_rtk(mirror_urls)
    if not rtk_path:
        logger.warning("[RTK] 所有下载源均失败，跳过 RTK 安装")
        return {"status": "failed", "rtk_path": "", "version": "", "message": "RTK 下载失败，可手动下载 rtk.exe 放到 bin/ 目录"}

    install_hook()
    version = get_rtk_version(rtk_path)
    return {"status": "ok", "rtk_path": rtk_path, "version": version, "message": "RTK 安装完成"}

def get_status() -> dict:
    rtk_path = find_rtk()
    if not rtk_path:
        return {"installed": False, "rtk_path": "", "version": ""}
    return {"installed": True, "rtk_path": rtk_path, "version": get_rtk_version(rtk_path)}
