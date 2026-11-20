"""
TurboTap - rapid-fire for keyboard keys, mouse buttons and game-controller buttons.

Hold a button -> it gets tapped rapidly for you (or use Toggle mode: press once
to start auto-tapping, press again to stop).

Windows only, Python 3.8+.
    Installed automatically on first run:
        pynput              keyboard / mouse hooks
        pysdl2 + pysdl2-dll reads every controller: PS5 DualSense, PS4, Xbox, Switch, generic
        sv-ttk              modern Windows 11 style theme
    Optional (Controller tab -> One-time setup): vgamepad, ViGEmBus, HidHide

Double-click TurboTap.pyw to run it. Run as Administrator if your game runs as admin.
"""

import collections
import copy
import ctypes
import glob
import importlib
import importlib.util
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import webbrowser
import winreg
import winsound
from ctypes import wintypes

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk

APP_NAME = "TurboTap"
APP_VERSION = "1.0"
APP_AUTHOR = "ilukezippo (BoYaqoub)"
GITHUB_REPO = "ilukezippo/TurboTap"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
DONATE_URL = "https://buymeacoffee.com/ilukezippo"
FROZEN = bool(getattr(sys, "frozen", False))   # running as TurboTap.exe (PyInstaller)
# the program HidHide must allow so TurboTap can still read the hidden controller
TURBOTAP_EXE = sys.executable if FROZEN else os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
CREATE_NO_WINDOW = 0x08000000


# =====================================================================
#  Dependencies
# =====================================================================
def pip_install(package):
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", package],
                           capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


REQUIRED_PACKAGES = [("pynput", "pynput"), ("sdl2", "pysdl2"), ("sdl2dll", "pysdl2-dll"), ("sv_ttk", "sv-ttk")]


def has_module(name):
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _bootstrap():
    if FROZEN:  # the .exe already contains every library
        return
    missing = [pip_name for mod, pip_name in REQUIRED_PACKAGES if not has_module(mod)]
    if not missing:
        return
    root = tk.Tk()
    root.withdraw()
    if not messagebox.askyesno(APP_NAME, "TurboTap needs a few free Python libraries:\n\n    "
                                         + ", ".join(missing) + "\n\nInstall them now? (takes a few seconds)"):
        if "pynput" in missing:
            sys.exit(0)
        root.destroy()
        return
    log = ""
    for pkg in missing:
        ok, out = pip_install(pkg)
        if not ok:
            log += f"\n[{pkg}]\n{out[-400:]}"
    importlib.invalidate_caches()
    if not has_module("pynput"):
        messagebox.showerror(APP_NAME, "Couldn't install pynput.\n\nOpen Command Prompt and run:\n"
                                       "    py -m pip install pynput pysdl2 pysdl2-dll sv-ttk\n" + log[-800:])
        sys.exit(1)
    root.destroy()


_bootstrap()
from pynput import keyboard as pkb  # noqa: E402
from pynput import mouse as pms  # noqa: E402


# =====================================================================
#  Win32
# =====================================================================
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32")
winmm = ctypes.WinDLL("winmm")

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetKeyNameTextW.argtypes = (wintypes.LONG, wintypes.LPWSTR, ctypes.c_int)
user32.GetKeyNameTextW.restype = ctypes.c_int
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
shell32.ShellExecuteW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, ctypes.c_int)
shell32.ShellExecuteW.restype = ctypes.c_void_p

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
MAGIC = 0x54555242  # tags our own injected input

EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
               0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5}
MODIFIER_VK = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
NEVER_IN_ALL_VK = {0x5B, 0x5C, 0x14, 0x90, 0x91}  # Win keys, Caps/Num/Scroll Lock

MOUSE_SEND = {"left": (0x0002, 0x0004, 0), "right": (0x0008, 0x0010, 0), "middle": (0x0020, 0x0040, 0),
              "x1": (0x0080, 0x0100, 1), "x2": (0x0080, 0x0100, 2)}
MOUSE_NAMES = {"left": "Left click", "right": "Right click", "middle": "Middle click",
               "x1": "Side button 1 (Back)", "x2": "Side button 2 (Forward)"}
MOUSE_MSGS = {0x201: ("left", True), 0x202: ("left", False), 0x204: ("right", True), 0x205: ("right", False),
              0x207: ("middle", True), 0x208: ("middle", False)}
WM_KEYDOWN, WM_SYSKEYDOWN, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MOUSEWHEEL = 0x100, 0x104, 0x20B, 0x20C, 0x20A
LLKHF_EXTENDED, LLKHF_INJECTED, LLMHF_INJECTED = 0x01, 0x10, 0x01


def _send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_key(vk, scan, ext, up):
    flags = KEYEVENTF_KEYUP if up else 0
    if ext:
        flags |= KEYEVENTF_EXTENDEDKEY
    if scan:  # scan codes work in more games (DirectInput / raw input)
        ki = KEYBDINPUT(0, scan, flags | KEYEVENTF_SCANCODE, 0, MAGIC)
    else:
        ki = KEYBDINPUT(vk, 0, flags, 0, MAGIC)
    _send(INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=ki)))


def send_mouse(button, up):
    down_flag, up_flag, data = MOUSE_SEND[button]
    mi = MOUSEINPUT(0, 0, data, up_flag if up else down_flag, 0, MAGIC)
    _send(INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=mi)))


def vk_scan(vk):
    return user32.MapVirtualKeyW(vk, 0), vk in EXTENDED_VK


_vk_name_cache = {}


def vk_name(vk):
    if vk in _vk_name_cache:
        return _vk_name_cache[vk]
    scan, ext = vk_scan(vk)
    name = ""
    if scan:
        buf = ctypes.create_unicode_buffer(64)
        if user32.GetKeyNameTextW((scan << 16) | (int(ext) << 24), buf, 64):
            name = buf.value
    name = name or f"Key 0x{vk:02X}"
    _vk_name_cache[vk] = name
    return name


_exe_cache = {}


def exe_of_pid(pid):
    if pid in _exe_cache:
        return _exe_cache[pid]
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h)
    if len(_exe_cache) > 500:
        _exe_cache.clear()
    _exe_cache[pid] = name
    return name


def foreground_pid():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def list_window_exes():
    names = set()

    @WNDENUMPROC
    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            n = exe_of_pid(pid.value)
            if n and pid.value != os.getpid():
                names.add(n)
        return True

    user32.EnumWindows(cb, 0)
    return sorted(names, key=str.lower)


def is_admin():
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


# ---------------- driver / installer helpers ----------------
class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


shell32.ShellExecuteExW.argtypes = (ctypes.POINTER(SHELLEXECUTEINFOW),)
shell32.ShellExecuteExW.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD


def run_and_wait(path):
    """Start an installer (it asks for admin itself) and wait until it closes."""
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "open"
    sei.lpFile = path
    sei.nShow = 1
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        return False
    if sei.hProcess:
        kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        kernel32.CloseHandle(sei.hProcess)
    return True


def service_installed(name):
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                       rf"SYSTEM\CurrentControlSet\Services\{name}"))
        return True
    except OSError:
        return False


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))) or (0,)


def latest_release():
    """(version, download url) of the newest TurboTap release on GitHub."""
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    ver = str(data.get("tag_name", "")).lstrip("vV")
    url = next((a["browser_download_url"] for a in data.get("assets", [])
                if a.get("name", "").lower() == "turbotap.exe"), data.get("html_url") or REPO_URL)
    return ver, url


_svc_cache = {}


def service_running(name):
    """True if a Windows driver/service is running now. Installed-but-stopped usually means the
    PC hasn't been restarted since installing it. Cached for 10 seconds."""
    now = time.monotonic()
    hit = _svc_cache.get(name)
    if hit and now - hit[0] < 10:
        return hit[1]
    try:
        out = subprocess.run(["sc", "query", name], capture_output=True, text=True, timeout=5,
                             creationflags=CREATE_NO_WINDOW).stdout
        running = "RUNNING" in out
    except Exception:  # noqa: BLE001
        running = True  # can't tell - don't nag
    _svc_cache[name] = (now, running)
    return running


def vgamepad_installed():
    if FROZEN:
        try:
            import vgamepad  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False
    importlib.invalidate_caches()
    return has_module("vgamepad")


def hidhide_client():
    roots = {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", ""),
             r"C:\Program Files"}
    for root in filter(None, roots):
        for pattern in (r"Nefarius Software Solutions\HidHide\*\HidHideClient.exe",
                        r"Nefarius Software Solutions\HidHide\HidHideClient.exe",
                        r"Nefarius Software Solutions*\**\HidHideClient.exe"):
            hits = glob.glob(os.path.join(root, pattern), recursive=True)
            if hits:
                return hits[0]
    return None


# Used only if GitHub's API can't be reached.
FALLBACK_INSTALLERS = {
    "ViGEmBus": ("ViGEmBus_1.22.0_x64_x86_arm64.exe",
                 "https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe"),
}


