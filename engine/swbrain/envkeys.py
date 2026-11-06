"""密钥只从环境变量读，但要读得到：Windows 上 setx 写进注册表（HKCU\\Environment）的变量，只有之后新开的进程才带；
从一个早就开着的终端 / Claude Code / 任务计划里再拉起的服务，进程环境里没有它，就会误判「没有 DeepSeek 密钥」而退到别的模型或模拟路况。
这里补一层：进程环境没有 → 查用户级 / 机器级注册表 → 找到就写回 os.environ，让 dsh 等子进程也继承。密钥永不落盘、不进日志。"""
from __future__ import annotations
import os
import sys

_REG = (("HKCU", "Environment"), ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))


def get(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if sys.platform == "win32":
        try:
            import winreg
            hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
            for hive, path in _REG:
                try:
                    with winreg.OpenKey(hives[hive], path) as k:
                        rv, _ = winreg.QueryValueEx(k, name)
                    rv = str(rv or "").strip()
                    if rv:
                        os.environ[name] = rv
                        return rv
                except OSError:
                    continue
        except Exception:
            pass
    return default


def present(name: str) -> bool:
    return bool(get(name))


def source(name: str) -> str | None:
    """诊断用：process / user / machine / None（不返回值本身）。"""
    if os.environ.get(name, "").strip():
        return "process"
    if sys.platform == "win32":
        try:
            import winreg
            hives = {"HKCU": (winreg.HKEY_CURRENT_USER, "user"), "HKLM": (winreg.HKEY_LOCAL_MACHINE, "machine")}
            for hive, path in _REG:
                try:
                    with winreg.OpenKey(hives[hive][0], path) as k:
                        rv, _ = winreg.QueryValueEx(k, name)
                    if str(rv or "").strip():
                        return hives[hive][1]
                except OSError:
                    continue
        except Exception:
            pass
    return None
