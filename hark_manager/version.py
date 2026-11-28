"""App version (bump the patch number with every tested fix) and the commit the running code is on."""
import os
import subprocess
import sys

APP_VERSION = "1.0.6"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args):
    try:
        r = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True, timeout=4,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def commit_sha():
    """Short SHA of the checked-out commit; an EXE build reads BUILD_SHA instead. '' when unknown."""
    if not getattr(sys, "frozen", False):
        s = _git("rev-parse", "--short=7", "HEAD")
        if s:
            return s
    for base in (getattr(sys, "_MEIPASS", ""), ROOT):
        try:
            with open(os.path.join(base, "BUILD_SHA"), encoding="utf-8") as f:
                return f.read().strip()[:7]
        except OSError:
            pass
    return ""


def local_edits():
    """True when tracked files differ from the commit (the running code is not exactly that SHA)."""
    return not getattr(sys, "frozen", False) and bool(_git("status", "--porcelain", "--untracked-files=no"))


def version_text():
    sha = commit_sha()
    t = f"v{APP_VERSION}"
    if sha:
        t += f" ({sha}{' +local edits' if local_edits() else ''})"
    return t
