import os
import argparse, asyncio, collections, io, json, re, secrets, shutil, socket, subprocess, sys, time, webbrowser
from pathlib import Path
from urllib.parse import urlparse
import random
import segno
from aiohttp import web, WSMsgType
from evdev import AbsInfo, UInput, ecodes as ec

os.system('clear')

KEYS = [getattr(ec, n) for n in dir(ec) if n.startswith("KEY_") and not n.startswith(("KEY_MAX", "KEY_CNT"))]
BTNS = {"left": ec.BTN_LEFT, "right": ec.BTN_RIGHT, "middle": ec.BTN_MIDDLE}
try:
    kbd = UInput({ec.EV_KEY: [k for k in KEYS if k < 249]}, name="remote-input keyboard")
    mouse = UInput({ec.EV_KEY: list(BTNS.values()),
                    ec.EV_REL: [ec.REL_X, ec.REL_Y, ec.REL_WHEEL, ec.REL_HWHEEL]}, name="remote-input mouse")
    st = AbsInfo(0, -32768, 32767, 16, 128, 0)
    PADKEYS = {"A": ec.BTN_SOUTH, "B": ec.BTN_EAST, "X": ec.BTN_NORTH, "Y": ec.BTN_WEST,
               "LB": ec.BTN_TL, "RB": ec.BTN_TR, "BACK": ec.BTN_SELECT, "START": ec.BTN_START,
               "GUIDE": ec.BTN_MODE, "L3": ec.BTN_THUMBL, "R3": ec.BTN_THUMBR}
    pad = UInput({ec.EV_KEY: list(PADKEYS.values()),
                  ec.EV_ABS: [(ec.ABS_X, st), (ec.ABS_Y, st), (ec.ABS_RX, st), (ec.ABS_RY, st),
                              (ec.ABS_Z, AbsInfo(0, 0, 255, 0, 0, 0)), (ec.ABS_RZ, AbsInfo(0, 0, 255, 0, 0, 0)),
                              (ec.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)), (ec.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0))]},
                 name="Microsoft X-Box 360 pad", vendor=0x045E, product=0x028E, version=0x110)
except (PermissionError, FileNotFoundError) as e:
    sys.exit(f"Can't open /dev/uinput ({e}).\nRun: sudo modprobe uinput  and see the setup steps for the 'uinput' group.")

NAMES = {"ctrl": "LEFTCTRL", "alt": "LEFTALT", "shift": "LEFTSHIFT", "cmd": "LEFTMETA", "esc": "ESC",
         "enter": "ENTER", "tab": "TAB", "backspace": "BACKSPACE", "delete": "DELETE", "space": "SPACE",
         "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "home": "HOME", "end": "END",
         "page_up": "PAGEUP", "page_down": "PAGEDOWN", "caps_lock": "CAPSLOCK"}
PUNCT = {"-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE", ";": "SEMICOLON", "'": "APOSTROPHE",
         "`": "GRAVE", "\\": "BACKSLASH", ",": "COMMA", ".": "DOT", "/": "SLASH", " ": "SPACE",
         "\n": "ENTER", "\t": "TAB"}
SHIFTED = dict(zip('!@#$%^&*()_+{}|:"~<>?', "1234567890-=[]\\;'`,./"))

def code(name):
    if len(name) == 1:
        n, shift = name, False
        if n.isupper(): n, shift = n.lower(), True
        elif n in SHIFTED: n, shift = SHIFTED[n], True
        label = n.upper() if (n.isascii() and n.isalnum()) else PUNCT.get(n)
        return (getattr(ec, "KEY_" + label, None) if label else None), shift
    return getattr(ec, "KEY_" + NAMES.get(name, name.upper()), None), False

held = {}
held_btn, held_pad = set(), set()
hat = {"x": 0, "y": 0}

def kev(c, down):
    kbd.write(ec.EV_KEY, c, 1 if down else 0); kbd.syn()