def latest_installer(repo):
    """(file name, download url) of the newest installer in nefarius/<repo>'s GitHub releases."""
    req = urllib.request.Request(f"https://api.github.com/repos/nefarius/{repo}/releases/latest",
                                 headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    assets = [a for a in data.get("assets", []) if a["name"].lower().endswith((".exe", ".msi"))]
    if not assets:
        raise RuntimeError("no installer in the latest release")
    arch = "arm64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "x64"
    assets.sort(key=lambda a: (arch not in a["name"].lower(), not a["name"].lower().endswith(".exe")))
    return assets[0]["name"], assets[0]["browser_download_url"]


def download(url, dest, progress):
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                progress(done * 100 // total)
    os.replace(dest + ".part", dest)


# ---------------- XInput (reading controllers) ----------------
class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


xinput = None
for _dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
    try:
        xinput = ctypes.WinDLL(_dll)
        xinput.XInputGetState.argtypes = (wintypes.DWORD, ctypes.POINTER(XINPUT_STATE))
        xinput.XInputGetState.restype = wintypes.DWORD
        break
    except OSError:
        xinput = None


class XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [("wLeftMotorSpeed", wintypes.WORD), ("wRightMotorSpeed", wintypes.WORD)]


class XINPUT_CAPABILITIES(ctypes.Structure):
    _fields_ = [("Type", ctypes.c_ubyte), ("SubType", ctypes.c_ubyte), ("Flags", wintypes.WORD),
                ("Gamepad", XINPUT_GAMEPAD), ("Vibration", XINPUT_VIBRATION)]


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


PAD_SUBTYPES = {0x01: "Gamepad", 0x02: "Racing wheel", 0x03: "Arcade stick", 0x04: "Flight stick",
                0x05: "Dance pad", 0x06: "Guitar", 0x07: "Guitar", 0x08: "Drum kit", 0x0B: "Bass guitar",
                0x13: "Arcade pad"}
BATTERY_LEVELS = {0: "Empty", 1: "Low", 2: "Medium", 3: "Full"}


def read_pad(index, state):
    return xinput is not None and xinput.XInputGetState(index, ctypes.byref(state)) == 0


def pad_info(index):
    """Short description: type, wired/wireless, battery. XInput can't tell the exact model."""
    if xinput is None:
        return ""
    caps = XINPUT_CAPABILITIES()
    try:
        if xinput.XInputGetCapabilities(index, 1, ctypes.byref(caps)) != 0:  # 1 = gamepads only
            return ""
    except AttributeError:
        return ""
    parts = [PAD_SUBTYPES.get(caps.SubType, "Controller")]
    try:
        bat = XINPUT_BATTERY_INFORMATION()
        if xinput.XInputGetBatteryInformation(index, 0, ctypes.byref(bat)) == 0:
            if bat.BatteryType == 1:
                parts.append("wired")
            elif bat.BatteryType in (2, 3):
                parts.append(f"wireless - battery {BATTERY_LEVELS.get(bat.BatteryLevel, '?')}")
            elif caps.Flags & 0x0002:
                parts.append("wireless")
    except AttributeError:  # older XInput versions have no battery info
        if caps.Flags & 0x0002:
            parts.append("wireless")
    return "  •  ".join(parts)


PAD_BUTTONS = [("A", 0x1000), ("B", 0x2000), ("X", 0x4000), ("Y", 0x8000),
               ("LB", 0x0100), ("RB", 0x0200), ("LT", None), ("RT", None),
               ("Back", 0x0020), ("Start", 0x0010), ("L-Stick", 0x0040), ("R-Stick", 0x0080),
               ("D-Up", 0x0001), ("D-Down", 0x0002), ("D-Left", 0x0004), ("D-Right", 0x0008)]
PAD_BITS = {n: b for n, b in PAD_BUTTONS}
TRIGGER_THRESHOLD = 30
GUIDE_BIT, TOUCH_BIT = 0x0400, 0x10000   # TOUCH_BIT = PlayStation touchpad click (display only)
OUTPUT_BITS = [b for _n, b in PAD_BUTTONS if b] + [GUIDE_BIT]

# SDL GameController button index -> XInput-style bit
SDL_TO_BIT = (0x1000, 0x2000, 0x4000, 0x8000, 0x0020, GUIDE_BIT, 0x0010, 0x0040, 0x0080,
              0x0100, 0x0200, 0x0001, 0x0002, 0x0004, 0x0008)
SDL_BUTTON_TOUCHPAD = 20
SDL_KIND = {1: "xbox", 2: "xbox", 3: "ps", 4: "ps", 5: "nintendo", 7: "ps", 11: "nintendo", 12: "nintendo",
            13: "nintendo"}
SDL_POWER = {0: "battery empty", 1: "battery low", 2: "battery medium", 3: "battery full", 4: "wired",
             5: "battery full"}
GENERIC_BUTTON_BITS = (0x1000, 0x2000, 0x4000, 0x8000, 0x0100, 0x0200, 0x0020, 0x0010, 0x0040, 0x0080)


def _c16(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else int(v)


class Pad:
    """One controller's current state (sticks use XInput ranges, Y up = positive)."""
    __slots__ = ("id", "name", "kind", "buttons", "lt", "rt", "lx", "ly", "rx", "ry", "power", "virtual")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, 0))


class PadReader(threading.Thread):
    """Reads all controllers in the background with SDL2 (PS5/PS4/Xbox/Switch/generic, USB or
    Bluetooth). Falls back to XInput (Xbox-style only) if SDL2 isn't available."""

    def __init__(self):
        super().__init__(daemon=True)
        self.pads = []                 # list of Pad, replaced every poll
        self.backend = "starting"
        self.error = ""
        self.running = True
        self.expect_virtual_until = 0.0
        self._xs = XINPUT_STATE()

    def get(self, pad_id):
        for pad in self.pads:
            if pad.id == pad_id:
                return pad
        return None

    def _is_new_virtual(self):
        if time.monotonic() < self.expect_virtual_until:
            self.expect_virtual_until = 0.0
            return True
        return False

    def run(self):
        try:
            self._run_sdl()
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        if self.running:
            self.backend = "xinput"
            self._run_xinput()

    # ---- SDL2 ----
    def _run_sdl(self):
        import sdl2
        sdl2.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")  # keep reading while a game has focus
        sdl2.SDL_SetHint(b"SDL_GAMECONTROLLER_USE_BUTTON_LABELS", b"0")  # A = bottom button on every pad
        sdl2.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI_PS5", b"1")
        sdl2.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI_PS4", b"1")
        # Xbox pads: use the polled XInput path. SDL's default RawInput / Windows.Gaming.Input paths only
        # deliver button presses to apps that pump an SDL window, so a detected pad stayed "frozen".
        sdl2.SDL_SetHint(b"SDL_JOYSTICK_RAWINPUT", b"0")
        sdl2.SDL_SetHint(b"SDL_JOYSTICK_WGI", b"0")
        sdl2.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI_XBOX", b"0")
        sdl2.SDL_SetHint(b"SDL_XINPUT_ENABLED", b"1")
        sdl2.SDL_SetHint(b"SDL_JOYSTICK_THREAD", b"1")  # device plug/unplug without a window
        if sdl2.SDL_Init(sdl2.SDL_INIT_GAMECONTROLLER | sdl2.SDL_INIT_JOYSTICK) != 0:
            raise RuntimeError(sdl2.SDL_GetError().decode(errors="replace"))
        self.backend = "sdl"
        devs = {}
        ev = sdl2.SDL_Event()
        try:
            while self.running:
                while sdl2.SDL_PollEvent(ctypes.byref(ev)):
                    if ev.type == sdl2.SDL_JOYDEVICEADDED:
                        self._sdl_open(sdl2, ev.jdevice.which, devs)
                    elif ev.type == sdl2.SDL_JOYDEVICEREMOVED:
                        d = devs.pop(ev.jdevice.which, None)
                        if d:
                            (sdl2.SDL_GameControllerClose if d["gc"] else sdl2.SDL_JoystickClose)(d["h"])
                now = time.monotonic()
                self.pads = [self._sdl_read(sdl2, iid, d, now) for iid, d in list(devs.items())]
                time.sleep(0.002)
        finally:
            for d in devs.values():
                (sdl2.SDL_GameControllerClose if d["gc"] else sdl2.SDL_JoystickClose)(d["h"])
            sdl2.SDL_Quit()

    def _sdl_open(self, sdl2, index, devs):
        if sdl2.SDL_IsGameController(index):
            h = sdl2.SDL_GameControllerOpen(index)
            if not h:
                return
            joy = sdl2.SDL_GameControllerGetJoystick(h)
            name = sdl2.SDL_GameControllerName(h)
            kind = SDL_KIND.get(sdl2.SDL_GameControllerGetType(h), "generic")
            gc = True
        else:
            h = joy = sdl2.SDL_JoystickOpen(index)
            if not h:
                return
            name, kind, gc = sdl2.SDL_JoystickName(h), "generic", False
        iid = sdl2.SDL_JoystickInstanceID(joy)
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        devs[iid] = {"gc": gc, "h": h, "joy": joy, "name": name or "Controller", "kind": kind,
                     "virtual": self._is_new_virtual(), "power_t": 0.0, "power": ""}

    def _sdl_read(self, sdl2, iid, d, now):
        h, joy = d["h"], d["joy"]
        b = 0
        if d["gc"]:
            for i, bit in enumerate(SDL_TO_BIT):
                if sdl2.SDL_GameControllerGetButton(h, i):
                    b |= bit
            try:
                if sdl2.SDL_GameControllerGetButton(h, SDL_BUTTON_TOUCHPAD):
                    b |= TOUCH_BIT
            except Exception:  # noqa: BLE001
                pass
            ax = [sdl2.SDL_GameControllerGetAxis(h, a) for a in range(6)]
            lt, rt = ax[4] * 255 // 32767, ax[5] * 255 // 32767
            if d["kind"] == "xbox":  # Xbox pad: read its XInput slot directly as well (always live)
                slot = sdl2.SDL_GameControllerGetPlayerIndex(h)
                if 0 <= slot < 4 and read_pad(slot, self._xs):
                    g = self._xs.Gamepad
                    b = (g.wButtons & 0xF7FF) | (b & ~0xF7FF)
                    lt, rt = g.bLeftTrigger, g.bRightTrigger
                    return Pad(id=iid, name=d["name"], kind="xbox", buttons=b, lt=lt, rt=rt,
                               lx=g.sThumbLX, ly=g.sThumbLY, rx=g.sThumbRX, ry=g.sThumbRY,
                               power=self._power(sdl2, d, joy, now), virtual=d["virtual"])
        else:
            for i, bit in enumerate(GENERIC_BUTTON_BITS[:sdl2.SDL_JoystickNumButtons(joy)]):
                if sdl2.SDL_JoystickGetButton(joy, i):
                    b |= bit
            if sdl2.SDL_JoystickNumHats(joy) > 0:
                hat = sdl2.SDL_JoystickGetHat(joy, 0)
                b |= (0x0001 if hat & 1 else 0) | (0x0008 if hat & 2 else 0) | \
                     (0x0002 if hat & 4 else 0) | (0x0004 if hat & 8 else 0)
            n = sdl2.SDL_JoystickNumAxes(joy)
            ax = [sdl2.SDL_JoystickGetAxis(joy, a) if a < n else 0 for a in range(4)] + [0, 0]
            lt = rt = 0
        return Pad(id=iid, name=d["name"], kind=d["kind"], buttons=b,
                   lt=max(0, min(255, lt)), rt=max(0, min(255, rt)),
                   lx=_c16(ax[0]), ly=_c16(-ax[1]), rx=_c16(ax[2]), ry=_c16(-ax[3]),
                   power=self._power(sdl2, d, joy, now), virtual=d["virtual"])

    @staticmethod
    def _power(sdl2, d, joy, now):
        if now - d["power_t"] > 2.0:
            d["power_t"] = now
            d["power"] = SDL_POWER.get(sdl2.SDL_JoystickCurrentPowerLevel(joy), "")
        return d["power"]

    # ---- XInput fallback ----
    def _run_xinput(self):
        state = XINPUT_STATE()
        connected = {}
        while self.running:
            pads = []
            for i in range(4):
                if read_pad(i, state):
                    if i not in connected:
                        connected[i] = self._is_new_virtual()
                    g = state.Gamepad
                    pads.append(Pad(id=i, name=f"Xbox-style controller #{i + 1}", kind="xbox",
                                    buttons=g.wButtons, lt=g.bLeftTrigger, rt=g.bRightTrigger,
                                    lx=g.sThumbLX, ly=g.sThumbLY, rx=g.sThumbRX, ry=g.sThumbRY,
                                    power="", virtual=connected[i]))
                else:
                    connected.pop(i, None)
            self.pads = pads
            time.sleep(0.004)


# =====================================================================
#  Settings
# =====================================================================
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
PROFILE_DIR = os.path.join(DATA_DIR, "profiles")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

DEFAULTS = {
    "cps": 20, "hold_pct": 50, "jitter_pct": 0, "mode": "hold",
    "hotkey": 0x77, "sound": True, "game_exe": "", "on_top": False,
    "kb_enabled": True, "kb_all": False, "kb_keys": [0x45], "kb_mods": False,
    "ms_enabled": False, "ms_all": False, "ms_buttons": ["left"],
    "pad_enabled": False, "pad_all": False, "pad_buttons": ["A"],
    "check_updates": True,
}


class Config:
    def __init__(self, data=None):
        self.__dict__.update(copy.deepcopy(DEFAULTS))
        if data:
            self.update(data)

    def update(self, data):
        for k, v in data.items():
            if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                setattr(self, k, copy.deepcopy(v))

    def to_dict(self):
        return {k: copy.deepcopy(getattr(self, k)) for k in DEFAULTS}


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# =====================================================================
#  Turbo engine
# =====================================================================
class Turbo:
    """Timing state for one rapid-fire button."""
    __slots__ = ("phys", "active", "out", "next", "scan", "ext")

    def __init__(self):
        self.phys = False    # physically held
        self.active = False  # rapid-firing
        self.out = False     # currently sending 'down'
        self.next = 0.0
        self.scan = 0
        self.ext = False

    def step(self, now, cps, ratio, jitter):
        """Advance timing. Returns True (send down), False (send up) or None (nothing)."""
        if not self.active:
            if self.out:
                self.out = False
                return False
            return None
        if now < self.next:
            return None
        period = 1.0 / cps
        if jitter:
            period *= 1.0 + random.uniform(-jitter, jitter)
        if self.out:
            self.out = False
            self.next = now + period * (1.0 - ratio)
            return False
        self.out = True
        self.next = now + period * ratio
        return True


class Engine:
    def __init__(self, cfg, reader):
        self.cfg = cfg
        self.reader = reader
        self.enabled = True
        self.running = True
        self.lock = threading.Lock()
        self.inputs = {}           # ("kb", vk) / ("ms", name) -> Turbo
        self.kb_phys = set()
        self.taps = 0
        self.capture = None        # "kb" / "hotkey" while waiting for a key press
        self.events = queue.Queue()
        self.my_pid = os.getpid()
        self._allow = (False, False)
        self.self_test = {"kb": False, "ms": False, "pad": False}
        self._allow_t = 0.0
        self.pad_status = "Off"
        self.pad_thread = None
        self.pad_source = None   # id of the real controller being mirrored
        # live tester data (written by hook threads, read by GUI)
        self.test_log = collections.deque(maxlen=400)   # (time, device, name, down, injected)
        self.test_kb = set()          # vks physically held
        self.test_kb_out = set()      # vks currently held down by turbo
        self.test_ms = set()
        self.test_ms_out = set()
        self.test_wheel = (0.0, "")
        self.refresh()
        self.kb_listener = pkb.Listener(win32_event_filter=self._kb_filter)
        self.ms_listener = pms.Listener(win32_event_filter=self._ms_filter)
        self.worker = threading.Thread(target=self._run, daemon=True)

    # ---------- lifecycle ----------
    def start(self):
        self.kb_listener.start()
        self.ms_listener.start()
        self.worker.start()
        self.sync_pad()

    def stop(self):
        self.running = False
        with self.lock:
            for key, t in self.inputs.items():
                t.active = False
                if t.out:
                    t.out = False
                    self._emit(key, t, False)
        for listener in (self.kb_listener, self.ms_listener):
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass

    def refresh(self):
        c = self.cfg
        self.kb_set = set(c.kb_keys)
        self.ms_set = set(c.ms_buttons)
        self.pad_set = set(c.pad_buttons)
        self._allow_t = 0.0

    def reset_all(self):
        with self.lock:
            for t in self.inputs.values():
                t.active = False

    def set_enabled(self, on):
        self.enabled = on
        self._allow_t = 0.0
        if not on:
            self.reset_all()
        self.events.put(("enabled", on))

    # ---------- where turbo is allowed ----------
    def _focus_state(self):
        """(TurboTap window is focused, focused window is OK for turbo) - cached 50 ms."""
        now = time.monotonic()
        if now - self._allow_t >= 0.05:
            pid = foreground_pid()
            own = pid == self.my_pid
            game = self.cfg.game_exe.strip().lower()
            ok = bool(pid) and not own and (not game or exe_of_pid(pid).lower() == game)
            self._allow, self._allow_t = (own, ok), now
        return self._allow

    def allowed(self, kind="kb"):
        """May turbo run right now for this device ("kb", "ms" or "pad")?
        Inside TurboTap's own window it only runs in the Tester's test areas."""
        if not self.enabled:
            return False
        own, ok = self._focus_state()
        return self.self_test.get(kind, False) if own else ok

    def is_kb_target(self, vk):
        c = self.cfg
        if not c.kb_enabled or vk == c.hotkey:
            return False
        if c.kb_all:
            if vk in NEVER_IN_ALL_VK or (vk in MODIFIER_VK and not c.kb_mods):
                return False
            return True
        return vk in self.kb_set

    def is_ms_target(self, btn):
        c = self.cfg
        return c.ms_enabled and (c.ms_all or btn in self.ms_set)

    def is_pad_target(self, name):
        c = self.cfg
        return c.pad_enabled and (c.pad_all or name in self.pad_set)

    # ---------- hooks (run on the hook threads - keep them fast) ----------
    def _kb_filter(self, msg, data):
        vk = data.vkCode
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        injected = bool(data.flags & LLKHF_INJECTED)
        self._log_kb(vk, down, injected)
        if injected:
            return False  # our own taps (and other tools') pass straight through

        first = down and vk not in self.kb_phys
        if down:
            self.kb_phys.add(vk)
        else:
            self.kb_phys.discard(vk)

        if self.capture and down:
            target, self.capture = self.capture, None
            self.events.put(("capture", target, vk))
            self.kb_listener.suppress_event()

        if first and vk == self.cfg.hotkey:
            self.set_enabled(not self.enabled)
            return False

        key = ("kb", vk)
        t = self.inputs.get(key)
        if t is None or not t.phys:
            if not down or not self.is_kb_target(vk) or not self.allowed("kb"):
                return False
        self._press(key, down, first, data.scanCode, bool(data.flags & LLKHF_EXTENDED))
        self.kb_listener.suppress_event()  # the game only sees our rapid taps
        return False

    def _ms_filter(self, msg, data):
        if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            btn = "x1" if (data.mouseData >> 16) == 1 else "x2"
            down = msg == WM_XBUTTONDOWN
        elif msg in MOUSE_MSGS:
            btn, down = MOUSE_MSGS[msg]
        elif msg == WM_MOUSEWHEEL:  # shown in the Tester only, never changed
            delta = ctypes.c_short(data.mouseData >> 16).value
            name = "wheel up" if delta > 0 else "wheel down"
            self.test_wheel = (time.perf_counter(), name)
            self.test_log.append((time.perf_counter(), "ms", name, True, bool(data.flags & LLMHF_INJECTED)))
            return False
        else:
            return False  # mouse moves: untouched
        injected = bool(data.flags & LLMHF_INJECTED)
        self._log_ms(btn, down, injected)
        if injected:
            return False
        key = ("ms", btn)
        t = self.inputs.get(key)
        if t is None or not t.phys:
            if not down or not self.is_ms_target(btn) or not self.allowed("ms"):
                return False
        self._press(key, down, down)
        self.ms_listener.suppress_event()
        return False

    def _log_kb(self, vk, down, injected):
        target = self.test_kb_out if injected else self.test_kb
        (target.add if down else target.discard)(vk)
        last = self.test_log[-1] if self.test_log else None
        if down and last and last[1] == "kb" and last[2] == vk and last[3] and last[4] == injected:
            return  # skip Windows auto-repeat spam in the log
        self.test_log.append((time.perf_counter(), "kb", vk, down, injected))

    def _log_ms(self, btn, down, injected):
        target = self.test_ms_out if injected else self.test_ms
        (target.add if down else target.discard)(btn)
        self.test_log.append((time.perf_counter(), "ms", btn, down, injected))

    def _press(self, key, down, first, scan=0, ext=False):
        with self.lock:
            t = self.inputs.get(key)
            if t is None:
                t = self.inputs[key] = Turbo()
            if scan:
                t.scan, t.ext = scan, ext
            if down:
                t.phys = True
                if first:
                    t.active = (not t.active) if self.cfg.mode == "toggle" else True
                    if t.active and not t.out:
                        t.next = 0.0
            else:
                t.phys = False
                if self.cfg.mode == "hold":
                    t.active = False

    # ---------- keyboard / mouse tapping thread ----------
    def _run(self):
        while self.running:
            c = self.cfg
            cps, ratio, jit = max(1.0, float(c.cps)), c.hold_pct / 100.0, c.jitter_pct / 100.0
            allowed = {"kb": self.allowed("kb"), "ms": self.allowed("ms")}
            now = time.perf_counter()
            todo = []
            with self.lock:
                for key, t in self.inputs.items():
                    if t.active and not allowed[key[0]]:
                        t.active = False
                    r = t.step(now, cps, ratio, jit)
                    if r is not None:
                        todo.append((key, t, r))
            for key, t, down in todo:
                if down:
                    self.taps += 1
                try:
                    self._emit(key, t, down)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.001)

    @staticmethod
    def _emit(key, t, down):
        kind, code = key
        if kind == "kb":
            scan, ext = (t.scan, t.ext) if t.scan else vk_scan(code)
            send_key(code, scan, ext, not down)
        else:
            send_mouse(code, not down)

    # ---------- controller ----------
    def sync_pad(self):
        if self.cfg.pad_enabled and (self.pad_thread is None or not self.pad_thread.is_alive()):
            self.pad_thread = threading.Thread(target=self._pad_loop, daemon=True)
            self.pad_thread.start()
        elif not self.cfg.pad_enabled and (self.pad_thread is None or not self.pad_thread.is_alive()):
            self.pad_status = "Off"

    def _pad_loop(self):
        try:
            self._pad_loop_inner()
        except Exception as e:  # noqa: BLE001
            self.pad_status = f"Error: {e}"
        finally:
            self.pad_source = None

    def _pad_loop_inner(self):
        try:
            importlib.invalidate_caches()
            import vgamepad as vg
        except Exception:  # noqa: BLE001
            self.pad_status = "Controller library isn't installed - see 'One-time setup' below."
            return
        reader = self.reader
        src = None
        while self.running and self.cfg.pad_enabled and src is None:
            src = next((p for p in reader.pads if not p.virtual), None)
            if src is None:
                self.pad_status = "No controller found - connect it by USB or Bluetooth."
                time.sleep(1.0)
        if src is None:
            return
        src_id, src_name = src.id, src.name
        reader.expect_virtual_until = time.monotonic() + 5.0  # the next new pad is our virtual one
        try:
            vpad = vg.VX360Gamepad()
        except Exception as e:  # noqa: BLE001
            reader.expect_virtual_until = 0.0
            self.pad_status = f"Couldn't create the virtual controller - install the ViGEmBus driver. ({e})"
            return
        self.pad_source = src_id
        running_msg = f"Running: {src_name} -> virtual Xbox 360 controller"
        self.pad_status = running_msg
        turbos = {name: Turbo() for name, _ in PAD_BUTTONS}
        last = None
        try:
            while self.running and self.cfg.pad_enabled:
                g = reader.get(src_id)
                if g is None:  # unplugged - wait for it (or another real pad) to come back
                    self.pad_status = f"{src_name} disconnected - waiting..."
                    if last is not None:
                        vpad.reset()
                        vpad.update()
                        last = None
                    again = next((p for p in reader.pads if not p.virtual and p.name == src_name), None) or \
                        next((p for p in reader.pads if not p.virtual), None)
                    if again:
                        src_id, src_name = again.id, again.name
                        self.pad_source = src_id
                        running_msg = f"Running: {src_name} -> virtual Xbox 360 controller"
                    time.sleep(0.5)
                    continue
                if self.pad_status != running_msg:
                    self.pad_status = running_msg
                c = self.cfg
                cps, ratio, jit = max(1.0, float(c.cps)), c.hold_pct / 100.0, c.jitter_pct / 100.0
                allowed = self.allowed("pad")
                now = time.perf_counter()
                out_b, out_lt, out_rt = g.buttons, g.lt, g.rt
                for name, bit in PAD_BUTTONS:
                    t = turbos[name]
                    if bit is None:
                        pressed = (g.lt if name == "LT" else g.rt) > TRIGGER_THRESHOLD
                    else:
                        pressed = bool(g.buttons & bit)
                    if not (allowed and self.is_pad_target(name)):
                        t.active = t.out = False
                        t.phys = pressed
                        continue  # normal pass-through
                    if pressed and not t.phys:
                        t.active = (not t.active) if c.mode == "toggle" else True
                        if t.active:
                            t.next = 0.0
                    elif not pressed and t.phys and c.mode == "hold":
                        t.active = False
                    t.phys = pressed
                    if t.step(now, cps, ratio, jit) is True:
                        self.taps += 1
                    if name == "LT":
                        out_lt = 255 if t.out else 0
                    elif name == "RT":
                        out_rt = 255 if t.out else 0
                    else:
                        out_b = (out_b | bit) if t.out else (out_b & ~bit)
                cur = (out_b, out_lt, out_rt, g.lx, g.ly, g.rx, g.ry)
                if cur != last:
                    vpad.reset()
                    for bit in OUTPUT_BITS:
                        if out_b & bit:
                            vpad.press_button(button=bit)
                    vpad.left_trigger(value=out_lt)
                    vpad.right_trigger(value=out_rt)
                    vpad.left_joystick(x_value=g.lx, y_value=g.ly)
                    vpad.right_joystick(x_value=g.rx, y_value=g.ry)
                    vpad.update()
                    last = cur
                time.sleep(0.002)
        finally:
            try:
                vpad.reset()
                vpad.update()
            except Exception:  # noqa: BLE001
                pass
            del vpad
            self.pad_status = "Off"


