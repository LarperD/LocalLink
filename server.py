"""Remote Input - Windows version.
Controls this PC's mouse and keyboard (SendInput), a virtual Xbox 360 pad (vgamepad + ViGEmBus)
and mirrors the screen (mss + Pillow). Linux/sway users: use server_linux.py.

pip install aiohttp segno mss pillow vgamepad
python server.py [--host 0.0.0.0] [--port 8765] [--monitor 1]
"""
import os
import argparse, asyncio, collections, ctypes, io, json, random, re, shutil, socket, sys, time
import ctypes.wintypes as wt
from pathlib import Path
from urllib.parse import urlparse
import segno
from aiohttp import web, WSMsgType

if sys.platform != "win32":
    sys.exit("This is the Windows version. On Linux use server_linux.py.")

os.system("cls")

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()  # real pixel coordinates on scaled displays
user32.VkKeyScanW.restype = ctypes.c_short

# ---------- optional: virtual Xbox 360 controller (needs the ViGEmBus driver) ----------
try:
    import vgamepad as vg
    PAD = vg.VX360Gamepad()
    B = vg.XUSB_BUTTON
    XB = {"A": B.XUSB_GAMEPAD_A, "B": B.XUSB_GAMEPAD_B, "X": B.XUSB_GAMEPAD_X, "Y": B.XUSB_GAMEPAD_Y,
          "LB": B.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
          "BACK": B.XUSB_GAMEPAD_BACK, "START": B.XUSB_GAMEPAD_START, "GUIDE": B.XUSB_GAMEPAD_GUIDE,
          "L3": B.XUSB_GAMEPAD_LEFT_THUMB, "R3": B.XUSB_GAMEPAD_RIGHT_THUMB,
          "UP": B.XUSB_GAMEPAD_DPAD_UP, "DOWN": B.XUSB_GAMEPAD_DPAD_DOWN,
          "LEFT": B.XUSB_GAMEPAD_DPAD_LEFT, "RIGHT": B.XUSB_GAMEPAD_DPAD_RIGHT}
except Exception as e:
    PAD, XB = None, {}
    print(f"[gamepad] virtual controller unavailable ({e}). Key/mouse bindings still work.")

# ---------- optional: screen mirror ----------
try:
    import mss
    from PIL import Image, ImageDraw
    RES = getattr(Image, "Resampling", Image).BILINEAR
except Exception as e:
    mss = None
    print(f"[screen] mirror unavailable ({e}). Run: pip install mss pillow")
MONITOR = 1  # --monitor N (0 = all monitors together)

# ---------- SendInput plumbing ----------
ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]

class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("pt", wt.POINT)]

user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

def send(*inputs):
    user32.SendInput(len(inputs), (INPUT * len(inputs))(*inputs), ctypes.sizeof(INPUT))

def mouse_in(dx=0, dy=0, data=0, flags=0):
    return INPUT(type=0, mi=MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0))

def key_in(vk=0, scan=0, flags=0):
    return INPUT(type=1, ki=KEYBDINPUT(vk, scan, flags, 0, 0))

# mouse event flags: move, then (down, up) per button
BTN = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
MOVE, WHEEL, HWHEEL = 0x1, 0x800, 0x1000