def key_event(name, down):
    c, shift = code(name)
    if c is None: return
    if down and name not in held:
        if shift: kev(ec.KEY_LEFTSHIFT, True)
        kev(c, True); held[name] = (c, shift)
    elif not down and name in held:
        c, shift = held.pop(name)
        kev(c, False)
        if shift: kev(ec.KEY_LEFTSHIFT, False)

WTYPE = shutil.which("wtype")

def type_text(s):
    if WTYPE and subprocess.run([WTYPE, "--", s]).returncode == 0:
        return
    for ch in s:
        key_event(ch, True); key_event(ch, False)

def pad_button(name, down):
    if name in ("LT", "RT"):
        pad.write(ec.EV_ABS, ec.ABS_Z if name == "LT" else ec.ABS_RZ, 255 if down else 0)
    elif name in PADKEYS:
        pad.write(ec.EV_KEY, PADKEYS[name], 1 if down else 0)
    elif name in ("UP", "DOWN", "LEFT", "RIGHT"):
        axis, v = {"UP": ("y", -1), "DOWN": ("y", 1), "LEFT": ("x", -1), "RIGHT": ("x", 1)}[name]
        hat[axis] = v if down else (0 if hat[axis] == v else hat[axis])
        pad.write(ec.EV_ABS, ec.ABS_HAT0X, hat["x"]); pad.write(ec.EV_ABS, ec.ABS_HAT0Y, hat["y"])
    else:
        return
    (held_pad.add if down else held_pad.discard)(name)
    pad.syn()

def release_all():
    for n in list(held): key_event(n, False)
    for b in list(held_btn):
        mouse.write(ec.EV_KEY, b, 0)
    mouse.syn(); held_btn.clear()
    for n in list(held_pad): pad_button(n, False)
    for a in (ec.ABS_X, ec.ABS_Y, ec.ABS_RX, ec.ABS_RY): pad.write(ec.EV_ABS, a, 0)
    pad.syn()

def handle(m):
    t = m["t"]
    if t == "move":
        mouse.write(ec.EV_REL, ec.REL_X, int(m["dx"])); mouse.write(ec.EV_REL, ec.REL_Y, int(m["dy"])); mouse.syn()
    elif t == "scroll":
        mouse.write(ec.EV_REL, ec.REL_HWHEEL, int(m.get("dx", 0))); mouse.write(ec.EV_REL, ec.REL_WHEEL, int(m.get("dy", 0))); mouse.syn()
    elif t in ("click", "btn"):
        b = BTNS[m["b"]]
        for down in ((1, 0) if t == "click" else (m["s"],)):
            mouse.write(ec.EV_KEY, b, down); mouse.syn()
            (held_btn.add if down else held_btn.discard)(b)
    elif t == "key":
        key_event(m["k"], m["s"] == "down")
    elif t == "combo":
        ks = [code(k)[0] for k in m["keys"]]
        ks = [k for k in ks if k is not None]
        for k in ks: kev(k, True)
        for k in reversed(ks): kev(k, False)
    elif t == "text":
        type_text(str(m["v"])[:500])
    elif t == "pad":
        pad_button(m["b"], bool(m["s"]))
    elif t == "stick":
        x, y = (max(-1, min(1, float(m[k]))) for k in ("x", "y"))
        ax, ay = (ec.ABS_X, ec.ABS_Y) if m["s"] == "L" else (ec.ABS_RX, ec.ABS_RY)
        pad.write(ec.EV_ABS, ax, int(x * 32767)); pad.write(ec.EV_ABS, ay, int(y * 32767)); pad.syn()
    elif t == "releaseall":
        release_all()

async def index(req):
    if req.query.get("token") != str(TOKEN):
        return web.Response(status=403, text="Open the full URL printed in the terminal (it includes ?token=...).")
    return web.FileResponse(Path(__file__).with_name("index.html"))

async def ws_handler(req):
    if req.query.get("token") != str(TOKEN):
        raise web.HTTPForbidden()
    origin = req.headers.get("Origin")
    if origin and urlparse(origin).netloc != req.host:
        raise web.HTTPForbidden()
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    await ws.send_json({"pad": True})
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try: handle(json.loads(msg.data))
                except Exception as e: print("bad message:", e)
    finally:
        release_all()
    return ws