def snapshot(dq):
    """Copy a deque that another thread may be appending to."""
    for _ in range(5):
        try:
            return list(dq)
        except RuntimeError:
            continue
    return []


# =====================================================================
#  GUI
# =====================================================================
BG = "#17181d"
PANEL = "#22242c"
BORDER = "#343744"
FG = "#e9eaf0"
MUTED = "#9096a5"
ACCENT = "#5b8cff"
GREEN = "#3ecf6e"
RED = "#ff5d5d"
ORANGE = "#ffb648"
IDLE = "#3a3d4a"


def F(size):
    """Every font in the app: Segoe UI Bold."""
    return ("Segoe UI", size, "bold")


FONT = F(10)
SV_THEME = has_module("sv_ttk")

# Controller line art (PNG, embedded at the bottom of this file) + where each button sits on it,
# in the art's original pixel coordinates (scaled by ART_SCALE when drawn).
ART_SCALE = 0.78
MOUSE_SCALE = 0.5
ART_LAYOUTS = {
    "ps": dict(
        art="PS", face="ps", labels=("L2", "R2", "L1", "R1", "Share", "Options"),
        triggers={"LT": (86, 10, 100, 42), "RT": (300, 10, 314, 42)},
        rects={"LB": (68, 48, 104, 63), "RB": (294, 48, 330, 63), "Touch": (139, 82, 258, 146),
               "D-Up": (73, 106, 92, 126), "D-Down": (73, 140, 92, 161),
               "D-Left": (54, 125, 76, 144), "D-Right": (90, 125, 110, 144)},
        ovals={"Back": (117, 88, 125, 108), "Start": (269, 88, 277, 108)},
        circles={"Y": (315, 107, 12), "X": (288, 132, 12), "B": (340, 133, 12), "A": (315, 158, 12),
                 "Guide": (199, 180, 18), "L-Stick": (137, 186, 17), "R-Stick": (257, 186, 17)},
        guide_ring=False),
    "xbox": dict(
        art="XBOX", face="xbox", labels=("LT", "RT", "LB", "RB", "View", "Menu"),
        triggers={"LT": (123, 10, 136, 40), "RT": (286, 10, 300, 40)},
        rects={"LB": (105, 47, 139, 61), "RB": (283, 47, 316, 61),
               "D-Up": (156, 178, 169, 192), "D-Down": (156, 208, 169, 222),
               "D-Left": (139, 194, 154, 207), "D-Right": (173, 194, 187, 207)},
        ovals={"Back": (177, 135, 191, 149), "Start": (230, 135, 244, 149)},
        circles={"Y": (303, 117, 12), "X": (279, 142, 12), "B": (330, 142, 12), "A": (303, 166, 12),
                 "Guide": (210, 101, 15), "L-Stick": (116, 141, 25), "R-Stick": (258, 199, 25)},
        guide_ring=True),
}
ART_LAYOUTS["nintendo"] = dict(ART_LAYOUTS["xbox"], face="nintendo", labels=("ZL", "ZR", "L", "R", "-", "+"))
FACE_STYLES = {
    "xbox": {"A": ("A", "#3ecf6e"), "B": ("B", "#ff5d5d"), "X": ("X", "#5b8cff"), "Y": ("Y", "#ffd23f")},
    "nintendo": {"A": ("B", "#e9eaf0"), "B": ("A", "#e9eaf0"), "X": ("Y", "#e9eaf0"), "Y": ("X", "#e9eaf0")},
    "ps": {"A": ("cross", "#7aa7ff"), "B": ("circle", "#ff6b6b"), "X": ("square", "#f08ad8"),
           "Y": ("triangle", "#4fd1a5")},
}


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = Config(load_json(SETTINGS_FILE))
        self.reader = PadReader()
        self.reader.start()
        self.engine = Engine(self.cfg, self.reader)
        self._icons = [tk.PhotoImage(data=ICON_256), tk.PhotoImage(data=ICON_32)]
        self._icon48 = tk.PhotoImage(data=ICON_48)
        root.iconphoto(True, *self._icons)
        self._loading = False
        self._save_job = None
        self._ui_cache = {}
        self._prev_mode = self.cfg.mode
        self._last_taps, self._last_t = 0, time.monotonic()

        root.title(f"{APP_NAME} {APP_VERSION}")
        root.report_callback_exception = self._on_tk_error
        self._style()
        self._build()
        self._load_into_ui()
        self.lbl_pad.config(text="\n\n\n")  # reserve room for status + restart warning before sizing
        root.update_idletasks()
        w, h = max(root.winfo_reqwidth(), 700), root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - w) // 2)
        y = max(0, (root.winfo_screenheight() - h) // 2 - 20)
        root.geometry(f"{w}x{h}+{x}+{y}")  # fixed size, centred on screen
        root.minsize(w, h)
        if self.cfg.check_updates:
            root.after(1500, lambda: self.check_updates(manual=False))
        self.engine.start()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll()
        self._tester_tick()

    # ---------------- look ----------------
    def _style(self):
        global BG, PANEL, BORDER, FG, MUTED, ACCENT, IDLE
        r = self.root
        s = ttk.Style(r)
        self.sv = False
        if SV_THEME:
            try:
                import sv_ttk
                sv_ttk.set_theme("dark")
                self.sv = True
            except Exception:  # noqa: BLE001
                self.sv = False
        if self.sv:  # modern Windows 11 look
            BG = PANEL = "#1c1c1c"
            BORDER, IDLE, FG, MUTED, ACCENT = "#3a3a3a", "#3b3d45", "#fafafa", "#9a9a9a", "#57c8ff"
        else:
            s.theme_use("clam")
            s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL, troughcolor=BG, focuscolor=PANEL)
            s.configure("TFrame", background=BG)
            s.configure("Card.TFrame", background=PANEL)
            for w in ("TCheckbutton", "TRadiobutton"):
                s.configure(w, background=PANEL, foreground=FG, indicatorbackground=BG, indicatorforeground=FG)
                s.map(w, background=[("active", PANEL)], indicatorbackground=[("selected", ACCENT)],
                      foreground=[("disabled", MUTED)])
            s.configure("TButton", background=BORDER, foreground=FG, borderwidth=0, padding=(10, 5))
            s.map("TButton", background=[("pressed", ACCENT), ("active", "#43475a")],
                  foreground=[("disabled", MUTED)])
            s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
            s.map("Accent.TButton", background=[("active", "#7aa2ff")])
            s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
            s.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(14, 7), borderwidth=0)
            s.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", FG)])
            s.configure("TCombobox", fieldbackground=PANEL, background=BORDER, foreground=FG, arrowcolor=FG,
                        selectbackground=PANEL, selectforeground=FG)
            s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
            r.option_add("*TCombobox*Listbox.background", PANEL)
            r.option_add("*TCombobox*Listbox.foreground", FG)
            r.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        r.configure(bg=BG)
        self.row_style = "TFrame" if self.sv else "Card.TFrame"  # frames inside a card: no border
        # Segoe UI Bold everywhere (theme fonts, default fonts, list popups)
        for name in tkfont.names(r):
            try:
                tkfont.nametofont(name, root=r).configure(family="Segoe UI", weight="bold")
            except tk.TclError:
                pass
        r.option_add("*TCombobox*Listbox.font", F(10))
        s.configure(".", font=F(10))
        lab = {} if self.sv else {"background": PANEL}
        s.configure("Card.TLabel", foreground=FG, font=F(10), **lab)
        s.configure("Head.TLabel", foreground=FG, font=F(12), **lab)
        s.configure("Muted.TLabel", foreground=MUTED, font=F(9), **lab)
        top = {} if self.sv else {"background": BG}
        s.configure("TLabel", foreground=FG, font=F(10), **top)
        s.configure("Title.TLabel", foreground=FG, font=F(20), **top)
        s.configure("Status.TLabel", foreground=MUTED, font=F(10), **top)
        s.configure("Big.TLabel", foreground=FG, font=F(22), **top)
        s.configure("TNotebook.Tab", font=F(10))
        s.configure("Done.TButton", foreground=GREEN)
        s.map("Done.TButton", foreground=[("disabled", GREEN)])
        s.configure("Warn.TButton", foreground=ORANGE)
        s.map("Warn.TButton", foreground=[("disabled", ORANGE)])

    def _card(self, parent, title=None, **pack):
        f = ttk.Frame(parent, style="Card.TFrame", padding=14 if self.sv else 12)
        f.pack(fill="x", **{"pady": (0, 10), **pack})
        if title:
            ttk.Label(f, text=title, style="Head.TLabel").pack(anchor="w", pady=(0, 6))
        return f

    def _slider(self, parent, label, var, lo, hi, fmt):
        row = ttk.Frame(parent, style=self.row_style)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, style="Card.TLabel", width=15).pack(side="left")
        val = ttk.Label(row, style="Card.TLabel", width=12, anchor="e")
        val.pack(side="right")
        if self.sv:
            sc = ttk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=var,
                           command=lambda v: var.set(int(round(float(v)))))
        else:
            sc = tk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=var, showvalue=False,
                          resolution=1, bg=ACCENT, fg=FG, troughcolor=BORDER, activebackground="#8fb0ff",
                          highlightthickness=0, bd=0, sliderrelief="flat", sliderlength=16, width=10,
                          cursor="hand2")
        sc.pack(side="left", fill="x", expand=True, padx=8)

        def upd(*_):
            val.config(text=fmt(int(var.get())))
        var.trace_add("write", upd)
        upd()

    # ---------------- layout ----------------
    def _build(self):
        r = self.root
        outer = ttk.Frame(r, padding=14)
        outer.pack(fill="both", expand=True)

        # header
        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 10))
        ttk.Label(head, image=self._icon48).pack(side="left", padx=(0, 12))
        left = ttk.Frame(head)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        self.lbl_status = ttk.Label(left, text="", style="Status.TLabel", width=52)
        self.lbl_status.pack(anchor="w")
        self.btn_power = tk.Button(head, text="OFF", width=7, font=F(13), bd=0,
                                   fg="#0b1a10", activeforeground="#0b1a10", cursor="hand2",
                                   command=lambda: self.engine.set_enabled(not self.engine.enabled))
        self.btn_power.pack(side="right", padx=(10, 0), ipady=6)
        rate = ttk.Frame(head)
        rate.pack(side="right")
        self.lbl_rate = ttk.Label(rate, text="0.0", style="Big.TLabel", anchor="e", width=5)
        self.lbl_rate.pack(anchor="e")
        ttk.Label(rate, text="taps / sec", style="Status.TLabel").pack(anchor="e")

        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True)
        self._tab_main()
        self._tab_keyboard()
        self._tab_mouse()
        self._tab_pad()
        self._tab_tester()
        self._tab_about()

        # footer
        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(10, 0))
        if is_admin():
            ttk.Label(foot, text="Running as Administrator", style="Status.TLabel").pack(side="left")
        else:
            ttk.Label(foot, text="Not admin - games running as admin won't receive taps.",
                      style="Status.TLabel").pack(side="left")
            ttk.Button(foot, text="Restart as admin", command=self.restart_admin).pack(side="right")

    def _tab_main(self):
        tab = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(tab, text="  Speed & Options  ")

        self.v_cps = tk.IntVar()
        self.v_hold = tk.IntVar()
        self.v_jit = tk.IntVar()
        self.v_mode = tk.StringVar()
        c = self._card(tab, "Speed")
        self._slider(c, "Taps per second", self.v_cps, 1, 50, lambda v: f"{v} / sec")
        self._slider(c, "Press length", self.v_hold, 10, 90, lambda v: f"{v}% down")
        self._slider(c, "Randomize", self.v_jit, 0, 40, lambda v: "off" if v == 0 else f"±{v}%")
        ttk.Label(c, text="Too fast for your game? Lower the speed or raise the press length. "
                          "Randomize makes the timing look more human.",
                  style="Muted.TLabel", wraplength=500).pack(anchor="w", pady=(6, 0))

        c = self._card(tab, "Mode")
        ttk.Radiobutton(c, text="Hold - rapid-fire while you hold the button", value="hold",
                        variable=self.v_mode).pack(anchor="w")
        ttk.Radiobutton(c, text="Toggle - press once to start auto-tapping, press again to stop",
                        value="toggle", variable=self.v_mode).pack(anchor="w")

        c = self._card(tab, "Options")
        row = ttk.Frame(c, style=self.row_style)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Only work in:", style="Card.TLabel", width=15).pack(side="left")
        self.v_game = tk.StringVar()
        self.cb_game = ttk.Combobox(row, textvariable=self.v_game, width=30)
        self.cb_game.pack(side="left", padx=8, fill="x", expand=True)
        self.cb_game.bind("<<ComboboxSelected>>", self._apply)
        self.cb_game.bind("<FocusOut>", self._apply)
        self.cb_game.bind("<Return>", self._apply)
        ttk.Button(row, text="Refresh", command=self._refresh_games).pack(side="left")

        row = ttk.Frame(c, style=self.row_style)
        row.pack(fill="x", pady=(8, 2))
        ttk.Label(row, text="On/off hotkey:", style="Card.TLabel", width=15).pack(side="left")
        self.btn_hotkey = ttk.Button(row, text="F8", width=22, command=lambda: self._start_capture("hotkey"))
        self.btn_hotkey.pack(side="left", padx=8)

        self.v_sound = tk.BooleanVar()
        self.v_top = tk.BooleanVar()
        ttk.Checkbutton(c, text="Play a sound when turned on/off", variable=self.v_sound).pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(c, text="Keep this window on top", variable=self.v_top).pack(anchor="w")

        c = self._card(tab, "Profiles")
        row = ttk.Frame(c, style=self.row_style)
        row.pack(fill="x")
        self.v_profile = tk.StringVar()
        self.cb_profile = ttk.Combobox(row, textvariable=self.v_profile, state="readonly", width=22)
        self.cb_profile.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Load", command=self.profile_load).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Save as...", command=self.profile_save).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Delete", command=self.profile_delete).pack(side="left", padx=(6, 0))

    def _device_header(self, parent, enable_var, all_var, label):
        c = self._card(parent)
        ttk.Checkbutton(c, text=f"Enable {label} turbo", variable=enable_var,
                        style="Switch.TCheckbutton" if self.sv else "TCheckbutton").pack(anchor="w")
        row = ttk.Frame(c, style=self.row_style)
        row.pack(anchor="w", pady=(6, 0))
        ttk.Radiobutton(row, text="Only the buttons I pick", value=0, variable=all_var).pack(side="left")
        ttk.Radiobutton(row, text="All buttons", value=1, variable=all_var).pack(side="left", padx=(16, 0))
        return c

    def _tab_keyboard(self):
        tab = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(tab, text="  Keyboard  ")
        self.v_kb_on = tk.BooleanVar()
        self.v_kb_all = tk.BooleanVar()
        self.v_kb_mods = tk.BooleanVar()
        c = self._device_header(tab, self.v_kb_on, self.v_kb_all, "keyboard")
        ttk.Checkbutton(c, text="'All buttons' includes Shift / Ctrl / Alt", variable=self.v_kb_mods
                        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(c, text="'All buttons' never touches the Windows key, Caps/Num/Scroll Lock or your hotkey.",
                  style="Muted.TLabel").pack(anchor="w")

        c = self._card(tab, "Keys I picked")
        self.lst_keys = tk.Listbox(c, height=7, bg="#232323" if self.sv else BG, fg=FG, selectbackground=ACCENT, bd=0,
                                   highlightthickness=0, font=F(11), activestyle="none")
        self.lst_keys.pack(fill="x")
        row = ttk.Frame(c, style=self.row_style)
        row.pack(fill="x", pady=(8, 0))
        self.btn_addkey = ttk.Button(row, text="+ Add key", style="Accent.TButton",
                                     command=lambda: self._start_capture("kb"))
        self.btn_addkey.pack(side="left")
        ttk.Button(row, text="Remove", command=self._remove_key).pack(side="left", padx=6)
        ttk.Button(row, text="Clear", command=self._clear_keys).pack(side="left")

    def _tab_mouse(self):
        tab = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(tab, text="  Mouse  ")
        self.v_ms_on = tk.BooleanVar()
        self.v_ms_all = tk.BooleanVar()
        self._device_header(tab, self.v_ms_on, self.v_ms_all, "mouse")
        c = self._card(tab, "Buttons I picked")
        self.v_ms_btn = {}
        for key, name in MOUSE_NAMES.items():
            v = tk.BooleanVar()
            v.trace_add("write", self._apply)
            self.v_ms_btn[key] = v
            ttk.Checkbutton(c, text=name, variable=v).pack(anchor="w")
        ttk.Label(c, text="Tip: Toggle mode + Left click = auto-clicker. Clicks inside TurboTap itself "
                          "are never affected, so you can always reach the settings.",
                  style="Muted.TLabel", wraplength=500).pack(anchor="w", pady=(8, 0))

    def _tab_pad(self):
        tab = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(tab, text="  Controller  ")
        self.v_pad_on = tk.BooleanVar()
        self.v_pad_all = tk.BooleanVar()
        c = self._device_header(tab, self.v_pad_on, self.v_pad_all, "controller")
        self.lbl_pad = ttk.Label(c, text="", style="Muted.TLabel", wraplength=520)
        self.lbl_pad.pack(anchor="w", pady=(8, 0))

        c = self._card(tab, "Buttons I picked")
        grid = ttk.Frame(c, style=self.row_style)
        grid.pack(anchor="w")
        self.v_pad_btn = {}
        for i, (name, _bit) in enumerate(PAD_BUTTONS):
            v = tk.BooleanVar()
            v.trace_add("write", self._apply)
            self.v_pad_btn[name] = v
            ttk.Checkbutton(grid, text=name, variable=v, width=10).grid(row=i // 4, column=i % 4, sticky="w")

        c = self._card(tab, "One-time setup")
        self.setup_btns = {}
        for key, title, desc in (
                ("vg", "1. Controller library", "vgamepad - lets TurboTap create a virtual controller"),
                ("vigem", "2. ViGEmBus driver", "the virtual Xbox 360 controller driver"),
                ("hidhide", "3. HidHide", "hides your real controller from games (restart PC after installing)")):
            row = ttk.Frame(c, style=self.row_style)
            row.pack(fill="x", pady=3)
            txt = ttk.Frame(row, style=self.row_style)
            txt.pack(side="left", fill="x", expand=True)
            ttk.Label(txt, text=title, style="Card.TLabel").pack(anchor="w")
            ttk.Label(txt, text=desc, style="Muted.TLabel").pack(anchor="w")
            btn = ttk.Button(row, width=19, command=lambda k=key: self._setup_install(k))
            btn.pack(side="right")
            self.setup_btns[key] = btn
        self.btn_hh_open = ttk.Button(c, text="Open HidHide settings", command=self._open_hidhide)
        self.lbl_hh_help = ttk.Label(c, style="Muted.TLabel", wraplength=520, justify="left", text=(
            "In HidHide: on the 'Applications' tab add this program so TurboTap can still read your "
            f"controller:\n    {TURBOTAP_EXE}\n"
            "On the 'Devices' tab tick your real controller and turn on 'Enable device hiding'. "
            "Without this the game sees both controllers and the real held button cancels the turbo."))
        self.lbl_hh_help.pack(anchor="w", pady=(8, 0))
        self._busy = {}
        self._setup_refresh()

    # ---------------- Tester tab ----------------
    def _tab_tester(self):
        tab = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(tab, text="  Tester  ")
        self.tab_tester = tab
        self.tnb = ttk.Notebook(tab)
        self.tnb.pack(fill="both", expand=True)
        self._log_seen = 0.0
        self._keys_sig = None

        # ---- Keyboard ----
        kt = ttk.Frame(self.tnb, padding=(0, 10, 0, 0))
        self.tnb.add(kt, text="  Keyboard  ")
        self.ttab_kb = kt
        c = self._card(kt, "Hold a key")
        ttk.Label(c, text="Turbo runs here while this tab is open - hold one of your turbo keys to see it "
                          "tapping. White = you're holding it, blue = a tap TurboTap sent.",
                  style="Muted.TLabel", wraplength=600).pack(anchor="w")
        self.cv_keys = tk.Canvas(c, width=620, height=120, bg=PANEL, highlightthickness=0)
        self.cv_keys.pack(anchor="center", pady=(8, 0))
        self.t_kb_rate = ttk.Label(c, text="", style="Head.TLabel", anchor="center")
        self.t_kb_rate.pack(fill="x", pady=(4, 0))
        c = self._card(kt, "Keyboard events")
        self.lst_kb = self._event_list(c)

        # ---- Mouse ----
        mt = ttk.Frame(self.tnb, padding=(0, 10, 0, 0))
        self.tnb.add(mt, text="  Mouse  ")
        self.ttab_ms = mt
        row = ttk.Frame(mt)
        row.pack(fill="x", pady=(0, 10))
        mc = ttk.Frame(row, style="Card.TFrame", padding=14)
        mc.pack(side="left", fill="y")
        ttk.Label(mc, text="Test area", style="Head.TLabel").pack(anchor="center")
        self.cv_mouse = tk.Canvas(mc, width=int(432 * MOUSE_SCALE), height=int(600 * MOUSE_SCALE), bg=PANEL,
                                  highlightthickness=0, cursor="hand2")
        self.cv_mouse.pack(anchor="center", pady=(6, 0))
        self._mouse_hover = False
        self.cv_mouse.bind("<Enter>", lambda _e: setattr(self, "_mouse_hover", True))
        self.cv_mouse.bind("<Leave>", lambda _e: setattr(self, "_mouse_hover", False))
        self._draw_mouse()
        ic = ttk.Frame(row, style="Card.TFrame", padding=14)
        ic.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ttk.Label(ic, text="Clicks", style="Head.TLabel").pack(anchor="w")
        ttk.Label(ic, text="Mouse turbo runs only while the pointer is over the mouse picture, so the rest "
                           "of TurboTap stays clickable. Hold a turbo button there: white = you're holding it, "
                           "blue = a click TurboTap sent.", style="Muted.TLabel", wraplength=330
                  ).pack(anchor="w", pady=(2, 10))
        self.t_ms_state = ttk.Label(ic, text="-", style="Head.TLabel", wraplength=330)
        self.t_ms_state.pack(anchor="w")
        self.t_ms_rate = ttk.Label(ic, text="", style="Head.TLabel", foreground=GREEN)
        self.t_ms_rate.pack(anchor="w", pady=(4, 10))
        self.lst_ms = self._event_list(ic, height=8)

        # ---- Gamepad ----
        gt = ttk.Frame(self.tnb, padding=(0, 10, 0, 0))
        self.tnb.add(gt, text="  Gamepad  ")
        self.ttab_pad = gt
        pc = self._card(gt)
        head = ttk.Frame(pc, style=self.row_style)
        head.pack(fill="x")
        ttk.Label(head, text="Controller", style="Head.TLabel").pack(side="left")
        self.v_test_pad = tk.StringVar()
        self.cb_test_pad = ttk.Combobox(head, textvariable=self.v_test_pad, state="readonly", width=34,
                                        values=["No controller connected"])
        self.cb_test_pad.pack(side="right")
        self.cv_pad = tk.Canvas(pc, width=620, height=262, bg=PANEL, highlightthickness=0)
        self.cv_pad.pack(anchor="center", pady=(6, 0))
        self.lbl_padinfo = ttk.Label(pc, text="", style="Muted.TLabel", anchor="center")
        self.lbl_padinfo.pack(fill="x")
        ttk.Label(pc, text="Your real controller shows what you press (holding). To see the turbo, pick the "
                           "'TurboTap virtual' controller in the list - that's what games receive.",
                  style="Muted.TLabel", wraplength=600, anchor="center", justify="center").pack(fill="x", pady=(6, 0))
        self._test_ids = []
        self.pad_kind = None
        self._pad_last = None
        self._art = {"PS": tk.PhotoImage(data=PAD_ART_PS), "XBOX": tk.PhotoImage(data=PAD_ART_XBOX)}
        self._draw_pad("xbox")

    # ---------------- About ----------------
    def _tab_about(self):
        tab = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(tab, text="  About  ")
        c = self._card(tab)
        row = ttk.Frame(c, style=self.row_style)
        row.pack(fill="x")
        self._about_icon = tk.PhotoImage(data=ICON_256).subsample(2)
        ttk.Label(row, image=self._about_icon).pack(side="left", padx=(0, 18))
        info = ttk.Frame(row, style=self.row_style)
        info.pack(side="left", fill="both", expand=True)
        ttk.Label(info, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(info, text=f"Version {APP_VERSION}", style="Head.TLabel").pack(anchor="w")
        ttk.Label(info, text="Rapid-fire for your keyboard, mouse and game controller. Hold a button and "
                             "TurboTap taps it for you - or press once to auto-tap.",
                  style="Card.TLabel", wraplength=430).pack(anchor="w", pady=(8, 8))
        ttk.Label(info, text=f"Made by {APP_AUTHOR}", style="Head.TLabel").pack(anchor="w")
        ttk.Label(info, text="Free and open source - MIT License", style="Muted.TLabel").pack(anchor="w")

        c = self._card(tab, "Updates")
        self.lbl_update = ttk.Label(c, text=f"You have version {APP_VERSION}.", style="Card.TLabel")
        self.lbl_update.pack(anchor="w")
        r = ttk.Frame(c, style=self.row_style)
        r.pack(fill="x", pady=(8, 0))
        ttk.Button(r, text="Check for updates", command=lambda: self.check_updates(manual=True)
                   ).pack(side="left")
        self.btn_get_update = ttk.Button(r, text="Download update", style="Accent.TButton",
                                         command=self._open_update)
        self.v_check_updates = tk.BooleanVar(value=self.cfg.check_updates)
        ttk.Checkbutton(c, text="Check for updates when TurboTap starts", variable=self.v_check_updates,
                        command=self._toggle_update_check).pack(anchor="w", pady=(8, 0))
        self._update_url = None

        c = self._card(tab, "Links")
        r = ttk.Frame(c, style=self.row_style)
        r.pack(fill="x")
        ttk.Button(r, text="GitHub page", command=lambda: webbrowser.open(REPO_URL)).pack(side="left")
        ttk.Button(r, text="Report a problem", command=lambda: webbrowser.open(REPO_URL + "/issues")
                   ).pack(side="left", padx=6)
        ttk.Button(r, text="\u2615  Buy me a coffee", command=lambda: webbrowser.open(DONATE_URL)
                   ).pack(side="left")

        c = self._card(tab, "Thanks to")
        ttk.Label(c, style="Muted.TLabel", wraplength=640, justify="left", text=(
            "SDL2 (via PySDL2) for reading controllers  \u2022  ViGEmBus and HidHide by Nefarius  \u2022  "
            "vgamepad  \u2022  pynput  \u2022  Sun Valley theme (sv-ttk)")).pack(anchor="w")

    def _toggle_update_check(self):
        self.cfg.check_updates = bool(self.v_check_updates.get())
        self._schedule_save()

    def check_updates(self, manual=False):
        self._set(self.lbl_update, text="Checking for updates...", foreground=FG)

        def work():
            try:
                info, err = latest_release(), None
            except Exception as e:  # noqa: BLE001
                info, err = None, e
            self._ui(lambda: self._update_result(info, err, manual))
        threading.Thread(target=work, daemon=True).start()

    def _update_result(self, info, err, manual):
        if err is not None:
            self._set(self.lbl_update, text="Couldn't check for updates - are you online?", foreground=ORANGE)
            if manual:
                messagebox.showwarning(APP_NAME, f"Couldn't check for updates.\n\n{err}")
            return
        ver, url = info
        if version_tuple(ver) > version_tuple(APP_VERSION):
            self._update_url = url
            self._set(self.lbl_update, text=f"TurboTap {ver} is available (you have {APP_VERSION}).",
                      foreground=GREEN)
            if not self.btn_get_update.winfo_ismapped():
                self.btn_get_update.pack(side="left", padx=6)
            if messagebox.askyesno(APP_NAME, f"TurboTap {ver} is available.\nYou have version {APP_VERSION}."
                                             "\n\nDownload it now?"):
                self._open_update()
        else:
            self._set(self.lbl_update, text=f"You have the latest version ({APP_VERSION}).", foreground=GREEN)
            if manual:
                messagebox.showinfo(APP_NAME, f"You have the latest version ({APP_VERSION}).")

    def _open_update(self):
        webbrowser.open(self._update_url or REPO_URL + "/releases/latest")

    def _event_list(self, parent, height=6):
        lst = tk.Listbox(parent, height=height, bg="#232323" if self.sv else BG, fg=FG, bd=0,
                         highlightthickness=0, font=F(9), activestyle="none")
        lst.pack(fill="both", expand=True)
        return lst

    def _set(self, widget, **kw):
        """Configure a widget only when something actually changed (avoids relayout / lag)."""
        key = id(widget)
        val = tuple(sorted(kw.items()))
        if self._ui_cache.get(key) != val:
            self._ui_cache[key] = val
            widget.config(**kw)

    def _draw_mouse(self):
        """Mouse diagram: left / right / wheel (with scroll arrows) / forward / back side buttons."""
        cv = self.cv_mouse
        k = MOUSE_SCALE
        T = lambda *pts: [v * k for v in pts]  # noqa: E731
        body, idle, line = "#1d1e24", "#2a2b33", "#4a4b56"
        cx, cy, r = 215, 197, 135            # top dome of the mouse (original 432 x 600 layout)

        def arc(a0, a1, step):
            pts = []
            for a in range(a0, a1 + step if step > 0 else a1 - 1, step):
                rad = math.radians(a)
                pts += [cx + r * math.cos(rad), cy - r * math.sin(rad)]
            return pts
        # body: pill shape
        bottom = []
        for a in range(0, -181, -5):
            rad = math.radians(a)
            bottom += [cx + r * math.cos(rad), 437 - r * math.sin(rad)]
        cv.create_polygon(*T(*([80, 197] + arc(180, 0, -5) + [350, 437] + bottom + [80, 437])),
                          fill=body, outline=line, width=2)
        # buttons
        left = [80, 227, 80, 197] + arc(180, 95, -5) + [205, cy - r * math.sin(math.radians(95)), 205, 227]
        right = [225, 227, 225, cy - r * math.sin(math.radians(85))] + arc(85, 0, -5) + [350, 197, 350, 227]
        self.m_items = {
            "left": cv.create_polygon(*T(*left), fill=idle, outline=line),
            "right": cv.create_polygon(*T(*right), fill=idle, outline=line),
            "middle": cv.create_oval(*T(192, 122, 238, 218), fill=idle, outline=line, width=2),
            "x2": cv.create_rectangle(*T(47, 280, 80, 363), fill=idle, outline=line),
            "x1": cv.create_rectangle(*T(47, 388, 80, 471), fill=idle, outline=line),
            "wheel up": cv.create_polygon(*T(215, 98, 203, 116, 227, 116), fill="#5a5c68", outline=""),
            "wheel down": cv.create_polygon(*T(215, 242, 203, 224, 227, 224), fill="#5a5c68", outline=""),
        }
        self.m_labels = {}
        for btn, text, x, y in (("left", "LEFT", 140, 160), ("right", "RIGHT", 290, 160),
                                ("x2", "FWD", 63, 262), ("x1", "BACK", 63, 492)):
            self.m_labels[btn] = cv.create_text(x * k, y * k, text=text, fill="#a9abb6", font=F(8))

    def _draw_pad(self, kind):
        """Controller line art for the pad's layout, with highlight shapes on top of each button."""
        L = ART_LAYOUTS.get(kind) or ART_LAYOUTS["xbox"]
        self.pad_kind = kind
        self._pad_last = None
        cv = self.cv_pad
        cv.delete("all")
        img = self._art[L["art"]]
        H = 262
        S = ART_SCALE
        ox, oy = 18, (H - img.height()) // 2
        T = lambda x, y: (ox + x * S, oy + y * S)  # noqa: E731
        cv.create_image(ox, oy, image=img, anchor="nw")
        p = {}
        self.trig_rng = {}
        for name, (x0, y0, x1, y1) in L["triggers"].items():
            a, b = T(x0, y0)
            c, d = T(x1, y1)
            self.trig_rng[name] = (a, b, c, d)
            p[name] = cv.create_rectangle(a, d, c, d, fill=ORANGE, outline="")
        for name, (x0, y0, x1, y1) in L["rects"].items():
            p[name] = cv.create_rectangle(*T(x0, y0), *T(x1, y1), fill=ACCENT, outline="", state="hidden")
        for name, (x0, y0, x1, y1) in L["ovals"].items():
            p[name] = cv.create_oval(*T(x0, y0), *T(x1, y1), fill=ACCENT, outline="", state="hidden")
        style = FACE_STYLES[L["face"]]
        self.pad_colors = {}
        for name, (x, y, r) in L["circles"].items():
            (cx, cy), rr = T(x, y), r * S
            if name == "Guide" and L["guide_ring"]:
                cv.create_oval(cx - rr - 2, cy - rr - 2, cx + rr + 2, cy + rr + 2, outline="#b2b8c8", width=1)
            col = style[name][1] if name in style else ACCENT
            p[name] = cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=col, outline="", state="hidden")
            if name in style:  # face button symbol, always visible
                self.pad_colors[name] = col
                sym, k = style[name][0], rr * 0.42
                if sym == "cross":
                    items = [cv.create_line(cx - k, cy - k, cx + k, cy + k, fill=col, width=2),
                             cv.create_line(cx - k, cy + k, cx + k, cy - k, fill=col, width=2)]
                elif sym == "circle":
                    items = [cv.create_oval(cx - k, cy - k, cx + k, cy + k, outline=col, width=2)]
                elif sym == "square":
                    items = [cv.create_rectangle(cx - k, cy - k, cx + k, cy + k, outline=col, width=2)]
                elif sym == "triangle":
                    items = [cv.create_polygon(cx, cy - k * 1.2, cx - k * 1.1, cy + k * 0.8, cx + k * 1.1,
                                               cy + k * 0.8, outline=col, fill="", width=2)]
                else:
                    items = [cv.create_text(cx, cy, text=sym, fill=col, font=F(8))]
                p[name + "sym"] = items
            if name.endswith("Stick"):  # dot that follows the stick
                p[name + "dot"] = cv.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=FG, outline="")
                p[name + "c"] = (cx, cy, rr)
        # stick crosshairs + trigger bars on the right
        lt_l, rt_l = L["labels"][0], L["labels"][1]
        self.cross = {}
        for name, cx, title in (("L-Stick", 428, "Left stick"), ("R-Stick", 556, "Right stick")):
            cy, r = 92, 52
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#5a5f6e", width=2)
            cv.create_line(cx - r, cy, cx + r, cy, fill="#3e424e")
            cv.create_line(cx, cy - r, cx, cy + r, fill="#3e424e")
            self.cross[name] = (cx, cy, r, cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=ACCENT, outline=""),
                                cv.create_text(cx, cy + r + 24, text="X +0.00   Y +0.00", fill=MUTED, font=F(8)))
            cv.create_text(cx, cy + r + 10, text=title, fill=FG, font=F(9))
        self.trig_bars = {}
        for name, x0, lab in (("LT", 378, lt_l), ("RT", 506, rt_l)):
            y0 = 206
            cv.create_text(x0, y0, text=lab, fill=FG, font=F(9), anchor="w")
            cv.create_rectangle(x0, y0 + 12, x0 + 100, y0 + 24, fill="#2a2d37", outline="#4b5060")
            bar = cv.create_rectangle(x0, y0 + 12, x0, y0 + 24, fill=ORANGE, outline="")
            pct = cv.create_text(x0 + 100, y0, text="0%", fill=MUTED, font=F(8), anchor="e")
            self.trig_bars[name] = (x0, y0 + 12, bar, pct)
        self.pad_items = p

    def _tester_sub(self):
        """Which Tester sub-tab is showing ("kb", "ms", "pad") or None."""
        if self.nb.select() != str(self.tab_tester):
            return None
        sel = self.tnb.select()
        return {str(self.ttab_kb): "kb", str(self.ttab_ms): "ms", str(self.ttab_pad): "pad"}.get(sel)

    def _tester_tick(self):
        try:
            sub = self._tester_sub()
            self.engine.self_test = {"kb": sub == "kb", "ms": sub == "ms" and self._mouse_hover,
                                     "pad": sub == "pad"}
            if sub:
                self._update_tester(sub)
        finally:
            self.root.after(33, self._tester_tick)

    def _rates(self, log, now, dev):
        counts = collections.Counter()
        for t, d, code, down, inj in log:
            if d == dev and down and inj and now - t <= 1.0:
                counts[code] += 1
        return counts

    def _update_tester(self, sub):
        e = self.engine
        now = time.perf_counter()
        log = snapshot(e.test_log)
        if sub == "kb":
            self._draw_keys(e.test_kb.copy(), e.test_kb_out.copy())
            counts = self._rates(log, now, "kb")
            text = "   ".join(f"{vk_name(c)}: {n} taps/sec" for c, n in counts.most_common(3))
            self._set(self.t_kb_rate, text=text or " ", foreground=GREEN)
        elif sub == "ms":
            ms, ms_out = e.test_ms.copy(), e.test_ms_out.copy()
            wt, wname = e.test_wheel
            if now - wt < 0.15:
                ms.add(wname)
            for btn, item in self.m_items.items():
                idle = "#5a5c68" if btn.startswith("wheel") else "#2a2b33"
                col = ACCENT if btn in ms_out else "#e9eaf0" if btn in ms else idle
                if self._ui_cache.get(("m", btn)) != col:
                    self._ui_cache[("m", btn)] = col
                    self.cv_mouse.itemconfig(item, fill=col)
                    if btn in self.m_labels and btn in ("left", "right"):
                        self.cv_mouse.itemconfig(self.m_labels[btn], fill="#1c1c1c" if col != idle else "#a9abb6")
            counts = self._rates(log, now, "ms")
            held = [b for b in MOUSE_NAMES if b in ms]
            if held:
                parts = []
                for b in held:
                    t = e.inputs.get(("ms", b))
                    parts.append(f"{MOUSE_NAMES[b].split(' (')[0]}: " + ("TURBO" if t and t.active else "holding"))
                self._set(self.t_ms_state, text="\n".join(parts))
            else:
                self._set(self.t_ms_state, text="Hold a mouse button over the picture")
            self._set(self.t_ms_rate, text="   ".join(
                f"{MOUSE_NAMES.get(c, c).split(' (')[0]}: {n} clicks/sec" for c, n in counts.most_common(2)) or " ")
        else:
            self._update_pad_view()

        # event lists
        new = [x for x in log if x[0] > self._log_seen]
        if new:
            self._log_seen = new[-1][0]
            for t, dev, code, down, inj in new[-40:]:
                lst = self.lst_kb if dev == "kb" else self.lst_ms
                name = vk_name(code) if dev == "kb" else MOUSE_NAMES.get(code, code.capitalize()).split(" (")[0]
                lst.insert("end", f"{t % 1000:8.3f}s   {'DOWN' if down else 'up'}   {name}   "
                                  f"({'TurboTap' if inj else 'you'})")
                lst.itemconfig("end", foreground=ACCENT if inj else MUTED)
            for lst in (self.lst_kb, self.lst_ms):
                excess = lst.size() - 200
                if excess > 0:
                    lst.delete(0, excess - 1)
                lst.see("end")

    def _draw_keys(self, phys, out):
        """Big keycaps for the keys being held: white = you, blue = TurboTap tap, with HOLDING / TURBO."""
        e = self.engine
        keys = sorted(phys | out, key=vk_name)[:6]
        turbo = {vk for vk in keys if (t := e.inputs.get(("kb", vk))) is not None and t.active}
        sig = (tuple(keys), frozenset(phys), frozenset(out), frozenset(turbo))
        if sig == self._keys_sig:
            return
        self._keys_sig = sig
        cv = self.cv_keys
        cv.delete("all")
        if not keys:
            cv.create_text(310, 60, text="Hold any key...", fill=MUTED, font=F(14))
            return
        w, gap = 92, 12
        x = 310 - (len(keys) * w + (len(keys) - 1) * gap) / 2
        for vk in keys:
            if vk in out:
                fill, txt = ACCENT, "#10161c"
            elif vk in phys:
                fill, txt = "#e9eaf0", "#1c1c1c"
            else:
                fill, txt = "#2a2b33", FG
            cv.create_rectangle(x, 8, x + w, 82, fill=fill, outline="#4a4b56", width=2)
            cv.create_text(x + w / 2, 45, text=vk_name(vk), fill=txt, font=F(13), width=w - 8)
            cap = ("TURBO", GREEN) if vk in turbo else ("holding", MUTED)
            cv.create_text(x + w / 2, 100, text=cap[0], fill=cap[1], font=F(10))
            x += w + gap

    def _update_pad_view(self):
        pads = list(self.reader.pads)
        now = time.monotonic()
        if now - getattr(self, "_slots_t", 0) >= 0.5 and not self._combo_open(self.cb_test_pad):
            self._slots_t = now
            ids = [p.id for p in pads]
            labels = []
            for pad in pads:
                tag = "  (TurboTap virtual)" if pad.virtual else \
                    ("  - in use" if pad.id == self.engine.pad_source else "")
                labels.append(pad.name + tag)
            if not labels:
                labels = ["No controller connected"]
            if labels != list(self.cb_test_pad["values"]) or ids != self._test_ids:
                cur = self.cb_test_pad.current()
                cur_id = self._test_ids[cur] if 0 <= cur < len(self._test_ids) else None
                self.cb_test_pad["values"] = labels
                if cur_id in ids:
                    idx = ids.index(cur_id)
                else:
                    idx = next((i for i, pad in enumerate(pads) if not pad.virtual), 0)
                self.cb_test_pad.current(idx)
                self._test_ids = ids
        idx = self.cb_test_pad.current()
        pad = self.reader.get(self._test_ids[idx]) if 0 <= idx < len(self._test_ids) else None
        kind = pad.kind if pad else (self.pad_kind or "xbox")
        if kind != self.pad_kind:
            self._draw_pad(kind)
        self._draw_pad_state(pad)

    @staticmethod
    def _combo_open(cb):
        """True while a combobox's drop-down list is open (don't touch it then)."""
        try:
            pd = cb.tk.call("ttk::combobox::PopdownWindow", cb)
            return bool(int(cb.tk.call("winfo", "ismapped", pd)))
        except (tk.TclError, ValueError):
            return False

    def _draw_pad_state(self, pad):
        state = (pad.id, pad.buttons, pad.lt, pad.rt, pad.lx >> 7, pad.ly >> 7, pad.rx >> 7, pad.ry >> 7,
                 pad.power) if pad else None
        if state == self._pad_last:
            return  # nothing changed - skip redrawing
        self._pad_last = state
        cv, p = self.cv_pad, self.pad_items
        b = pad.buttons if pad else 0
        dark = "#202228"
        for name, bit in PAD_BUTTONS:
            if bit is None:
                val = (pad.lt if name == "LT" else pad.rt) if pad else 0
                x0, y0, x1, y1 = self.trig_rng[name]
                cv.coords(p[name], x0, y1 - (y1 - y0) * val / 255, x1, y1)
                bx, by, bar, pct = self.trig_bars[name]
                cv.coords(bar, bx, by, bx + 100 * val / 255, by + 12)
                cv.itemconfig(pct, text=f"{val * 100 // 255}%")
                continue
            if name not in p:
                continue
            on = bool(b & bit)
            cv.itemconfig(p[name], state="normal" if on else "hidden")
            if name in self.pad_colors:
                col = self.pad_colors[name]
                for item in p[name + "sym"]:
                    c2 = dark if on else col
                    if cv.type(item) in ("text", "line"):
                        cv.itemconfig(item, fill=c2)
                    else:
                        cv.itemconfig(item, outline=c2)
        cv.itemconfig(p["Guide"], state="normal" if b & GUIDE_BIT else "hidden")
        if "Touch" in p:
            cv.itemconfig(p["Touch"], state="normal" if b & TOUCH_BIT else "hidden")
        for name, (sx, sy) in (("L-Stick", (pad.lx, pad.ly) if pad else (0, 0)),
                               ("R-Stick", (pad.rx, pad.ry) if pad else (0, 0))):
            nx, ny = sx / 32767, -sy / 32767
            cx, cy, rr = p[name + "c"]
            k = rr * 0.6
            cv.coords(p[name + "dot"], cx + nx * k - 4, cy + ny * k - 4, cx + nx * k + 4, cy + ny * k + 4)
            hx, hy, r, dot, txt = self.cross[name]
            cv.coords(dot, hx + nx * r - 5, hy + ny * r - 5, hx + nx * r + 5, hy + ny * r + 5)
            cv.itemconfig(txt, text=f"X {sx / 32767:+.2f}   Y {sy / 32767:+.2f}")
        if not pad:
            if self.reader.backend == "xinput":
                msg = "SDL2 isn't available, so only Xbox-style controllers can be read."
            else:
                msg = "No controller connected - plug in by USB or pair by Bluetooth."
            self._set(self.lbl_padinfo, text=msg)
            return
        info = [{"ps": "PlayStation layout", "xbox": "Xbox layout", "nintendo": "Nintendo layout"}
                .get(pad.kind, "Generic controller (Xbox layout)")]
        if pad.power:
            info.append(pad.power)
        self._set(self.lbl_padinfo, text="  \u2022  ".join(info))

    # ---------------- settings <-> UI ----------------
    def _all_vars(self):
        return [self.v_cps, self.v_hold, self.v_jit, self.v_mode, self.v_sound, self.v_top,
                self.v_kb_on, self.v_kb_all, self.v_kb_mods, self.v_ms_on, self.v_ms_all,
                self.v_pad_on, self.v_pad_all]

    def _load_into_ui(self):
        c = self.cfg
        self._loading = True
        try:
            self.v_cps.set(c.cps)
            self.v_hold.set(c.hold_pct)
            self.v_jit.set(c.jitter_pct)
            self.v_mode.set(c.mode)
            self.v_sound.set(c.sound)
            self.v_top.set(c.on_top)
            self.v_game.set(c.game_exe or "(any window)")
            self.v_kb_on.set(c.kb_enabled)
            self.v_kb_all.set(c.kb_all)
            self.v_kb_mods.set(c.kb_mods)
            self.v_ms_on.set(c.ms_enabled)
            self.v_ms_all.set(c.ms_all)
            self.v_pad_on.set(c.pad_enabled)
            self.v_pad_all.set(c.pad_all)
            for k, v in self.v_ms_btn.items():
                v.set(k in c.ms_buttons)
            for k, v in self.v_pad_btn.items():
                v.set(k in c.pad_buttons)
        finally:
            self._loading = False
        if not getattr(self, "_traced", False):
            for v in self._all_vars():
                v.trace_add("write", self._apply)
            self._traced = True
        self._refresh_keylist()
        self._refresh_games()
        self._refresh_profiles()
        self.btn_hotkey.config(text=f"{vk_name(c.hotkey)}   (click to change)")
        self._apply()

    def _apply(self, *_):
        if self._loading:
            return
        c = self.cfg
        try:
            c.cps = int(self.v_cps.get())
            c.hold_pct = int(self.v_hold.get())
            c.jitter_pct = int(self.v_jit.get())
        except (tk.TclError, ValueError):
            return
        c.mode = self.v_mode.get() or "hold"
        c.sound = bool(self.v_sound.get())
        c.on_top = bool(self.v_top.get())
        game = self.v_game.get().strip()
        c.game_exe = "" if game in ("", "(any window)") else game
        c.kb_enabled = bool(self.v_kb_on.get())
        c.kb_all = bool(self.v_kb_all.get())
        c.kb_mods = bool(self.v_kb_mods.get())
        c.ms_enabled = bool(self.v_ms_on.get())
        c.ms_all = bool(self.v_ms_all.get())
        c.ms_buttons = [k for k, v in self.v_ms_btn.items() if v.get()]
        c.pad_enabled = bool(self.v_pad_on.get())
        c.pad_all = bool(self.v_pad_all.get())
        c.pad_buttons = [k for k, v in self.v_pad_btn.items() if v.get()]
        if c.mode != self._prev_mode:
            self._prev_mode = c.mode
            self.engine.reset_all()
        self.root.attributes("-topmost", c.on_top)
        self.engine.refresh()
        self.engine.sync_pad()
        self._schedule_save()

    def _schedule_save(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(800, self._save)

    def _save(self):
        self._save_job = None
        try:
            save_json(SETTINGS_FILE, self.cfg.to_dict())
        except OSError:
            pass

    # ---------------- keys / hotkey capture ----------------
    def _start_capture(self, target):
        self.engine.capture = target
        btn = self.btn_hotkey if target == "hotkey" else self.btn_addkey
        btn.config(text="Press a key...  (Esc = cancel)")

    def _on_capture(self, target, vk):
        c = self.cfg
        if vk != 0x1B:  # Esc cancels
            if target == "hotkey":
                c.hotkey = vk
                c.kb_keys = [k for k in c.kb_keys if k != vk]
            elif vk != c.hotkey and vk not in c.kb_keys:
                c.kb_keys.append(vk)
        self.btn_hotkey.config(text=f"{vk_name(c.hotkey)}   (click to change)")
        self.btn_addkey.config(text="+ Add key")
        self._refresh_keylist()
        self.engine.refresh()
        self._schedule_save()

    def _refresh_keylist(self):
        self.lst_keys.delete(0, "end")
        for vk in self.cfg.kb_keys:
            self.lst_keys.insert("end", f"  {vk_name(vk)}")

    def _remove_key(self):
        sel = self.lst_keys.curselection()
        if sel:
            del self.cfg.kb_keys[sel[0]]
            self._refresh_keylist()
            self.engine.refresh()
            self._schedule_save()

    def _clear_keys(self):
        self.cfg.kb_keys.clear()
        self._refresh_keylist()
        self.engine.refresh()
        self._schedule_save()

    def _refresh_games(self):
        try:
            exes = list_window_exes()
        except Exception:  # noqa: BLE001
            exes = []
        self.cb_game["values"] = ["(any window)"] + exes

    # ---------------- profiles ----------------
    def _refresh_profiles(self):
        try:
            names = sorted(os.path.splitext(f)[0] for f in os.listdir(PROFILE_DIR) if f.endswith(".json"))
        except OSError:
            names = []
        self.cb_profile["values"] = names

    def profile_save(self):
        name = simpledialog.askstring(APP_NAME, "Profile name (e.g. the game's name):",
                                      initialvalue=self.v_profile.get(), parent=self.root)
        if not name:
            return
        name = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip()
        if not name:
            return
        save_json(os.path.join(PROFILE_DIR, name + ".json"), self.cfg.to_dict())
        self._refresh_profiles()
        self.v_profile.set(name)

    def profile_load(self):
        name = self.v_profile.get()
        data = load_json(os.path.join(PROFILE_DIR, name + ".json")) if name else None
        if not data:
            return
        self.cfg.update(data)
        self._prev_mode = self.cfg.mode
        self.engine.reset_all()
        self._load_into_ui()

    def profile_delete(self):
        name = self.v_profile.get()
        if name and messagebox.askyesno(APP_NAME, f"Delete profile '{name}'?"):
            try:
                os.remove(os.path.join(PROFILE_DIR, name + ".json"))
            except OSError:
                pass
            self.v_profile.set("")
            self._refresh_profiles()

    # ---------------- controller setup ----------------
    def _setup_refresh(self, force=False):
        state = {"vg": vgamepad_installed(), "vigem": service_installed("ViGEmBus"),
                 "hidhide": service_installed("HidHide")}
        # installed but not running yet = needs a PC restart
        self._restart_needed = [k for k, svc in (("vigem", "ViGEmBus"), ("hidhide", "HidHide"))
                                if state[k] and not service_running(svc)]
        sig = (tuple(state.items()), tuple(sorted(self._busy.items())), tuple(self._restart_needed))
        if not force and sig == getattr(self, "_setup_sig", None):
            return state
        self._setup_sig = sig
        for key, btn in self.setup_btns.items():
            if key in self._busy:
                btn.config(text=self._busy[key], state="disabled", style="TButton")
            elif key in self._restart_needed:
                btn.config(text="Restart PC to finish", state="disabled", style="Warn.TButton")
            elif state[key]:
                btn.config(text="\u2713  Installed", state="disabled", style="Done.TButton")
            else:
                btn.config(text="Install" if key == "vg" else "Download & install",
                           state="normal", style="Accent.TButton")
        show = state["hidhide"] and hidhide_client() is not None
        if show and not self.btn_hh_open.winfo_ismapped():
            self.btn_hh_open.pack(anchor="w", pady=(8, 0), before=self.lbl_hh_help)
        elif not show and self.btn_hh_open.winfo_ismapped():
            self.btn_hh_open.pack_forget()
        return state

    def _set_busy(self, key, text):
        """Thread-safe: update a setup button's progress text."""
        def f():
            if text is None:
                self._busy.pop(key, None)
            else:
                self._busy[key] = text
            self._setup_refresh()
        self._ui(f)

    def _setup_install(self, key):
        if key in self._busy:
            return
        self._busy[key] = "Starting..."
        self._setup_refresh()
        threading.Thread(target=self._setup_worker, args=(key,), daemon=True).start()

    def _setup_worker(self, key):
        try:
            if key == "vg" and FROZEN:
                self._set_busy(key, None)
                self._ui(lambda: messagebox.showerror(
                    APP_NAME, "The controller library is built into TurboTap.exe but couldn't load.\n"
                              "Please download TurboTap again from GitHub."))
                return
            if key == "vg":
                self._set_busy(key, "Installing...")
                ok, log = pip_install("vgamepad")
                importlib.invalidate_caches()
                self._set_busy(key, None)
                if ok:
                    self._ui(self.engine.sync_pad)
                else:
                    self._ui(lambda: messagebox.showerror(APP_NAME, "Install failed:\n\n" + log[-800:]))
                return

            repo = "ViGEmBus" if key == "vigem" else "HidHide"
            self._set_busy(key, "Finding latest...")
            try:
                name, url = latest_installer(repo)
            except Exception:  # noqa: BLE001
                if repo not in FALLBACK_INSTALLERS:
                    raise
                name, url = FALLBACK_INSTALLERS[repo]
            dest = os.path.join(tempfile.gettempdir(), name)
            if not os.path.exists(dest):
                download(url, dest, lambda pct: self._set_busy(key, f"Downloading {pct}%"))
            self._set_busy(key, "Finish the installer...")
            if not run_and_wait(dest):
                raise RuntimeError("the installer didn't start (cancelled?)")
            self._set_busy(key, None)

            def after():
                if self._setup_refresh().get(key):
                    if key == "hidhide":
                        messagebox.showinfo(APP_NAME, "HidHide is installed.\n\nRestart your PC, then click "
                                                      "'Open HidHide settings' and follow the steps under it.")
                    self.engine.sync_pad()
            self._ui(after)
        except Exception as e:  # noqa: BLE001
            self._set_busy(key, None)
            page = f"https://github.com/nefarius/{'ViGEmBus' if key == 'vigem' else 'HidHide'}/releases/latest"

            def fail(err=e):
                if messagebox.askyesno(APP_NAME, f"Couldn't download/install automatically:\n{err}\n\n"
                                                 "Open the download page instead?"):
                    webbrowser.open(page)
            self._ui(fail)

    def _open_hidhide(self):
        exe = hidhide_client()
        if exe:
            os.startfile(exe)  # noqa: S606

    # ---------------- status loop ----------------
    def _poll(self):
        try:
            while True:
                ev = self.engine.events.get_nowait()
                if ev[0] == "enabled":
                    self._beep(ev[1])
                elif ev[0] == "capture":
                    self._on_capture(ev[1], ev[2])
                elif ev[0] == "call":
                    ev[1]()
        except queue.Empty:
            pass

        e = self.engine
        on = e.enabled
        self._set(self.btn_power, text="ON" if on else "OFF", bg=GREEN if on else RED,
                  activebackground=GREEN if on else RED)
        hk = vk_name(self.cfg.hotkey)
        if not on:
            msg = f"Paused - press {hk} or click OFF to turn on"
        else:
            pid = foreground_pid()
            game = self.cfg.game_exe
            if pid == e.my_pid and any(e.self_test.values()):
                msg = "Test mode - turbo works here in the Tester"
            elif pid == e.my_pid:
                msg = "Ready - switch to your game and hold a turbo button"
            elif game and exe_of_pid(pid).lower() != game.lower():
                msg = f"Waiting for {game} to be the active window"
            else:
                msg = f"Active - {hk} turns it off"
        self._set(self.lbl_status, text=msg)

        now = time.monotonic()
        if now - self._last_t >= 0.5:
            rate = (e.taps - self._last_taps) / (now - self._last_t)
            self._last_taps, self._last_t = e.taps, now
            self._set(self.lbl_rate, text=f"{rate:.1f}", foreground=GREEN if rate > 0 else FG)

        pad_msg = "Status: " + e.pad_status
        if self.cfg.pad_enabled and getattr(self, "_restart_needed", None):
            pad_msg += "\n\u26a0 Restart your PC to finish installing " + \
                " and ".join({"vigem": "ViGEmBus", "hidhide": "HidHide"}[k] for k in self._restart_needed) + \
                " - until then games still see your real controller."
        self._set(self.lbl_pad, text=pad_msg)
        if now - getattr(self, "_setup_t", 0) > 2.0:
            self._setup_t = now
            self._setup_refresh()
        self.root.after(100, self._poll)

    def _ui(self, fn):
        """Run fn on the UI thread (safe to call from background threads)."""
        self.engine.events.put(("call", fn))

    def _beep(self, on):
        """On: 'Windows Battery Low' sound.  Off: 'Windows Balloon' sound."""
        if not self.cfg.sound:
            return
        wav = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media",
                           "Windows Battery Low.wav" if on else "Windows Balloon.wav")
        try:
            if not os.path.exists(wav):
                raise FileNotFoundError(wav)
            winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:  # noqa: BLE001
            threading.Thread(target=winsound.Beep, args=(988 if on else 494, 70), daemon=True).start()

    # ---------------- misc ----------------
    def restart_admin(self):
        self._save()
        script = os.path.abspath(sys.argv[0])
        params = "" if FROZEN else f'"{script}"'
        r = shell32.ShellExecuteW(None, "runas", sys.executable, params, os.path.dirname(script), 1)
        if (r or 0) > 32:
            self.on_close()

    def on_close(self):
        self._save()
        self.reader.running = False
        self.engine.stop()
        self.root.destroy()

    def _on_tk_error(self, *args):
        messagebox.showerror(APP_NAME, "".join(traceback.format_exception(*args))[-1500:])


