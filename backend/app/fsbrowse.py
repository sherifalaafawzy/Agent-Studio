"""Folder picking for the workspace setting.

Browsers never reveal real disk paths, but this API runs on the user's own machine, so it can:
  * open the native OS folder dialog (tkinter) on the user's desktop, and
  * list sub-folders for the in-app browser (fallback when no GUI dialog is available).
Only folder NAMES are listed (no file contents), and the API is bound to localhost.
"""
from __future__ import annotations

import os
import string
import threading
from pathlib import Path

_dialog_lock = threading.Lock()


def pick_folder(initial: str | None = None, title: str = "Choose the agents' workspace folder") -> str | None:
    """Native folder dialog. Returns the chosen absolute path, or None if cancelled. Raises RuntimeError if no GUI."""
    if not _dialog_lock.acquire(blocking=False):
        raise RuntimeError("a folder dialog is already open on your desktop")
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)           # in front of the browser
        root.update()
        try:
            start = initial if initial and Path(initial).is_dir() else str(Path.home())
            p = filedialog.askdirectory(parent=root, initialdir=start, title=title, mustexist=True)
        finally:
            root.destroy()
        return str(Path(p).resolve()) if p else None
    except ImportError as e:
        raise RuntimeError(f"no GUI toolkit available ({e})")
    except Exception as e:  # noqa: BLE001 - e.g. no display: the UI falls back to the in-app browser
        raise RuntimeError(f"could not open the folder dialog: {e}")
    finally:
        _dialog_lock.release()


def roots() -> list[dict]:
    if os.name == "nt":
        out = []
        for d in string.ascii_uppercase:
            p = f"{d}:\\"
            if os.path.exists(p):
                out.append(dict(name=f"{d}:", path=p))
        return out
    return [dict(name="/", path="/")]


def list_dirs(path: str | None) -> dict:
    """Sub-folders of `path` (or the drives / filesystem root + home when empty)."""
    home = str(Path.home())
    if not path:
        return dict(path=None, parent=None, dirs=[dict(name="Home", path=home)] + roots(), home=home)
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise ValueError("not an existing absolute folder")
    p = p.resolve()
    dirs = []
    try:
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            try:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "$")) and e.name not in ("System Volume Information",):
                    dirs.append(dict(name=e.name, path=str(Path(e.path))))
            except OSError:
                continue
    except PermissionError:
        raise ValueError("no permission to read that folder")
    parent = str(p.parent) if p.parent != p else None
    return dict(path=str(p), parent=parent, dirs=dirs[:500], home=home)