GRIM = shutil.which("grim")  # Wayland screenshots: sudo pacman -S grim
SCREEN_OUT = None            # --screen-output DP-1 (default: all monitors)
CURSOR = False
if GRIM:
    h = subprocess.run([GRIM, "-h"], capture_output=True, text=True)
    CURSOR = "-c" in (h.stdout + h.stderr)
WF = shutil.which("wf-recorder")  # fast engine: sudo pacman -S wf-recorder
ENGINE, WF_BAD = "auto", False    # --engine auto|wf|grim
NICE = ["nice", "-n", "10"] if shutil.which("nice") else []  # grim runs at low priority so it can't starve your game

def fast():
    return bool(WF) and ENGINE != "grim" and not WF_BAD

def engine_label():
    if fast(): return "wf-recorder (fast)" + ("; grim for All screens" if GRIM else "")
    if WF_BAD: return "grim (wf-recorder produced no frames)"
    return "grim" + ("" if WF else " (slow: sudo pacman -S wf-recorder)")

def default_output(ids):
    """Monitor to mirror when none is chosen: --screen-output, else the first one (wf-recorder records one monitor)."""
    return SCREEN_OUT or (ids[0] if fast() and ids else None)

def list_outputs():
    """Connected monitors, from sway (swaymsg) or any wlroots compositor with wlr-randr."""
    try:
        d = json.loads(subprocess.run(["swaymsg", "-t", "get_outputs", "-r"], capture_output=True, text=True, timeout=3).stdout)
        return [{"id": o["name"], "label": f'{o["name"]} ({o["rect"]["width"]}x{o["rect"]["height"]}) {o.get("model", "")}'.strip()}
                for o in d if o.get("active")]
    except Exception:
        pass
    try:
        d = json.loads(subprocess.run(["wlr-randr", "--json"], capture_output=True, text=True, timeout=3).stdout)
        return [{"id": o["name"], "label": f'{o["name"]} {o.get("model", "")}'.strip()} for o in d if o.get("enabled", True)]
    except Exception:
        return []

async def screens(req):
    if req.query.get("token") != str(TOKEN):
        raise web.HTTPForbidden()
    outs = await asyncio.get_running_loop().run_in_executor(None, list_outputs)
    return web.json_response({"default": default_output([o["id"] for o in outs]) or "all", "engine": engine_label(),
                              "screens": [{"id": "all", "label": "All screens"}] + outs})

async def grab(cmd):
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        data, _ = await p.communicate()
    except asyncio.CancelledError:
        p.kill(); raise
    return data if p.returncode == 0 else b""

async def wf_stream(req, resp, out, q, scale, fps):
    """One long-running wf-recorder piped straight to the browser as MJPEG: no process per frame, and
    the compositor only copies the screen when something changed. False = it never produced a frame."""
    global WF_BAD
    qs = int(round(31 - (q - 10) * 29 / 85))  # JPEG-style 10..95 -> mjpeg qscale 31..2
    cmd = [WF, "-y", "-o", out, "-c", "mjpeg", "-m", "mpjpeg", "-x", "yuvj420p", "-r", str(int(fps)),
           "-F", f"scale=w=trunc(iw*{scale:.3f}/2)*2:h=trunc(ih*{scale:.3f}/2)*2",
           "-p", "flags=+qscale", "-p", f"global_quality={qs * 118}", "-f", "/dev/stdout"]
    p = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.DEVNULL)
    got, waited = False, 0
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(p.stdout.read(65536), 2)
            except asyncio.TimeoutError:
                waited += 2
                if req.transport is None or req.transport.is_closing():
                    return True  # the viewer left while the screen was idle
                if not got and waited >= 6:
                    break
                continue
            if not chunk:
                break
            got = True
            await resp.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        return True
    finally:
        try:
            p.kill()
            await asyncio.wait_for(p.wait(), 2)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
    if not got and req.transport is not None and not req.transport.is_closing():
        WF_BAD = True
        print("wf-recorder gave no frames, using grim instead (try: --engine grim to skip this check)")
    return got