# Controller line art for the Tester (PlayStation-style and Xbox-style)
PAD_ART_PS = (
    "iVBORw0KGgoAAAANSUhEUgAAAUUAAADvCAMAAABSWjZTAAAAkFBMVEVWWmaWm6rJ0OGpq68sLzjz9PiyuMixuMh0eYausdKU"
    "mqg3OkS8w9Oxt8dUWGPK0eF3d3o9QUt8gY+8wtPEy9x0eYajqbc9PT1/f/+8wtMAAP+u0th8fax9gY99qq1///8+QUs3O0UA"
    "/////wAkJEi0tGowMz4zZmZ/P3+ZZpn/AAD//6oAAAAnKjMeISkaHCXiIZiBAAAAMHRSTlPq56MM7QvTre8hqu6fVrNsA+zl"
    "zs6tSAQCYAEVCbAKAri7AQEHA70FBAUBAwD+/fyBpS9pAAAc9UlEQVR42u1dh3bjuLIEQDCIoBglr+PM3vgSw///3asGJZs5"
    "SJBM7xXOnF0fWSbBYjfQXR3A8se4frAHBA8UHyg+UGyP2NrpYdkbflL7NMnd8yZRtH+c6NiblMXoJdAj2jKguyg4zdLK462h"
    "aNu2y/3T4C/WNrXazvfOeZbc2ZmG8XoUc5cX5yH5S25tEEQr3x/C8jxL5uxie1Mo2vnOUZ8oFkxYG1wn7ec4YNXXLN19vi0U"
    "8Zbdr+kVUjxtUBjj3BKsMUtPGJ7kfwSKz20UyweKDxQfKD5QfKD4QPGB4gPFB4oPFB8oPlB8oPhA8YHiA8UHig8UHyg+UHyg"
    "+EDxgeIj7rJ9FNU2Y4Cd6JXvbiwGaNs75ytIWXkBPsm3lxeRu478nGSCsP5uEyi+ngZe6pvjsVAPlro7e4tR/di2XH6eJHP2"
    "UKHPB/g2FDvithOfw9qkKGJ7yS33c5L71u9+f58sWq3xy3rSw3q3rC3mkMX5P2maT6dZxs25v5uY70UoWnsxPnYblMXdxHxN"
    "ZJuwS3a8wDmtMQODO/bGlNpCKtH4fJnzcj2M7KJJqbIYHXy/LZ227dhtpjp1RoUMsqtf+yUo7v1iYrBgW3Y3HJdMTcxXwu6x"
    "vgNFdwbF3bZM7p37QNEAisEDxQeKDxQfKD5QbKOYPFD8q8ripL1I5Ni27EW4LhPzVUDx/lZ33GIUe+PPbGO+C157wKfeumvH"
    "3+JHf9Ux9YfjWvGmQMz/DrN7fL7cffsOP1qHMc41db3xss+3yNKOzpeqAu3vYcZe8/wvVIP6XfwieBLwm1Rr/GZZZ9p290Yf"
    "brREuiZmUcB9mi5NXU/X+v2dcRfbst9OE9TjjOV2a8zt/nTfTL3zy1CMdzXlHUXRmTJ+ebJoW7G3KIyvmkD81ZxvFD1ZvwjI"
    "59fvQVEbBvYeUQOHc4/Vw+NCBMLdbbB4X09nR0EOl/PzdDHfIBN7mu9rfH8Ubbpx5DoOJgSXoKpKPapCKca4k0V2vD11tjFf"
    "xhJ1nm5VVYVMGHedIMLzWPe2uqEb75EAgrLoRw3wCfP5YR9vShotwXmqiv588YHyeYbwb/5+VxQBz15wT066gNzOtySPiOdP"
    "zLZk/CCu9QHZSnt75/qsqIrJwSNsMpvA0f6NaYiwnJxuxXi2v46QWIeiFSP+N4NhUfKIFGRnW51hd7MBDI7d4MU1ko4q5mbs"
    "u9Y9UUSixuycCnZArNzaSPsXrD+zEy78e8oiMtgCIecnFWrDZ7/fdYb2GW4z8Nb6nwmRcabK+Ql7h/1VBhpbxdTtMy8ploxK"
    "Mibc1nDA4+0d90bDcUEjdq4OSyKZW8NPTC3L3DvJomXvOSuLpaMsZHMUfpS/8PZn5gb1yHkOvM4tixWT5eIKDoCtUefDChAH"
    "9pzc9YtbjQqXn45kzM7PvdxqXIzi361cpOU1s0QXKO9mKBYfQb7n11yAHS5nGtkKhvuQrJ2ZZL7PPlGMJ+MfV46jyF/Oop4w"
    "n8nV0syC2LoxivZMDGgQQw90BXdEckLxj1ujyE9txHBXLtbLPXkLt0Uxfg74Sn1WQcDg9ytP5/ffAcVao4/AD2wD46v1mzmX"
    "uq5submdrF3vhawUVEuyQN4PRXUIK3AisiqccPXSHfzPZUQKWxohEKxaKYoHVoUOdxBS1+7DnVD0eaVoHTmCZhibWZIMMVIU"
    "mr4wCswWshB7vna5ZhFqiFgpHV7p57kTis6x8v2yOHK8vCHtYdwXcKyENyAVJRN/3BDFPF5tKgK6Gj0m8E/eDUXQYMSESSzG"
    "gg1gmIGdlyh64WKAMKMunPatUIxnkkomUQyDIhTqfigqdUKxDLzum/czT1VEy2P/Uf6AZJDReCMUkZkzzy5NocjuiWIitZAR"
    "il2UfCzuMBkyWF8uWFLWh5E8GPsmKIIndLzyx8jiKIrSFwlWPlSyKRXClGUE41Du0/stUNzlT5ksfjyKADABBcak/rAEpiGq"
    "NHoPlgb5s20eRfv1n6utnE3KomCSHZLPj8oPEcIGK3vCeEGFN1uQabd3BkWxgr9ajyEW7/tQVJ8otiCC3CVZE9eS85L1F/xU"
    "xLeQxREuR/PZdZ4BWPneOv2FosC/O1o6ibZ0iqhr6Qiv6uQx0j7Oe2omQT7ZplG0vqiSNhOF5TlRsmZumJv1nH/YiQlW7yoV"
    "FX68n9UdVpCxUolQZi0XEA5hx0KsEBIecHAuSAZmCxrsH4ciFVlYShheGEg5kCXrwaiwmKfCTx0sVHf0ADmpQOTBqlAtd0vy"
    "PmJMDLqJ6xvgs3mDWwx6nLC82FmlXdiy/e0OzwO1T5MqpLjhvdgIRjPzUlbINieMBebo9tVFm7I9bmdt8ICt67reuL+k6BBL"
    "MOBOcVX2HCp40ArMWBnelRkrPagumDHpts0zoBguRLFw1gZW2dyq+DS0QZ+2jvPCXJbuseK95VMiMud7vGa778YvFuHB913f"
    "76TBMK7CzqOUpNEDkWF8GMevRncXbG1DKIqqtaL4/gCKOveJf0UM7oRioT7q9KbOAsN6u4vnVSwdKYF5NoniCCPWXZc5UByE"
    "qCqLu6NIytH/gjhWbpuul1hC+ZBDUaFtkW0ORSoVKQcTrbCiBLL5oivO5mKA90OxGExlK5LWBEqfEwM69N1kpc04ieI73JYR"
    "CrazLnNWbh1FlTHImGpKAqvAjA8HsoLYGIq75jlC7ZflSiaSxtKN16e2jSImKUFBsC+LF6ROxsbL2mIzKNojZo62YVXRSDc5"
    "OrWXt0EUG7PyASPLeAgvIfQ4MWONx+vIAI4j2plBkapPx6BpqzAssTAqNocicgoQyEKZmjrDqBDaEjpIfiRD4yQH9ffwRfl1"
    "NhY4CdsEigjOirFoC/YS6XmNPa1i/tZQTLjDP5hKAJHwP8luJEEplsiykt7p6ZDTD5uWEZTCU41w4LsJFMlvaYd0yRP5DN0C"
    "ui/99qqTfpeFTuDfAIqem0oqJdAhfnFKdGGZYCxEihkjsrvUm8whVfq5dCrAWW6qNFrhv0yuiy+8vTrr8aFzYBhxc6eEaeJs"
    "JNh4vGS//o7Pvh1FLXbsPBt5rO9dYlMUB87dM6xQ3aRC3FUPVn3GYspVp8lN7tFRUxSPB/KbXb2EOIJhVTyCc6+r8/VCrSeM"
    "xYV0o7/33RlFgIjXDADh5buOX1QfTnJOqyQiqhFGqEil9fdAB3zGYlbRjJO7S+OpcbsQa4kkJSkp+HMA1cAccg9hMuC3B56i"
    "igjqrPMvVS8udF/fhda8r9hUyF0F03DI+nFBOWXp58qkYzFqde4Tm9hcoqbf4nuVTCFtnhcygAXN4NSji4eIrFWJgDNFgd4Q"
    "pWFaNVjXc7wriuTbteKk3C2KoUTgLAWIX5+X9DB1mhTYZROWDrw/vymKsgg4pRS4AWku9OBAPqgPmQQhlx1LvW66yBMj1ZBd"
    "z/+uKNLGl7TtC+x/rN9uLESKtOOXLa8QBEFSbwMmUPxlOw2SO01hsJ7NKea7AvqcZcdKhrTxHXzKdPNOqZclIh5Vt0DinihK"
    "un/aYV5V78XCXvMrrxuLCcuTw7aihIxNlFk142MgyERaNajDQFXe4QCtlqlz8InZbqs/63io90QRwQrZ9boQY+lXvgy4/zyt"
    "jvXU/eXN+9jCtjl4u27r7dILQ1MLfoSO465tt97rh4XuiCItP714ChjQNOiFBpTqct2gqk5/y5Ynm7AJJsJvCXrRjmIhrIGW"
    "zcwPmO8kpe906PnyW1Gk23e2EgjYsUtQwffvRQzwwYlYSZY32mFTTEQ5gaIOYFGItOQhdJt16jy/E8VCxzOqLqNThn0U+3GX"
    "RCgZ6HnKYHH4ZQzFfwHFpCOLYXdRqVgkEbCEcdAWxe9Hsehr9EAMUCFdoieLzuffLk9mZKMKvW/G0DSKXieNTRZuioJ9sq9a"
    "e8t3ofjpsCYiUaKTSEsxDbfH7/ViMSTHpzUemhYvTHwa1+ioFbaCkxc0E02IKcZp1i4St5mTlrLdNOt7dpeXhqVTuu2omxyK"
    "aVDUpQtt4FVnanp5HdHo7vLeNhUisOsNFGEskp8aaKrTOdB/m7XcPJW8uzBx6lV8OxQ/RCPh14MutFRV+t1PPlW/HYth2b/V"
    "+RO1uBseG+veZB3CjrnVlC4iOBMnIzevlC68mOLjEMom5k3r8uyXBry4ZR2g6zZ3vma9i46x8LTP2DntWAzEj8lP6aRrxteg"
    "SE50Z7+geJlMlHbwoMZlImBsV/QqpUfW9wc+9l2PgTBh/R29Pg7EuRmKoQOP9cuoSHkpKWHjM5+btEUNxmKKRiyGHbzii8QA"
    "p/HHVbtL3KYidAQNGixOPBwSFlVADiKqHhxNJ9GMT+wiPJqQ+ICeibvDyqXKWwzkQ7jQnqQxXXKnBPmkEvlESCRLzlihaU0h"
    "P4lkisX4OBQECCvmAkAm/GbCTn6dLIqP9ubgkbNHSl7RAFWMxbAg9kYG5FMf0ORep8cgMhRKaHk/gMDdP6gBCrvB4FQk3tIe"
    "AqNirgiQkeV4sjqTE6VC3iXe/NnnJxhLSttC5aAA6d0EcTnHyEbyZ996ssR9whAarWnaFAklyWnpgU+NhGnq9EQUraSERmeg"
    "vYSCvKDnoDA+DgLt/wBi1a658JEbqE6Zvr5Tg6hSXr9HYHuOxeBX1FoAM6/wGH57R7wGRSt/6W8Ep2gAaTTTlunZSJRunfB5"
    "CqXRGCwIZboB0G3Gk+jFw488IkXVTZxO8QLwochLrblm/XMNI2IxiM8ASadjDKXBdSgO1r1/Ue3lh0v+01DCS4OP76/lB/Tk"
    "MD/Qg+0wtP17jiDR5zwsP21cLEb0EVMVnP96IVW8TsTkvL+WL+sLxMaoCG/GsHAbMUByBj22qPAAS1BgeggEK9hYDxCMU+Wk"
    "NnfCs0Y7vPiE8ZTnLwdSJJZxjGykBDVjMyiyBl1Xpn7JljVtqG7Rf6OY6FRSNQL1AFH4CK1SczlEPGTZiMVUg5H3hfmg7JJj"
    "KGj3SppxDHLhA1lsewi/bMZYCkq4PHiz7Yp2l6L4BotkGhNUsDST1hRscr5xFCl5Q3jdWIwzOetyYQt3NlbiMl1txdr8JnhN"
    "ydm2USQmoO1JsCwp+XRZWbIsts/GEnSq6Xd0KnpengW6fpRmLwd6tNsDAcKYepMPujBdh63LuLsTiqUuRaLttTJ1SUX1Lt3s"
    "+Gg4r7vNoVyI4nNuzyxyg9nx5loOKUoNOJXGhYaW27F6l+mFEX0bLrcXIz4tAzrVrvGdcohJvhhDSsHggeMGrkg94XzIW6IY"
    "qZkNYMnCOIhiLLzZ9plkw6rz6kXpY/xoBEO0MnI8To2F/A+fkrsCR3ilCRSLXvSKUJx598y9GMVchLMNfJADmEXUapoEBw69"
    "coyIjBQBUljOjhAoL6ZzX5Prr9w3I5C5KtkccbwoQ4INbS72vO3Hee1fpYdU+1hIljCid45HJF/V8uIcXxyu37rg63fpOpy2"
    "6Hsza1caXWbp2F2GdhjGAxQadOIRlCdRYVP9OeBvsQ/qK+lTct5kUy2IdlL2trLg7PEO/xXod1w70Fzr1MLIWrwPpVAoMfd6"
    "UOd7EYq7kYroLvHkgAVJPS+FUotJM4cYJ56mx2NK6xyToziiQHOwO1hCq4YcRYeufgzp6mKigaokyWteHWQe6s7nTCkqILoE"
    "xfd8UcSzpN4+7NSDf9JQy1IiQEstlIjJjF8dLO/wU1VERo88JFrPnirWSrBewahwlcyvEAqUjRhLyfhsh5tFfDcbPJFp4WJe"
    "UvjiK2V+WFQyTzbL8ih/1B879zUYkzgpUj6w45XHLq+KtWX0GshURIylVvsEPxCvs2BXWlBzzobO5HXNEQtendFKgYYI4QTS"
    "N+qmxgaRcsfFFHnkA4u1Xzc1kHR1j9WWl3LG+Ci8vgrih5UI4RifVWOvs3OP4BJZtK7r79oGxtcqk3iYtkfnWAhNp6EJCxsq"
    "d/T9CUKzD31ZX/1PUNpYoFPAk+qveGNaTS2JsNOFVKxRFctA1JX7l6BoLvieajo8hdrpshPUSSC6KXUMZ4j/m9qjEMbtuLzl"
    "h5ZOgob6hpGlgEWy1mo5JtH4sqSFSJ1jMQtQnE/A66EYv1qCmaFT6kzqjv4qTqtu2d+LEyeY1AFsxe3lWoucpFWgbNbkJLqk"
    "dGwlVz7KN1yyBbyFrdMkaJ3/W41iO+XuKhOa8gBVFy9q90Ufd8kLCsVNblO8XSSgaLvBWiq7YCc6B2HCaAhR+LTiEf39eln8"
    "haCkoc3FrzuzlL3FkhSu11XJdyM27Qi3gEfupM4UkL26GufkoZpi6ZjI54qlB1CMuClGjw0vgIXOPuuyzH4gJnVM+e26OAg6"
    "4suDVqcmYEtzKP6R26utbuFVhu5OXPEQr6F0XJh3H35aflTQdIN1Ac6w/xaSOjNujCpfQI4NWN3CEGmNihMcnDJ8ngonc5x1"
    "6PtpHZDwlWWz6KEsRggZuroUxqj3BYf6soHTCkJDr1AVxYjtQr/rVposQbEpziF5xuWwbfK3/oJxXWpkvBZF21BIlJBiYyc3"
    "k++b+h2Nno6BK9fxm4uuGu7LpOU01C1LTKE4HzVgA4mLhpZFV+qlcfj1Yrv9cLu7C5verJp7NMUrRhlWIg2ZubxdZMnna2Ux"
    "+jD0Bnl/B/mK20RF9zm79uCQvdhc+8qCjb1vL9Xt402hmDj2alkMjoZ4CKDopaPRLyK1VqGI5mGsvYOMNcPRGzQTxlCcJ8d6"
    "KP42VE6hZdEb02jP1Y1P2nN1Jk0dHCQp20B5o7JI6m4OxXK24Jx1RdHmhTEUx9fFFEtcN7CJwMD4CyyJf61aIb3xpS/yymGD"
    "/FInTKxD0VoWLVi6R4+1KtK586nb9QzTcfNA8jaDS3kObCzWQ9uU51Xmthd3xmBkvcI1kyiusReh5r4/lmQFY7Ed3aXmUmOW"
    "NVlBVCxmDsW5ZNAeioExm58Mk2HetUw5Je8lPXnzRrKsUHnS7XhG0uxN+C5RaE6jZ4PSrOu5mKvVo5Xpz8HLEXFVDjSDYp8p"
    "2L1LfXQP3SLWJhm8ul4umNF0Sj6TUsu6MZf15z6M3zspBrfKmqIdct9w+MpQ/N7LvD6rQXbRoDDqqzueycS9uZTaLop7cwZC"
    "/Tj98EOZan5x0H2DSjst8hpBUjgtrtPTdG0sDbCXpU6NZmZr5VjwvFuj0XuDBY+KxEW6Xd1KSeNUMLbvYB9xzzkmJSI1XsYH"
    "Y4aKoos9Jr2o20IaSr36QnH6GNWuLEYGDYS6ala2/eM/ddRIjUaC0SXKZdTSP0zqHkeUeaGmAhJh2yDSvcF5ZRTFJLMmF0a2"
    "LhV5pdHv6Xp/z/lkqRFMJ/+WzmcbtQw9l0c4nEpX5nlUtcfG+I6D0jHpz6tTaEpq+tJwpj4lJv9ajqIljGZnV55Os0DpYCAg"
    "V5GoUxmQozi5hyHRhJKAPEKST5wy6Qs6Epzi9D7gjs7FU74wnqgPH3C3FEWK//1p9O7wAT/quJuXpqkX/q1OgshmDYEQZ1BT"
    "toOcpgnq1fVvuDolDKkTg2YcxNJbgeJvYxTt1/3RyV0vXIjpn4qSWZbKZUlA88YKy+oJUye+qk4Jd9LC+EAyyvLdxTZF0bar"
    "dRxXa+WpfSk32YOjTAIeeOerS+U5Nym7metp2fEA56qFLuTnskjXMHrIdefK8NXDD+xCLqX1+oLfqnRpJs2E3Sb+183rYLTX"
    "6lTH8hbPCAj11VVxqzHtSXdQPIQ3moVetaqyvNXVdXfem2E4R9R2fBd+u7f5o0e1HMVlafH/kQPU3FI/2jaWovPXG8c1KPo/"
    "RDTufkfGl9qLu2Y/37tND8Ue68fda7HVZDS1jaLRpnRLrpXA1ovWD3EIF2zISpVGTZ1FKFIrImVQBTI+9xToMBdc1mFnn82v"
    "4CFymI3yEdYSFClcYMyL5k6w2ztsOiPRy/ZYjXdrh7UD2+fO6A0S4d/eBDdm5fNgGYrv5ko08Ah0y73IxuvV4PNCEO34IlmM"
    "8//GgQ7hxGqb6fq9/cGUOHoumpIsQHFnDsXDC0T71SIcHW+4/A0U9j5fd15cN1zpCh6OlQbSW7RfbSSpM2Oe9G97EYquIRSP"
    "Tr4jfHBOJrqz6XNKGr5fSWVEPHhBtrSd51fAuLOijKpcG4XAZd0uQezymg+0jAWSJvtOt1A0VejiR3+vb2jTo0RBhiMqqbsl"
    "OFdqRYW0JjeqQb5m0FoQv2TU1hUXpwNHEiI9XDd6o2PsT6fZm9ox1TIU8RVDQZfmDbW4WXu37vHnoMuYQMM2+/ybq0Z9hd0+"
    "OFCrNdwiw//3u9PDnLdM1zOU3T0R2WezB8te7S3F+uYWjX1k1UV1VpybGfpCuOhTtLNO17abMm6OG3DviyL4j98dobGs5v5m"
    "WXZubli7Xfvar9089cRUfsQCFGEuCmUq9bQvanYMUfm1s55jkxCeBCCOtahbg9cWZpJNpgJYTRTNlK6tO3fr5sMUUUWFbO9L"
    "UDRzu9UH3t52mMoORhhw3ENgxl+axDL8tiUUDS33UyrW3F3M5OgkwZYU2px0LEPRVBkld9ec03oPFF+MpCpQK8Z4CYpGXJdV"
    "B4zeA8VXMyqt3N0SFM0Eo1GoZP9jSyjON3xeOFJ71GBsoPhLhPeoDfkWlb4XinbvhN5LHRd7YyhCD424L8tQNJIvFiLr9DXf"
    "mjCa6G1TsruhWC3rQHpvFM082t1QlHy/LTsn1wcn7QwEN0kW4yUoGvGhX+PtCWNswvBeiuL1RjcaxO22p9IIGRqgq5aieL3R"
    "jdDov7a4MJowvO+G4tGxrXyD491EEGsJisAxNRC2yjeJYhwbqG8M7SVsxPUoKv6Wx1tEkbxA9lNQPIp8o+O3gSDWnVAk72+r"
    "MBpItr4TinQ6a7xZGK8OYt0JRR5sc2+pGYmrkyTug6IUVr5hjb663HYJitfbi2gmbG8YxavDL0tRvO5dOe52FVp7gRm7i+9y"
    "bYJf/I/tokgmI78LiupKvyXeMIjI87vu+Or7sLT8Jd40injHV7GMi1ladhWzuG0M6wadV6U7LYoB/nFNPJpI7q3DeF3jqmXx"
    "6OsyTJiwNo/idcYOE+8LrO7rcsZcN4/trYviNYcL0JL1bN82l1a6uy0bi59k7RWLFoIhi7Ljd5ffw1twCMoGVNq+gvJeltdN"
    "JanpFS/qR6B4+f6CehfbXlbBdiHtUXoit/MfMKyLU3ZKEFaLKthQTuaml9aC2HH+I8al8X2iWv53Ya3+ZU4gRPGPnyGLFxs7"
    "aC+XL+/tJNIL9hcZPG0xlj+yMl60hYLGt+PFKF5ECLPAev8ZonhpeWU1Q+P3ulhyszbAJoXxgtRWa0UvUNuO1zfgOIJYjH8K"
    "iiSMq5/w4/C0pkeydmDWBg6mjPrtjX/Eq7dphoT/3/m6k0lwk3LV8ZPBTwJRP6OrDOfNDJwHaDv+Gu7ysOWY1dB4Xrf4Kx78"
    "l2VfcNps5q/qe/vDRDG2rRWHHDDn5ZLTZinbL1t4UA/O/3DzOP9hg3iXhRUwpX9YIiVsJGlyUdtwdBX6cZJ42kODJV4a2tZn"
    "T0tKQ9nwy8pfDv681NPm9Zb/wAHuP3DU3PPh7F/XWmQKs7H2INb+4E3eh17UDzK3u3Ly7k53nsLJWZn7li9TNTbeZuVJZN54"
    "NZZK+Yt1RVeh71fqvZgIeqKlDBRtabMaNvq2dGcmoVu39+xHhZtQ5x8r/7ED8LxHGacehGWvuSM1h4qs5a2n2JSvhFcWZY7L"
    "WaJ0Z3F9YDh6/3DHde2ruwp9N4wWPZ5Lj0ftkuqnK3TbJMcJ6NF2i5+PTSar1V1/0O8noD7RNDj1Fdrr6/9oDD+7GuHxDoH4"
    "fLoUnY2ifb6yaxKb+f1vIg7frd1nA8n9k1bj3Y/HsPZjLN2D5+nl/Hi2jsLZK9WMLZD8HgP7y/prYKi3mV338d7eVi/3bJHo"
    "P6Pjz7ONQd1/7L8OhGeBpIeioTsbXXAFlj/G9eOBoonx/ypIx0kfpnZKAAAAAElFTkSuQmCC"
)
PAD_ART_XBOX = (
    "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"
    "RU5ErkJggg=="
)


