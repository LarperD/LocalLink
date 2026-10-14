#!/usr/bin/env python3
"""LocalLink launcher: sets everything up on first run, then starts the right server.

    python launch.py                 install if needed, then start (reachable from your phone: --host 0.0.0.0)
    python launch.py --local         only this computer can connect (--host 127.0.0.1)
    python launch.py --port 9000     any other arguments are passed on to the server
    python launch.py --reinstall     rebuild the virtual environment from scratch
    python launch.py --setup-only    install, but don't start the server
    python launch.py --help          this text

Works on Windows (server.py) and Linux (server_linux.py). Needs Python 3.8+.
Keep it in the same folder as server.py, server_linux.py and index.html.
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
                ("mss", "mss", False), ("PIL", "pillow", False), ("vgamepad", "vgamepad", False),
                ("pyaudiowpatch", "PyAudioWPatch", False)]
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

# ---------- output helpers ----------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles: no UnicodeEncodeError
except Exception:
    pass
if WIN:
    os.system("")  # switches the Windows console to ANSI colour mode
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(code, text):
    return f"\x1b[{code}m{text}\x1b[0m" if COLOR else text


def say(msg=""):
    print(msg, flush=True)


def step(msg):
    say(f"{paint('36', '›')} {msg}")


def good(msg):
    say(f"{paint('32', '✓')} {msg}")


def warn(msg):
    say(f"{paint('33', '!')} {msg}")


def pause_if_windows(msg):
    if WIN and sys.stdin and sys.stdin.isatty():
        input(f"\n{msg}")  # keeps the window open when double-clicked


def die(msg, code=1):
    say(f"\n{paint('31;1', 'ERROR:')} {msg}")
    pause_if_windows("Press Enter to close...")
    sys.exit(code)


# ---------- virtual environment ----------
def venv_python():
    return VENV / ("Scripts/python.exe" if WIN else "bin/python")


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], **kw)


def make_venv():
    step(f"Creating virtual environment ({VENV.name}) ...")
    cmd = [sys.executable, "-m", "venv", VENV]
    if LINUX:
        cmd.insert(3, "--system-site-packages")  # reuse distro packages such as python-evdev
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(VENV, ignore_errors=True)
        hint = "\nOn Debian/Ubuntu try: sudo apt install python3-venv" if LINUX else ""
        die(f"couldn't create a virtual environment:\n{r.stderr.strip()}{hint}")
    run([venv_python(), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", "pip"],
        capture_output=True)


def missing(names):
    """Import names that the venv's Python can't find."""
    code = ("import importlib.util, sys; "
            "print(' '.join(n for n in sys.argv[1:] if importlib.util.find_spec(n) is None))")
    r = run([venv_python(), "-c", code, *names], capture_output=True, text=True)
    return r.stdout.split()


def pip_install(pip_names):
    """Quiet install; pip's own output is only shown if it fails."""
    r = run([venv_python(), "-m", "pip", "install", "--disable-pip-version-check", *pip_names],
            capture_output=True, text=True)
    if r.returncode != 0:
        say("\n".join((r.stdout + r.stderr).strip().splitlines()[-15:]))
    return r.returncode == 0


def install(reinstall):
    if reinstall and VENV.exists():
        step("Removing old virtual environment ...")
        shutil.rmtree(VENV, ignore_errors=True)
    if not venv_python().exists():
        make_venv()

    skip_file = VENV / "optional_failed.txt"  # optional packages that failed once: don't retry every launch
    skipped = set(skip_file.read_text().split()) if skip_file.exists() else set()

    absent = set(missing([imp for imp, _, _ in PACKAGES]))
    need_req = [pip for imp, pip, req in PACKAGES if imp in absent and req]
    need_opt = [(imp, pip) for imp, pip, req in PACKAGES if imp in absent and not req and imp not in skipped]

    if need_req:
        step(f"Installing {', '.join(need_req)} (first run only) ...")
        if not pip_install(need_req):
            hint = ("\nCheck your internet connection. If the error above mentions a compiler or Python.h,\n"
                    "evdev needs build tools and Python headers:\n"
                    "  Debian/Ubuntu: sudo apt install build-essential python3-dev\n"
                    "  Arch:          sudo pacman -S python-evdev   (then run with --reinstall)\n"
                    "  Fedora:        sudo dnf install gcc python3-devel") if LINUX else ""
            die(f"pip failed to install {', '.join(need_req)}.{hint}")

    for imp, pip in need_opt:  # optional: a failure only disables that feature
        step(f"Installing optional {pip} ...")
        if not pip_install([pip]):
            skipped.add(imp)
            warn(f"couldn't install {pip}; continuing without it (retry with --reinstall)")
    if skipped:
        skip_file.write_text("\n".join(sorted(skipped)))
    elif skip_file.exists():
        skip_file.unlink()


