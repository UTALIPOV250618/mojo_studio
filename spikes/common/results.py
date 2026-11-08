"""Write spike results as JSON with machine / ffmpeg / git metadata."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def _run(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True
        )
    except Exception:  # noqa: BLE001 - metadata must never raise
        return None
    return proc.stdout.strip() or None


def _cpu() -> str | None:
    try:
        system = platform.system()
        if system == "Darwin":
            return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if sys.platform == "win32":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001 - metadata must never raise
        return None
    return platform.processor() or None


def _gpu() -> str | None:
    try:
        system = platform.system()
        if system == "Darwin":
            out = _run(["system_profiler", "SPDisplaysDataType"])
            if out:
                for line in out.splitlines():
                    if "Chipset Model:" in line:
                        return line.split(":", 1)[1].strip()
        elif system == "Windows":
            out = _run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController).Name -join '; '"]
            )  # fmt: skip
            return out
    except Exception:  # noqa: BLE001 - metadata must never raise
        return None
    return None


def machine_info() -> dict[str, Any]:
    try:
        ram = psutil.virtual_memory().total
    except Exception:  # noqa: BLE001 - metadata must never raise
        ram = None
    return {
        "platform": platform.platform(),
        "cpu": _cpu(),
        "ram_bytes": ram,
        "gpu": _gpu(),
    }


def ffmpeg_version() -> str | None:
    exe = shutil.which("ffmpeg")
    out = _run([exe, "-version"]) if exe else None
    return out.splitlines()[0] if out else None


def git_rev() -> str | None:
    root = str(Path(__file__).resolve().parents[2])
    rev = _run(["git", "-C", root, "rev-parse", "--short", "HEAD"])
    if rev is None:
        return None
    dirty = _run(["git", "-C", root, "status", "--porcelain"])
    return f"{rev}-dirty" if dirty else rev


def write_result(spike: str, name: str, data: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / spike / f"{name}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **data,
        "machine": machine_info(),
        "ffmpeg_version": ffmpeg_version(),
        "git_rev": git_rev(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