# App icon (lightning bolt), embedded so TurboTap stays a single file
ICON_256 = (
    "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"
)
ICON_48 = (
    "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"
    "QmCC"
)
ICON_32 = (
    "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"
)


def main():
    try:  # own taskbar icon instead of Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ilukezippo.TurboTap")
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    winmm.timeBeginPeriod(1)  # 1 ms timer accuracy for smooth tapping
    try:
        root = tk.Tk()
        app = App(root)
        if SELFTEST:  # used by the build workflow: start, check the bundled parts, quit
            root.after(6000, lambda: _selftest_finish(app))
        root.mainloop()
    finally:
        winmm.timeEndPeriod(1)


SELFTEST = os.environ.get("TURBOTAP_SELFTEST", "")


def _selftest_finish(app):
    checks = {"sdl": app.reader.backend == "sdl", "vgamepad": vgamepad_installed(), "theme": app.sv,
              "hooks": app.engine.kb_listener.is_alive() and app.engine.ms_listener.is_alive()}
    with open(SELFTEST, "w", encoding="utf-8") as f:
        for k, v in checks.items():
            f.write(f"{k}={'ok' if v else 'FAIL'}\n")
        if app.reader.error:
            f.write(f"sdl_error={app.reader.error}\n")
    app.on_close()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        err = traceback.format_exc()
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, "error.log"), "w", encoding="utf-8") as f:
                f.write(err)
        except OSError:
            pass
        if SELFTEST:
            with open(SELFTEST, "w", encoding="utf-8") as f:
                f.write("crash\n" + err)
            sys.exit(1)
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_NAME, err[-1500:])