# ---------- checks ----------
def preflight():
    if not (WIN or LINUX):
        die(f"unsupported platform: {sys.platform}. This tool supports Windows and Linux.")
    for f in (SERVER, HERE / "index.html"):
        if not f.exists():
            die(f"{f.name} not found next to launch.py ({HERE}).")
    if LINUX and os.geteuid() != 0:
        if not (os.path.exists("/dev/uinput") and os.access("/dev/uinput", os.W_OK)):
            say(UINPUT_HELP)
            die("uinput isn't ready (see above).")


def linux_tools():
    """Optional programs the Linux server uses; say what's missing and how to get it."""
    need = {}  # feature -> package names
    if not shutil.which("parec"):
        need["PC audio"] = {"pacman": "libpulse", "apt": "pulseaudio-utils", "dnf": "pulseaudio-utils"}
    if not shutil.which("wf-recorder") and not shutil.which("grim"):
        need["screen mirror"] = {"pacman": "wf-recorder grim", "apt": "wf-recorder grim", "dnf": "wf-recorder grim"}
    elif not shutil.which("wf-recorder"):
        need["faster screen mirror"] = {"pacman": "wf-recorder", "apt": "wf-recorder", "dnf": "wf-recorder"}
    if not shutil.which("wtype"):
        need["typing non-ASCII text"] = {"pacman": "wtype", "apt": "wtype", "dnf": "wtype"}
    if not need:
        return
    mgr = next((m for m in ("pacman", "apt", "dnf") if shutil.which(m)), None)
    warn("Optional tools missing: " + ", ".join(need))
    if mgr:
        pkgs = " ".join(dict.fromkeys(" ".join(v[mgr] for v in need.values()).split()))
        say("  Install with: " + {"pacman": "sudo pacman -S ", "apt": "sudo apt install ", "dnf": "sudo dnf install "}[mgr] + pkgs)


# ---------- main ----------
def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        say(__doc__)
        say("Server options (passed through):")
        say("  --host H --port N --token T" + ("  --monitor N" if WIN else "  --screen-output NAME  --engine auto|wf|grim"))
        return 0
    reinstall, setup_only, local = "--reinstall" in args, "--setup-only" in args, "--local" in args
    args = [a for a in args if a not in ("--reinstall", "--setup-only", "--local")]

    say(paint("1;36", "LocalLink") + paint("2", " launcher"))
    if sys.version_info < (3, 8):
        die(f"Python 3.8 or newer is required (found {sys.version.split()[0]}).")
    preflight()
    install(reinstall)
    good("Dependencies ready")
    if LINUX:
        linux_tools()
    if setup_only:
        return 0

    if not any(a == "--host" or a.startswith("--host=") for a in args):
        args = ["--host", "127.0.0.1" if local else "0.0.0.0", *args]  # 0.0.0.0 = reachable from your phone
    if WIN:
        say(paint("2", "(If Windows Firewall asks, allow Python on Private networks so your phone can connect.)"))
    cmd = [str(venv_python()), str(SERVER), *args]
    os.chdir(HERE)
    if LINUX:
        os.execv(cmd[0], cmd)  # become the server: clean Ctrl+C, no extra process
    try:
        return subprocess.call(cmd, cwd=str(HERE))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 0
    if code:
        pause_if_windows("The server stopped. Press Enter to close...")
    sys.exit(code)
