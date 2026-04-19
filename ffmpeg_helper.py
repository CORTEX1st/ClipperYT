"""
FFmpeg Helper — Cari ffmpeg di folder project dulu,
baru cari di PATH sistem Windows.
Taruh file ini di E:\project\ProjectAI\ffmpeg_helper.py
"""

import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

def get_ffmpeg():
    """Return path ke ffmpeg.exe"""
    local = BASE_DIR / "ffmpeg.exe"
    if local.exists():
        return str(local)
    return "ffmpeg"  # fallback ke PATH sistem

def get_ffprobe():
    """Return path ke ffprobe.exe"""
    local = BASE_DIR / "ffprobe.exe"
    if local.exists():
        return str(local)
    return "ffprobe"  # fallback ke PATH sistem

def run_ffmpeg(args: list, **kwargs) -> subprocess.CompletedProcess:
    """Jalankan ffmpeg dengan args"""
    return subprocess.run([get_ffmpeg()] + args, **kwargs)

def run_ffprobe(args: list, **kwargs) -> subprocess.CompletedProcess:
    """Jalankan ffprobe dengan args"""
    return subprocess.run([get_ffprobe()] + args, **kwargs)

def check_ffmpeg() -> bool:
    """Cek apakah ffmpeg tersedia"""
    try:
        result = subprocess.run(
            [get_ffmpeg(), "-version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False