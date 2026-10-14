#!/usr/bin/env python3
"""Remote Input: install dependencies (first run only) and launch the right server.

    python launch.py                 install if needed, then start (listens on the LAN: --host 0.0.0.0)
    python launch.py --port 9000     any other arguments are passed to the server
    python launch.py --reinstall     rebuild the virtual environment from scratch
    python launch.py --setup-only    install, but don't start the server

Works on Windows (server.py) and Linux (server_linux.py). Needs Python 3.8+.
Put it in the same folder as server.py, server_linux.py and index.html.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
WIN = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")

SERVER = HERE / ("server.py" if WIN else "server_linux.py")

# (import name, pip name, required?)
if WIN:
    PACKAGES = [("aiohttp", "aiohttp", True), ("segno", "segno", True),
                ("mss", "mss", False), ("PIL", "pillow", False), ("vgamepad", "vgamepad", False)]
else:
    PACKAGES = [("aiohttp", "aiohttp", True), ("segno", "segno", True), ("evdev", "evdev", True)]

UINPUT_HELP = """\
/dev/uinput is missing or not writable for your user, so the server can't create virtual devices.
One-time setup (then log out and back in):

    sudo modprobe uinput
    echo uinput | sudo tee /etc/modules-load.d/uinput.conf
    echo 'KERNEL=="uinput", GROUP="uinput", MODE="0660", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/99-uinput.rules
    sudo groupadd -f uinput
    sudo usermod -aG uinput "$USER"
    sudo udevadm control --reload-rules && sudo udevadm trigger
"""


def say(msg=""):
    print(msg, flush=True)


def die(msg, code=1):
    say(f"\nERROR: {msg}")
    if WIN and sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close...")  # keep the window open when double-clicked
    sys.exit(code)


def venv_python():
    return VENV / ("Scripts/python.exe" if WIN else "bin/python")


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], **kw)


def make_venv():
    say(f"Creating virtual environment in {VENV} ...")
    cmd = [sys.executable, "-m", "venv", VENV]
    if LINUX:
        cmd.insert(3, "--system-site-packages")  # reuse distro packages such as python-evdev
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(VENV, ignore_errors=True)
        hint = "\nOn Debian/Ubuntu try: sudo apt install python3-venv" if LINUX else ""
        die(f"couldn't create a virtual environment:\n{r.stderr.strip()}{hint}")
    run([venv_python(), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], capture_output=True)


def missing(names):
    """Import names that the venv's Python can't find."""
    code = ("import importlib.util, sys; "
            "print(' '.join(n for n in sys.argv[1:] if importlib.util.find_spec(n) is None))")
    r = run([venv_python(), "-c", code, *names], capture_output=True, text=True)
    return r.stdout.split()


def pip_install(pip_names):
    return run([venv_python(), "-m", "pip", "install", *pip_names]).returncode == 0


def install(reinstall):
    if reinstall and VENV.exists():
        say("Removing old virtual environment ...")
        shutil.rmtree(VENV, ignore_errors=True)
    if not venv_python().exists():
        make_venv()

    skip_file = VENV / "optional_failed.txt"  # optional packages that failed once: don't retry every launch
    skipped = set(skip_file.read_text().split()) if skip_file.exists() else set()

    absent = set(missing([imp for imp, _, _ in PACKAGES]))
    need_req = [pip for imp, pip, req in PACKAGES if imp in absent and req]
    need_opt = [(imp, pip) for imp, pip, req in PACKAGES if imp in absent and not req and imp not in skipped]

    if need_req:
        say(f"Installing: {', '.join(need_req)} ...")
        if not pip_install(need_req):
            hint = ("\nCheck your internet connection. If the error above mentions a compiler or Python.h,\n"
                    "evdev needs build tools and Python headers:\n"
                    "  Debian/Ubuntu: sudo apt install build-essential python3-dev\n"
                    "  Arch:          sudo pacman -S python-evdev   (then run with --reinstall)\n"
                    "  Fedora:        sudo dnf install gcc python3-devel") if LINUX else ""
            die(f"pip failed to install {', '.join(need_req)}.{hint}")

    for imp, pip in need_opt:  # optional: a failure only disables that feature
        say(f"Installing optional: {pip} ...")
        if not pip_install([pip]):
            skipped.add(imp)
            say(f"  (couldn't install {pip}; continuing without it)")
    if skipped:
        skip_file.write_text("\n".join(sorted(skipped)))
    elif skip_file.exists():
        skip_file.unlink()


def preflight():
    for f in (SERVER, HERE / "index.html"):
        if not f.exists():
            die(f"{f.name} not found next to launch.py ({HERE}).")
    if LINUX and os.geteuid() != 0:
        if not (os.path.exists("/dev/uinput") and os.access("/dev/uinput", os.W_OK)):
            say(UINPUT_HELP)
            die("uinput isn't ready (see above).")
    if not (WIN or LINUX):
        die(f"unsupported platform: {sys.platform}. This tool supports Windows and Linux.")


def main():
    args = sys.argv[1:]
    reinstall, setup_only = "--reinstall" in args, "--setup-only" in args
    args = [a for a in args if a not in ("--reinstall", "--setup-only")]

    if sys.version_info < (3, 8):
        die(f"Python 3.8 or newer is required (found {sys.version.split()[0]}).")
    preflight()
    install(reinstall)
    say("Dependencies ready.")
    if setup_only:
        return 0

    if not any(a == "--host" or a.startswith("--host=") for a in args):
        args = ["--host", "0.0.0.0", *args]  # reachable from your phone on the same network
    if WIN:
        say("(If Windows Firewall asks, allow Python on Private networks so your phone can connect.)")
    try:
        return subprocess.call([str(venv_python()), str(SERVER), *args], cwd=str(HERE))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 0
    if code and WIN and sys.stdin and sys.stdin.isatty():
        input("\nThe server stopped. Press Enter to close...")
    sys.exit(code)