# ---------- keys ----------
VK = {"ctrl": 0xA2, "alt": 0xA4, "shift": 0xA0, "cmd": 0x5B, "esc": 0x1B, "enter": 0x0D, "tab": 0x09,
      "backspace": 0x08, "delete": 0x2E, "space": 0x20, "up": 0x26, "down": 0x28, "left": 0x25,
      "right": 0x27, "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22, "caps_lock": 0x14}
EXT = {0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22, 0x2E, 0x2D, 0x5B, 0x5C, 0xA3, 0xA5}
MODS = {0xA0, 0xA2, 0xA4, 0x5B, 0x14}

def resolve(name):
    """key name -> (virtual-key, needs_shift); single characters follow your keyboard layout"""
    if len(name) == 1:
        r = user32.VkKeyScanW(ord(name))
        return (None, False) if r == -1 else (r & 0xFF, bool(r & 0x100))
    if name in VK:
        return VK[name], False
    if re.fullmatch(r"f([1-9]|1[0-2])", name):
        return 0x6F + int(name[1:]), False
    return None, False

def kinput(vk, down):
    scan = user32.MapVirtualKeyW(vk, 0)  # scancodes so games (DirectInput / raw input) see the key too
    flags = (0x8 if scan else 0) | (0 if down else 0x2) | (0x1 if vk in EXT else 0)
    return key_in(vk, scan, flags)

held = {}  # name -> (vk, shift_added, pressed_at)
held_btn, held_pad = set(), set()

def key_event(name, down):
    vk, shift = resolve(name)
    if vk is None: return
    if down and name not in held:
        if shift: send(kinput(0xA0, True))
        send(kinput(vk, True)); held[name] = (vk, shift, time.monotonic())
    elif not down and name in held:
        vk, shift, _ = held.pop(name)
        send(kinput(vk, False))
        if shift: send(kinput(0xA0, False))

async def repeater():
    """Windows doesn't auto-repeat injected key-downs, so repeat held non-modifier keys like a real keyboard."""
    while True:
        await asyncio.sleep(0.033)
        now = time.monotonic()
        for vk, _, t0 in list(held.values()):
            if vk not in MODS and now - t0 > 0.5:
                send(kinput(vk, True))

def type_text(s):
    for ch in s.replace("\r\n", "\n"):
        if ch in "\n\t":
            vk = 0x0D if ch == "\n" else 0x09
            send(kinput(vk, True), kinput(vk, False))
            continue
        b = ch.encode("utf-16-le")
        for i in range(0, len(b), 2):
            u = int.from_bytes(b[i:i + 2], "little")
            send(key_in(0, u, 0x4), key_in(0, u, 0x4 | 0x2))  # unicode: any layout, any language

# ---------- gamepad ----------
def pad_button(name, down):
    if PAD is None: return
    if name in ("LT", "RT"):
        (PAD.left_trigger_float if name == "LT" else PAD.right_trigger_float)(value_float=1.0 if down else 0.0)
    elif name in XB:
        (PAD.press_button if down else PAD.release_button)(button=XB[name])
    else:
        return
    (held_pad.add if down else held_pad.discard)(name)
    PAD.update()

def release_all():
    for n in list(held): key_event(n, False)
    for b in list(held_btn): send(mouse_in(0, 0, 0, BTN[b][1]))
    held_btn.clear(); held_pad.clear()
    if PAD:
        PAD.reset(); PAD.update()

def handle(m):
    t = m["t"]
    if t == "move":
        send(mouse_in(int(m["dx"]), int(m["dy"]), 0, MOVE))
    elif t == "scroll":
        dx, dy = int(m.get("dx", 0)), int(m.get("dy", 0))
        if dy: send(mouse_in(0, 0, dy * 120, WHEEL))
        if dx: send(mouse_in(0, 0, dx * 120, HWHEEL))
    elif t in ("click", "btn"):
        b = m["b"]
        for down in ((1, 0) if t == "click" else (m["s"],)):
            send(mouse_in(0, 0, 0, BTN[b][0] if down else BTN[b][1]))
            (held_btn.add if down else held_btn.discard)(b)
    elif t == "key":
        key_event(m["k"], m["s"] == "down")
    elif t == "combo":
        vks = [v for v in (resolve(k)[0] for k in m["keys"]) if v is not None]
        if vks:
            send(*[kinput(v, True) for v in vks]); send(*[kinput(v, False) for v in reversed(vks)])
    elif t == "text":
        type_text(str(m["v"])[:500])
    elif t == "pad":
        pad_button(m["b"], bool(m["s"]))
    elif t == "stick" and PAD:
        x, y = (max(-1.0, min(1.0, float(m[k]))) for k in ("x", "y"))  # screen y points down, pad y points up
        (PAD.left_joystick_float if m["s"] == "L" else PAD.right_joystick_float)(x_value_float=x, y_value_float=-y)
        PAD.update()
    elif t == "releaseall":
        release_all()

# ---------- screen mirror ----------
def capture(q, scale, mon):
    with mss.mss() as sct:
        m = sct.monitors[mon if 0 <= mon < len(sct.monitors) else 1]
        shot = sct.grab(m)
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if scale < 1:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), RES, reducing_gap=2.0)
    ci = CURSORINFO(); ci.cbSize = ctypes.sizeof(ci)
    if user32.GetCursorInfo(ctypes.byref(ci)) and ci.flags & 1:  # draw the mouse pointer (grabs don't include it)
        x, y, k = (ci.pt.x - m["left"]) * scale, (ci.pt.y - m["top"]) * scale, 14
        ImageDraw.Draw(img).polygon([(x, y), (x, y + k * 1.4), (x + k * .35, y + k * 1.1), (x + k * .6, y + k * 1.6),
                                     (x + k * .8, y + k * 1.5), (x + k * .55, y + k * 1.0), (x + k, y + k * 1.0)],
                                    fill="white", outline="black")
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=q)
    return buf.getvalue()

