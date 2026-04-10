"""Cross-platform clipboard copy."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    if sys.platform == "linux":
        # Wayland — preferred on modern Linux when running under a Wayland session
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            try:
                proc = subprocess.run(
                    ["wl-copy"], input=text.encode(), capture_output=True, timeout=5,
                )
                if proc.returncode == 0:
                    return True
            except Exception:
                pass

        # X11 fallback
        for cmd in ("xclip -selection clipboard", "xsel --clipboard --input"):
            binary = cmd.split()[0]
            if shutil.which(binary):
                try:
                    proc = subprocess.run(cmd.split(), input=text.encode(), capture_output=True, timeout=5)
                    return proc.returncode == 0
                except Exception:
                    continue

    if sys.platform == "darwin":
        try:
            proc = subprocess.run(["pbcopy"], input=text.encode(), capture_output=True, timeout=5)
            return proc.returncode == 0
        except Exception:
            pass

    if shutil.which("clip.exe"):
        try:
            proc = subprocess.run(["clip.exe"], input=text.encode(), capture_output=True, timeout=5)
            return proc.returncode == 0
        except Exception:
            pass

    return False
