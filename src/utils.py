"""
cc2go 公共工具函数
"""

import os
import sys



def get_base_dir() -> str:
    """项目根目录，兼容 PyInstaller onefile 打包"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