async def screen(req):
    """Live desktop view as an MJPEG stream: wf-recorder when possible, else a grim capture per frame."""
    if req.query.get("token") != str(TOKEN):
        raise web.HTTPForbidden()
    if not (GRIM or fast()):
        raise web.HTTPNotImplemented(text="Install wf-recorder or grim (sudo pacman -S wf-recorder grim) for the screen view.")
    try:
        num = lambda k, d, lo, hi: max(lo, min(hi, float(req.query.get(k, d))))
        q, scale, fps = int(num("q", 50, 10, 95)), num("scale", 0.5, 0.1, 1), num("fps", 10, 1, 30)
    except ValueError:
        raise web.HTTPBadRequest()
    ids = [o["id"] for o in await asyncio.get_running_loop().run_in_executor(None, list_outputs)]
    sel = req.query.get("output")
    out = None if (sel == "all" and GRIM) else (sel if sel in ids else default_output(ids))
    resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=ffmpeg",
                                       "Cache-Control": "no-store"})
    await resp.prepare(req)
    inflight, fails, last = collections.deque(), 0, 0.0
    try:
        if out and fast() and await wf_stream(req, resp, out, q, scale, fps):
            return resp
        if not GRIM:
            return resp
        cmd = NICE + [GRIM, "-t", "jpeg", "-q", str(q), "-s", str(scale)] + (["-c"] if CURSOR else []) \
            + (["-o", out] if out else []) + ["-"]
        while fails < 5:
            await asyncio.sleep(max(0.0, 1 / fps - (time.monotonic() - last)))
            while len(inflight) < 2:
                inflight.append(asyncio.create_task(grab(cmd)))
            data = await inflight.popleft()
            if not data:
                fails += 1; await asyncio.sleep(1); continue
            fails, last = 0, time.monotonic()
            await resp.write(b"--ffmpeg\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data)
                             + data + b"\r\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        for t in inflight: t.cancel()
    return resp

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    TOKEN = random.randint(1000, 9999)
    ap.add_argument("--token", default=TOKEN)
    ap.add_argument("--screen-output", help="monitor name for the screen view, e.g. DP-1")
    ap.add_argument("--engine", choices=["auto", "wf", "grim"], default="auto", help="screen capture engine")
    a = ap.parse_args()
    SCREEN_OUT, ENGINE = a.screen_output, a.engine
    app = web.Application()
    app.add_routes([web.get("/", index), web.get("/ws", ws_handler), web.get("/screen.mjpg", screen), web.get("/screens", screens)])
    url = f"http://{lan_ip() if a.host == '0.0.0.0' else a.host}:{a.port}/?token={TOKEN}"
    cols = shutil.get_terminal_size((80, 24)).columns
    p = lambda n: " " * max(0, (cols - n) // 2)
    mid = lambda s: p(len(s)) + s
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    print()
    print()
    print(mid('LOCAL LINK'))
    print(mid('Vibe Coded Remote controller !'))
    print()
    print(mid("Remote Input running. Open:"))
    print(mid(url))
    print(mid("(Ctrl+C to stop)"))
    print()
    if fast(): print(mid("Screen engine: wf-recorder (fast)" + ("; grim for All screens" if GRIM else "")))
    elif GRIM: print(mid("Screen engine: grim. For a much lighter mirror: sudo pacman -S wf-recorder"))
    else: print(mid("Screen view is off: sudo pacman -S wf-recorder grim"))
    print(mid("(or) SCAN QR CODE to Use"))
    qr = io.StringIO()
    segno.make(url).terminal(out=qr, compact=True, border=1)
    for line in qr.getvalue().splitlines():
        print(p(len(ansi.sub("", line))) + line)
    print()
    #if a.host in ("127.0.0.1", "localhost"): webbrowser.open(url)
    web.run_app(app, host=a.host, port=a.port, print=None)