async def screens(req):
    if req.query.get("token") != str(TOKEN):
        raise web.HTTPForbidden()
    def lst():
        with mss.mss() as sct:
            return [{"id": str(i), "label": ("All screens" if i == 0 else f"Screen {i}") + f' ({m["width"]}x{m["height"]})'}
                    for i, m in enumerate(sct.monitors)]
    items = await asyncio.get_running_loop().run_in_executor(None, lst) if mss else []
    return web.json_response({"default": str(MONITOR), "engine": "mss + Pillow", "screens": items})

async def screen(req):
    """Live desktop view as an MJPEG stream; two captures run in parallel to raise the frame rate."""
    if req.query.get("token") != str(TOKEN):
        raise web.HTTPForbidden()
    if mss is None:
        raise web.HTTPNotImplemented(text="pip install mss pillow for the screen view.")
    try:
        num = lambda k, d, lo, hi: max(lo, min(hi, float(req.query.get(k, d))))
        q, scale, fps = int(num("q", 50, 10, 95)), num("scale", 0.5, 0.1, 1), num("fps", 10, 1, 30)
        mon = int(req.query.get("output", MONITOR))
    except ValueError:
        raise web.HTTPBadRequest()
    resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame",
                                       "Cache-Control": "no-store"})
    await resp.prepare(req)
    loop = asyncio.get_running_loop()
    inflight, fails, last = collections.deque(), 0, 0.0
    try:
        while fails < 5:
            await asyncio.sleep(max(0.0, 1 / fps - (time.monotonic() - last)))
            while len(inflight) < 2:
                inflight.append(loop.run_in_executor(None, capture, q, scale, mon))
            try:
                data = await inflight.popleft()
            except Exception as e:
                print("screen capture failed:", e); data = b""
            if not data:
                fails += 1; await asyncio.sleep(1); continue
            fails, last = 0, time.monotonic()
            await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data)
                             + data + b"\r\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        for f in inflight: f.cancel()
    return resp

# ---------- web ----------
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
    await ws.send_json({"pad": PAD is not None})
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try: handle(json.loads(msg.data))
                except Exception as e: print("bad message:", e)
    finally:
        release_all()
    return ws

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

async def start_repeater(app):
    app["repeater"] = asyncio.create_task(repeater())

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    TOKEN = random.randint(1000, 9999)
    ap.add_argument("--token", default=TOKEN)
    ap.add_argument("--monitor", type=int, default=1, help="screen to mirror: 1 = primary, 2 = next, 0 = all")
    a = ap.parse_args()
    MONITOR = a.monitor
    app = web.Application()
    app.add_routes([web.get("/", index), web.get("/ws", ws_handler), web.get("/screen.mjpg", screen), web.get("/screens", screens)])
    app.on_startup.append(start_repeater)
    url = f"http://{lan_ip() if a.host == '0.0.0.0' else a.host}:{a.port}/?token={TOKEN}"
    cols = shutil.get_terminal_size((80, 24)).columns
    indent = lambda n: " " * max(0, (cols - n) // 2)
    mid = lambda s: indent(len(s)) + s
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
    print(mid("(or) SCAN QR CODE to Use"))
    qr = io.StringIO()
    segno.make(url).terminal(out=qr, compact=True, border=1)
    for line in qr.getvalue().splitlines():
        print(indent(len(ansi.sub("", line))) + line)
    print()
    #if a.host in ("127.0.0.1", "localhost"): webbrowser.open(url)
    web.run_app(app, host=a.host, port=a.port, print=None)
