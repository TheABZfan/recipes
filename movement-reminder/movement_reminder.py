#!/usr/bin/env python3
"""Hourly movement reminder.

Every hour it makes a noise and pops up an alert telling you to get up and
walk. The alert has an emergency "delay 2 minutes" button. If you don't touch
it, the break starts on its own: a full-screen window that stays on top, keeps
itself focused and refuses to be minimised or closed for 3 minutes. That window
has an emergency "end break early" button.

It sits in the Windows system tray, remembers its settings between runs, can
start itself at login, lets you push the next break back by 5 or 10 minutes,
and stays quiet when you've already been away from the keyboard.

Standard library only (Python 3.8+ with tkinter). Run it with:

    python movement_reminder.py

Handy flags for trying it out without waiting an hour:

    python movement_reminder.py --interval 1 --break-length 15 --grace 5
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import traceback

try:
    import tkinter as tk

    TK_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on the Python build
    tk = None
    TK_IMPORT_ERROR = exc

SYSTEM = platform.system()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, "movement-reminder-error.log")

# Palette
BG = "#0f141c"
BG_PANEL = "#161d29"
FG = "#e8edf5"
FG_DIM = "#8b98ad"
ACCENT = "#4ade80"
DANGER = "#f0883e"

APP_NAME = "Movement Reminder"

NO_TKINTER_HELP = """This copy of Python can't open windows: importing tkinter failed.

Fix it one of these ways:
  - Re-run the installer from python.org, choose "Modify", and make sure
    "tcl/tk and IDLE" is ticked.
  - If you installed Python from the Microsoft Store, install it from
    python.org instead - the Store build is the usual cause of this.
  - On Debian/Ubuntu: sudo apt install python3-tk

Details: %s"""


# --------------------------------------------------------------------------
# Failure reporting (the app usually runs without a console, so a bare
# traceback would go nowhere - write it down and put it on screen)
# --------------------------------------------------------------------------

def log_note(message: str) -> None:
    """Write to the log without interrupting anyone."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(
                "\n--- %s ---\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
            )
    except OSError:
        pass


def report_fatal(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(
                "\n=== %s ===\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
            )
        message += "\n\nThis was also written to:\n%s" % LOG_PATH
    except OSError:
        pass

    try:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
    except Exception:
        pass

    if SYSTEM == "Windows":
        try:
            import ctypes

            # MB_OK | MB_ICONERROR | MB_SETFOREGROUND
            ctypes.windll.user32.MessageBoxW(
                None, message[:2000], APP_NAME + " - error", 0x10 | 0x10000
            )
            return
        except Exception:
            pass

    if tk is not None:
        try:
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_NAME + " - error", message[:2000])
            root.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Settings, remembered between runs
# --------------------------------------------------------------------------

DEFAULTS = {
    "interval_minutes": 60.0,
    "break_seconds": 180,
    "delay_seconds": 120,
    "grace_seconds": 20,
    "steal_focus": True,
    "primary_only": False,
    "idle_skip": True,
    "idle_minutes": 5.0,
    "minimize_to_tray": True,
    "start_hidden": False,
}


def config_dir() -> str:
    if SYSTEM == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "MovementReminder")
    if SYSTEM == "Darwin":
        return os.path.expanduser("~/Library/Application Support/MovementReminder")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "movement-reminder")


CONFIG_PATH = os.path.join(config_dir(), "config.json")


def load_config() -> dict:
    """Settings from disk, falling back to defaults for anything missing or bogus."""
    data = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return data
    if not isinstance(stored, dict):
        return data
    for key, default in DEFAULTS.items():
        if key not in stored:
            continue
        try:
            # bool first: it is a subclass of int.
            if isinstance(default, bool):
                data[key] = bool(stored[key])
            elif isinstance(default, float):
                data[key] = float(stored[key])
            elif isinstance(default, int):
                data[key] = int(stored[key])
            else:
                data[key] = stored[key]
        except (TypeError, ValueError):
            pass  # keep the default
    return data


def save_config(data: dict) -> bool:
    try:
        os.makedirs(config_dir(), exist_ok=True)
        payload = {key: data[key] for key in DEFAULTS if key in data}
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# "Are you even at the desk?" - seconds since the last keyboard/mouse input
# --------------------------------------------------------------------------

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


_X11_IDLE = {"tried": False, "display": None, "info": None, "xlib": None, "xss": None}


def idle_seconds():
    """Seconds since the last input, or None if this system can't tell us."""
    try:
        if SYSTEM == "Windows":
            return _idle_windows()
        if SYSTEM == "Linux":
            return _idle_x11()
    except Exception:
        return None
    return None


def _idle_windows():
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint32
    tick = ctypes.windll.kernel32.GetTickCount()
    # GetTickCount wraps every ~49 days; mask keeps the difference sane.
    return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _idle_x11():
    """XScreenSaver's idle counter. Handles stay open - this is polled often."""
    if not _X11_IDLE["tried"]:
        _X11_IDLE["tried"] = True
        try:
            import ctypes.util

            xlib_name = ctypes.util.find_library("X11")
            xss_name = ctypes.util.find_library("Xss")
            if not xlib_name or not xss_name:
                return None
            xlib = ctypes.cdll.LoadLibrary(xlib_name)
            xss = ctypes.cdll.LoadLibrary(xss_name)
            xlib.XOpenDisplay.restype = ctypes.c_void_p
            xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            xlib.XDefaultRootWindow.restype = ctypes.c_ulong
            xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
            xss.XScreenSaverQueryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(_XScreenSaverInfo),
            ]
            display = xlib.XOpenDisplay(None)
            if not display:
                return None
            _X11_IDLE.update(
                xlib=xlib,
                xss=xss,
                display=display,
                info=xss.XScreenSaverAllocInfo(),
            )
        except Exception:
            return None

    if not _X11_IDLE["display"]:
        return None
    xlib, xss = _X11_IDLE["xlib"], _X11_IDLE["xss"]
    display, info = _X11_IDLE["display"], _X11_IDLE["info"]
    root = xlib.XDefaultRootWindow(display)
    if not xss.XScreenSaverQueryInfo(display, root, info):
        return None
    return info.contents.idle / 1000.0


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("event_mask", ctypes.c_ulong),
    ]


# --------------------------------------------------------------------------
# Start with the computer
# --------------------------------------------------------------------------

def _script_path() -> str:
    return os.path.abspath(__file__)


def _windowless_python() -> str:
    """pythonw.exe where possible, so no console flashes at login."""
    exe = sys.executable or "python"
    if SYSTEM == "Windows" and os.path.basename(exe).lower() == "python.exe":
        candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


def autostart_path() -> str:
    if SYSTEM == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(
            base,
            "Microsoft",
            "Windows",
            "Start Menu",
            "Programs",
            "Startup",
            "Movement Reminder.lnk",
        )
    if SYSTEM == "Linux":
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(base, "autostart", "movement-reminder.desktop")
    return ""


def autostart_supported() -> bool:
    return SYSTEM in ("Windows", "Linux")


def autostart_enabled() -> bool:
    path = autostart_path()
    return bool(path) and os.path.exists(path)


def set_autostart(enable: bool):
    """Returns (ok, message). Never raises - the caller shows the message."""
    path = autostart_path()
    if not path:
        return False, "Starting automatically isn't supported on this system."

    if not enable:
        try:
            if os.path.exists(path):
                os.remove(path)
            return True, "Removed from startup."
        except OSError as exc:
            return False, "Couldn't remove the startup entry: %s" % exc

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if SYSTEM == "Windows":
            return _write_windows_shortcut(path)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "[Desktop Entry]\nType=Application\nName=%s\n"
                "Exec=%s %s\nX-GNOME-Autostart-enabled=true\n"
                % (APP_NAME, _windowless_python(), _script_path())
            )
        return True, "Added to startup."
    except OSError as exc:
        return False, "Couldn't write the startup entry: %s" % exc


def _write_windows_shortcut(path: str):
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut({lnk});"
        "$s.TargetPath = {exe};"
        "$s.Arguments = {args};"
        "$s.WorkingDirectory = {cwd};"
        "$s.WindowStyle = 7;"
        "$s.Description = 'Movement Reminder';"
        "$s.Save()"
    ).format(
        lnk=_ps_quote(path),
        exe=_ps_quote(_windowless_python()),
        args=_ps_quote('"%s"' % _script_path()),
        cwd=_ps_quote(APP_DIR),
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "Couldn't create the startup shortcut: %s" % exc
    if result.returncode != 0 or not os.path.exists(path):
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        return False, "Couldn't create the startup shortcut. %s" % detail
    return True, "Added to startup."


# --------------------------------------------------------------------------
# Sound + system notifications (all best-effort; never fatal)
# --------------------------------------------------------------------------

def _popen(cmd: list) -> bool:
    """Fire and forget a command. True if it launched."""
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if SYSTEM == "Windows":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, **kwargs)
        return True
    except (OSError, ValueError):
        return False


def play_alert_sound() -> None:
    """The 'get up' noise: attention-grabbing."""
    try:
        if SYSTEM == "Windows":
            import winsound

            winsound.PlaySound(
                "SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC
            )
            return
        if SYSTEM == "Darwin":
            if _popen(["afplay", "/System/Library/Sounds/Glass.aiff"]):
                return
        else:
            candidates = [
                ["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
                ["aplay", "-q", "/usr/share/sounds/alsa/Front_Center.wav"],
                ["canberra-gtk-play", "-i", "alarm-clock-elapsed"],
            ]
            for cmd in candidates:
                if _popen(cmd):
                    return
    except Exception:
        pass
    _terminal_bell()


def play_chime(rising: bool = True) -> None:
    """A softer two-note chime for break start / break end."""
    if SYSTEM == "Windows":
        try:
            import threading
            import winsound

            notes = (660, 880) if rising else (880, 660)

            def _beep():
                for freq in notes:
                    winsound.Beep(freq, 140)

            threading.Thread(target=_beep, daemon=True).start()
            return
        except Exception:
            pass
    elif SYSTEM == "Darwin":
        sound = "Tink.aiff" if rising else "Pop.aiff"
        if _popen(["afplay", "/System/Library/Sounds/" + sound]):
            return
    else:
        name = "message-new-instant" if rising else "complete"
        if _popen(["paplay", "/usr/share/sounds/freedesktop/stereo/%s.oga" % name]):
            return
    _terminal_bell()


def _terminal_bell() -> None:
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass


NOTIFY_HOOK = None  # set to the tray's balloon when one is running


def notify(title: str, message: str) -> None:
    """Desktop notification, on top of the app's own popup."""
    hook = NOTIFY_HOOK
    if hook is not None:
        try:
            if hook(title, message):
                return  # the tray balloon did it, no need to spawn PowerShell
        except Exception:
            pass
    try:
        if SYSTEM == "Windows":
            script = (
                "[void][System.Reflection.Assembly]::LoadWithPartialName("
                "'System.Windows.Forms');"
                "[void][System.Reflection.Assembly]::LoadWithPartialName("
                "'System.Drawing');"
                "$n = New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon = [System.Drawing.SystemIcons]::Information;"
                "$n.BalloonTipTitle = {title};"
                "$n.BalloonTipText = {message};"
                "$n.Visible = $true;"
                "$n.ShowBalloonTip(10000);"
                "Start-Sleep -Seconds 7;"
                "$n.Dispose()"
            ).format(title=_ps_quote(title), message=_ps_quote(message))
            _popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    script,
                ]
            )
        elif SYSTEM == "Darwin":
            _popen(
                [
                    "osascript",
                    "-e",
                    'display notification "%s" with title "%s"'
                    % (message.replace('"', ""), title.replace('"', "")),
                ]
            )
        else:
            _popen(["notify-send", "-u", "critical", title, message])
    except Exception:
        pass


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# --------------------------------------------------------------------------
# Windows tray icon
#
# tkinter has no tray support, so this is Shell_NotifyIcon driven straight
# through ctypes. It owns a hidden window and its own message loop on a private
# thread; clicks are posted to a queue that the tkinter side drains, because
# tkinter must only ever be touched from the main thread. If any of it fails
# the app just keeps its ordinary window - see ReminderApp._start_tray.
# --------------------------------------------------------------------------

if SYSTEM == "Windows":
    from ctypes import wintypes

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]


class WindowsTray:
    """A tray icon with a right-click menu. Actions land on a queue."""

    WM_TRAY = 0x0400 + 20  # WM_APP + 20
    ID_SHOW, ID_BREAK, ID_PAUSE, ID_QUIT = 1, 2, 3, 4
    ACTIONS = {ID_SHOW: "show", ID_BREAK: "break", ID_PAUSE: "pause", ID_QUIT: "quit"}

    def __init__(self, actions: "queue.Queue", tooltip: str = APP_NAME):
        self.actions = actions
        self.tooltip = tooltip
        self.paused = False           # read by the menu builder, set by the app
        self.ok = False
        self.error = None
        self.hwnd = None
        self._data = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # If setup hangs or dies we simply carry on without a tray icon.
        self._ready.wait(timeout=5.0)

    # -- public, called from the tkinter thread ---------------------------

    def set_tooltip(self, text: str) -> None:
        if not self.ok or text == self.tooltip:
            return
        self.tooltip = text
        try:
            data = self._base_data()
            data.uFlags = 0x04  # NIF_TIP
            data.szTip = text[:127]
            self.shell32.Shell_NotifyIconW(1, ctypes.byref(data))  # NIM_MODIFY
        except Exception:
            pass

    def show_message(self, title: str, text: str) -> bool:
        if not self.ok:
            return False
        try:
            data = self._base_data()
            data.uFlags = 0x10  # NIF_INFO
            data.szInfo = text[:255]
            data.szInfoTitle = title[:63]
            data.dwInfoFlags = 0x01  # NIIF_INFO
            return bool(self.shell32.Shell_NotifyIconW(1, ctypes.byref(data)))
        except Exception:
            return False

    def stop(self) -> None:
        if self.hwnd:
            try:
                self.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass

    # -- everything below runs on the tray thread -------------------------

    def _run(self) -> None:
        try:
            self._setup()
            self.ok = True
        except Exception:
            self.error = traceback.format_exc()
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self.ok = False
            self._teardown()

    def _setup(self) -> None:
        # Declaring prototypes is not optional here: ctypes defaults every
        # return value to a 32-bit int, which silently truncates the 64-bit
        # handles this all runs on.
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        u = self.user32
        u.DefWindowProcW.restype = LRESULT
        u.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        u.RegisterClassW.restype = wintypes.ATOM
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.LoadIconW.restype = wintypes.HICON
        u.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.CreatePopupMenu.argtypes = []
        u.AppendMenuW.restype = wintypes.BOOL
        u.AppendMenuW.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR
        ]
        u.TrackPopupMenu.restype = ctypes.c_int
        u.TrackPopupMenu.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
        ]
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.GetMessageW.restype = ctypes.c_int
        u.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
        ]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = LRESULT
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self.shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)
        ]

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        # Held on the instance so the trampoline isn't garbage collected.
        self._wndproc = WNDPROC(self._on_message)

        hinstance = kernel32.GetModuleHandleW(None)
        self._class_name = "MovementReminderTray_%d" % os.getpid()
        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = self._class_name
        if not self.user32.RegisterClassW(ctypes.byref(wndclass)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._wndclass = wndclass  # keep alive

        self.hwnd = self.user32.CreateWindowExW(
            0, self._class_name, APP_NAME, 0, 0, 0, 0, 0, None, None, hinstance, None
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        # IDI_INFORMATION as a resource id, via MAKEINTRESOURCE.
        self.hicon = self.user32.LoadIconW(
            None, ctypes.cast(ctypes.c_void_p(32516), wintypes.LPCWSTR)
        )

        data = self._base_data()
        data.uFlags = 0x01 | 0x02 | 0x04  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = self.WM_TRAY
        data.hIcon = self.hicon
        data.szTip = self.tooltip[:127]
        if not self.shell32.Shell_NotifyIconW(0, ctypes.byref(data)):  # NIM_ADD
            raise ctypes.WinError(ctypes.get_last_error())

    def _base_data(self) -> "NOTIFYICONDATAW":
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.hIcon = getattr(self, "hicon", None)
        return data

    def _teardown(self) -> None:
        try:
            data = self._base_data()
            self.shell32.Shell_NotifyIconW(2, ctypes.byref(data))  # NIM_DELETE
        except Exception:
            pass

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == self.WM_TRAY:
                event = lparam & 0xFFFF
                if event in (0x0202, 0x0203):      # WM_LBUTTONUP / DBLCLK
                    self.actions.put("show")
                elif event == 0x0205:              # WM_RBUTTONUP
                    self._popup_menu(hwnd)
                return 0
            if msg == 0x0002:                      # WM_DESTROY
                self.user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self, hwnd) -> None:
        menu = self.user32.CreatePopupMenu()
        if not menu:
            return
        try:
            self.user32.AppendMenuW(menu, 0x0000, self.ID_SHOW, "Show window")
            self.user32.AppendMenuW(menu, 0x0000, self.ID_BREAK, "Break now")
            self.user32.AppendMenuW(
                menu,
                0x0000,
                self.ID_PAUSE,
                "Resume reminders" if self.paused else "Pause reminders",
            )
            self.user32.AppendMenuW(menu, 0x0800, 0, None)  # MF_SEPARATOR
            self.user32.AppendMenuW(menu, 0x0000, self.ID_QUIT, "Quit")

            point = wintypes.POINT()
            self.user32.GetCursorPos(ctypes.byref(point))
            # Required so the menu closes when you click elsewhere.
            self.user32.SetForegroundWindow(hwnd)
            chosen = self.user32.TrackPopupMenu(
                menu, 0x0002 | 0x0080 | 0x0100,  # RIGHTBUTTON | NONOTIFY | RETURNCMD
                point.x, point.y, 0, hwnd, None,
            )
            self.user32.PostMessageW(hwnd, 0x0000, 0, 0)  # WM_NULL, dismisses cleanly
            action = self.ACTIONS.get(chosen)
            if action:
                self.actions.put(action)
        finally:
            self.user32.DestroyMenu(menu)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def fmt_long(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def primary_center_in_span(virtual_origin, primary_size):
    """Middle of the primary monitor, in coordinates local to a window that
    covers the whole virtual desktop.

    On Windows the primary monitor always starts at (0, 0) in virtual desktop
    coordinates, while the virtual desktop itself can start at a negative
    offset when a second screen sits to the left or above. Subtracting that
    offset converts to window-local pixels. Falls back to the middle of the
    span if the primary size looks nonsensical.
    """
    origin_x, origin_y = virtual_origin
    width, height = primary_size
    if width <= 0 or height <= 0:
        return None
    return (-origin_x + width // 2, -origin_y + height // 2)


def center(win: tk.Misc, width: int, height: int) -> None:
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 3)
    win.geometry("%dx%d+%d+%d" % (width, height, x, y))


def make_button(parent, text, command, *, primary=False, danger=False, small=False):
    if danger:
        bg, active = DANGER, "#ff9d55"
        fg = "#1a1006"
    elif primary:
        bg, active = ACCENT, "#69eb9c"
        fg = "#06210f"
    else:
        bg, active = "#243044", "#2f3f59"
        fg = FG
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=fg,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        padx=14 if small else 22,
        pady=6 if small else 12,
        font=("Segoe UI", 10 if small else 12, "bold"),
    )
    return btn


# --------------------------------------------------------------------------
# The alert popup (stage 1)
# --------------------------------------------------------------------------

class AlertWindow:
    """Small always-on-top prompt with the emergency 2-minute delay button."""

    def __init__(self, app: "ReminderApp", grace_seconds: int):
        self.app = app
        self.closed = False
        self.deadline = time.monotonic() + grace_seconds

        self.win = tk.Toplevel(app.root)
        self.win.title("Time to move")
        self.win.configure(bg=BG_PANEL)
        self.win.protocol("WM_DELETE_WINDOW", self.start_break)
        self.win.resizable(False, False)
        center(self.win, 460, 240)
        self.win.attributes("-topmost", True)

        tk.Label(
            self.win,
            text="Time to get up and walk",
            bg=BG_PANEL,
            fg=FG,
            font=("Segoe UI", 20, "bold"),
        ).pack(pady=(30, 6))

        self.sub = tk.Label(
            self.win,
            text="",
            bg=BG_PANEL,
            fg=FG_DIM,
            font=("Segoe UI", 11),
        )
        self.sub.pack(pady=(0, 22))

        row = tk.Frame(self.win, bg=BG_PANEL)
        row.pack()
        make_button(row, "Start break now", self.start_break, primary=True).pack(
            side="left", padx=6
        )
        make_button(
            row, "Delay 2 minutes", self.delay, danger=True
        ).pack(side="left", padx=6)

        self.win.bind("<Return>", lambda _e: self.start_break())
        self.win.bind("<Escape>", lambda _e: self.delay())

        play_alert_sound()
        notify(APP_NAME, "Time to get up and walk for 3 minutes.")

        self.win.after(50, self._raise)
        self._tick()

    def _raise(self) -> None:
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def _tick(self) -> None:
        if self.closed:
            return
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.start_break()
            return
        self.sub.config(text="Break starts automatically in %ds" % int(remaining + 0.5))
        self.win.after(200, self._tick)

    def start_break(self) -> None:
        if self.closed:
            return
        self.close()
        self.app.begin_break()

    def delay(self) -> None:
        if self.closed:
            return
        self.close()
        self.app.delay_alert()

    def close(self) -> None:
        self.closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# The break lock screen (stage 2)
# --------------------------------------------------------------------------

class BreakWindow:
    """Full-screen countdown that refuses to be minimised, closed or unfocused."""

    ENFORCE_MS = 400

    def __init__(self, app: "ReminderApp", seconds: int, steal_focus: bool, primary_only: bool):
        self.app = app
        self.total = seconds
        self.steal_focus = steal_focus
        self.closed = False
        self.deadline = time.monotonic() + seconds

        self.win = tk.Toplevel(app.root)
        self.win.title("Movement break")
        self.win.configure(bg=BG)
        # Ignore every request to close: Alt+F4 and the window menu route here.
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)
        self._make_inescapable(primary_only)

        wrap = tk.Frame(self.win, bg=BG)
        if self.content_center is None:
            wrap.place(relx=0.5, rely=0.5, anchor="center")
        else:
            # Spanning every monitor would otherwise centre the text in the gap
            # between them; put it on the main screen instead.
            wrap.place(
                x=self.content_center[0], y=self.content_center[1], anchor="center"
            )

        tk.Label(
            wrap,
            text="GET UP AND WALK",
            bg=BG,
            fg=ACCENT,
            font=("Segoe UI", 34, "bold"),
        ).pack(pady=(0, 4))
        tk.Label(
            wrap,
            text="Stand, stretch, look away from the screen.",
            bg=BG,
            fg=FG_DIM,
            font=("Segoe UI", 14),
        ).pack(pady=(0, 24))

        self.clock = tk.Label(
            wrap,
            text=fmt_mmss(seconds),
            bg=BG,
            fg=FG,
            font=("Segoe UI", 96, "bold"),
        )
        self.clock.pack()

        self.bar_width = 520
        self.bar = tk.Canvas(
            wrap,
            width=self.bar_width,
            height=8,
            bg="#222c3c",
            highlightthickness=0,
        )
        self.bar.pack(pady=(18, 34))
        self.bar_fill = self.bar.create_rectangle(
            0, 0, self.bar_width, 8, fill=ACCENT, width=0
        )

        make_button(
            wrap, "Emergency: end break early", self.end_early, danger=True
        ).pack()
        tk.Label(
            wrap,
            text="Only use this if something actually needs you right now.",
            bg=BG,
            fg=FG_DIM,
            font=("Segoe UI", 9),
        ).pack(pady=(10, 0))

        # Nothing else gets a say while this is up.
        for seq in ("<Escape>", "<Alt-F4>", "<Control-w>", "<Control-q>"):
            self.win.bind(seq, lambda _e: "break")

        play_chime(rising=True)
        self._enforce()
        self._tick()

    def _make_inescapable(self, primary_only: bool) -> None:
        # Set by _span_all_monitors when the window covers more than one screen;
        # None means "just centre in the window".
        self.content_center = None
        spanned = False
        if SYSTEM == "Windows" and not primary_only:
            spanned = self._span_all_monitors()
        if not spanned:
            # Set the geometry explicitly as well as asking for fullscreen: on a
            # normal desktop the fullscreen attribute wins and also drops the
            # title bar, but if the window manager ignores it the geometry still
            # covers the screen.
            self.win.geometry(
                "%dx%d+0+0"
                % (self.win.winfo_screenwidth(), self.win.winfo_screenheight())
            )
            try:
                self.win.attributes("-fullscreen", True)
            except tk.TclError:
                pass
        try:
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.win.config(cursor="arrow")

    def _span_all_monitors(self) -> bool:
        """Cover the whole virtual desktop so a second screen isn't an escape hatch."""
        try:
            metrics = ctypes.windll.user32.GetSystemMetrics
            x = metrics(76)  # SM_XVIRTUALSCREEN
            y = metrics(77)  # SM_YVIRTUALSCREEN
            width = metrics(78)  # SM_CXVIRTUALSCREEN
            height = metrics(79)  # SM_CYVIRTUALSCREEN
            primary_w = metrics(0)  # SM_CXSCREEN
            primary_h = metrics(1)  # SM_CYSCREEN
            if width <= 0 or height <= 0:
                return False
            self.win.overrideredirect(True)
            self.win.geometry("%dx%d+%d+%d" % (width, height, x, y))
            self.content_center = primary_center_in_span(
                (x, y), (primary_w, primary_h)
            )
            return True
        except Exception:
            return False

    def _enforce(self) -> None:
        """Keep the window on top, unminimised and focused."""
        if self.closed:
            return
        try:
            if self.win.state() == "iconic":
                self.win.deiconify()
            self.win.attributes("-topmost", True)
            self.win.lift()
            if self.steal_focus and self.win.focus_displayof() is None:
                self.win.focus_force()
        except tk.TclError:
            return
        self.win.after(self.ENFORCE_MS, self._enforce)

    def _tick(self) -> None:
        if self.closed:
            return
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.finish(completed=True)
            return
        self.clock.config(text=fmt_mmss(remaining))
        fraction = max(0.0, min(1.0, remaining / self.total))
        try:
            self.bar.coords(self.bar_fill, 0, 0, self.bar_width * fraction, 8)
        except tk.TclError:
            pass
        self.win.after(200, self._tick)

    def end_early(self) -> None:
        self.finish(completed=False)

    def finish(self, completed: bool) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        play_chime(rising=False)
        self.app.end_break(completed)


# --------------------------------------------------------------------------
# Settings dialog
# --------------------------------------------------------------------------

class SettingsWindow:
    """Edit the settings and write them to disk so they survive a restart."""

    FIELDS = [
        ("interval_minutes", "Minutes between breaks", float, 0.1, 24 * 60),
        ("break_seconds", "Break length (seconds)", int, 5, 3600),
        ("delay_seconds", "\"Delay\" button postpones by (seconds)", int, 5, 3600),
        ("grace_seconds", "Alert waits before starting (seconds)", int, 0, 600),
        ("idle_minutes", "Count as away after (minutes idle)", float, 0.5, 240),
    ]

    def __init__(self, app: "ReminderApp"):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Settings")
        self.win.configure(bg=BG_PANEL)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        center(self.win, 460, 470)

        self.entries = {}
        self.vars = {}

        tk.Label(
            self.win, text="Settings", bg=BG_PANEL, fg=FG,
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=22, pady=(18, 2))
        tk.Label(
            self.win, text="Saved to %s" % CONFIG_PATH, bg=BG_PANEL, fg=FG_DIM,
            font=("Segoe UI", 8), wraplength=410, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=22, pady=(0, 12))

        row = 2
        for key, label, _cast, _lo, _hi in self.FIELDS:
            tk.Label(
                self.win, text=label, bg=BG_PANEL, fg=FG, font=("Segoe UI", 10),
            ).grid(row=row, column=0, sticky="w", padx=(22, 8), pady=4)
            entry = tk.Entry(
                self.win, width=8, bg="#0d1219", fg=FG, insertbackground=FG,
                relief="flat", justify="right", font=("Segoe UI", 10),
                highlightthickness=1, highlightbackground="#2a3648",
                highlightcolor=ACCENT,
            )
            entry.insert(0, self._format(getattr(app, "cfg")[key]))
            entry.grid(row=row, column=1, sticky="e", padx=(0, 22), pady=4)
            self.entries[key] = entry
            row += 1

        idle_ok = idle_seconds() is not None
        checks = [
            ("idle_skip", "Skip reminders while I'm away from the desk", idle_ok),
            ("minimize_to_tray", "Closing the window hides it in the tray", True),
            ("start_hidden", "Start hidden in the tray", True),
            ("steal_focus", "Break window keeps grabbing focus", True),
            ("primary_only", "Break covers only the main monitor", True),
        ]
        for key, label, enabled in checks:
            var = tk.BooleanVar(value=bool(app.cfg[key]))
            self.vars[key] = var
            box = tk.Checkbutton(
                self.win, text=label, variable=var, bg=BG_PANEL, fg=FG,
                selectcolor="#0d1219", activebackground=BG_PANEL,
                activeforeground=FG, font=("Segoe UI", 10), anchor="w",
                highlightthickness=0, bd=0,
                state="normal" if enabled else "disabled",
                disabledforeground=FG_DIM,
            )
            box.grid(row=row, column=0, columnspan=2, sticky="w", padx=18, pady=1)
            row += 1

        if not idle_ok:
            tk.Label(
                self.win,
                text="(idle detection isn't available on this system)",
                bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 8),
            ).grid(row=row, column=0, columnspan=2, sticky="w", padx=40)
            row += 1

        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        auto_box = tk.Checkbutton(
            self.win,
            text="Start automatically when I log in",
            variable=self.autostart_var, bg=BG_PANEL, fg=FG,
            selectcolor="#0d1219", activebackground=BG_PANEL,
            activeforeground=FG, font=("Segoe UI", 10), anchor="w",
            highlightthickness=0, bd=0,
            state="normal" if autostart_supported() else "disabled",
            disabledforeground=FG_DIM,
        )
        auto_box.grid(row=row, column=0, columnspan=2, sticky="w", padx=18, pady=(10, 0))
        row += 1

        self.message = tk.Label(
            self.win, text="", bg=BG_PANEL, fg=DANGER, font=("Segoe UI", 9),
            wraplength=410, justify="left",
        )
        self.message.grid(row=row, column=0, columnspan=2, sticky="w", padx=22, pady=(10, 0))
        row += 1

        buttons = tk.Frame(self.win, bg=BG_PANEL)
        buttons.grid(row=row, column=0, columnspan=2, pady=(14, 0))
        make_button(buttons, "Save", self.save, primary=True, small=True).pack(
            side="left", padx=5
        )
        make_button(buttons, "Cancel", self.close, small=True).pack(side="left", padx=5)

        self.win.bind("<Return>", lambda _e: self.save())
        self.win.bind("<Escape>", lambda _e: self.close())
        self.win.after(30, self._raise)

    def _raise(self) -> None:
        try:
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    @staticmethod
    def _format(value) -> str:
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)

    def save(self) -> None:
        updated = {}
        for key, label, cast, low, high in self.FIELDS:
            raw = self.entries[key].get().strip().replace(",", ".")
            try:
                value = cast(float(raw)) if cast is int else cast(raw)
            except ValueError:
                self._fail("%s: please enter a number." % label)
                return
            if not low <= value <= high:
                self._fail("%s: must be between %s and %s." % (label, low, high))
                return
            updated[key] = value

        for key, var in self.vars.items():
            updated[key] = bool(var.get())

        if autostart_supported() and self.autostart_var.get() != autostart_enabled():
            ok, message = set_autostart(self.autostart_var.get())
            if not ok:
                self._fail(message)
                self.autostart_var.set(autostart_enabled())
                return

        self.app.apply_settings(updated, persist=True)
        self.close()

    def _fail(self, message: str) -> None:
        self.message.config(text=message)

    def close(self) -> None:
        self.app.settings_win = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Control panel + scheduler
# --------------------------------------------------------------------------

class ReminderApp:
    def __init__(self, cfg: dict, use_tray: bool = True):
        self.cfg = dict(cfg)
        self.use_tray = use_tray
        self._apply_values(self.cfg)

        self.paused = False
        self.alert: AlertWindow | None = None
        self.break_win: BreakWindow | None = None
        self.settings_win: SettingsWindow | None = None
        self.breaks_taken = 0
        self.breaks_skipped = 0
        self.delays = 0
        self.away_pauses = 0
        self.away = False
        self._was_away = False
        self._away_remaining = 0.0
        self._idle_cache = (0.0, None)
        self._flash = ("", 0.0)
        self._tray_tip = ""
        self._tray_hint_shown = False
        self._tick_id = None
        self.tray = None
        self.tray_actions: "queue.Queue" = queue.Queue()
        self.next_at = time.monotonic() + self.interval

        self.root = tk.Tk()
        self.root.report_callback_exception = self._on_callback_error
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        center(self.root, 380, 360)
        self.root.protocol("WM_DELETE_WINDOW", self.close_panel)
        self._build_panel()
        self._start_tray()
        self.root.bind("<Unmap>", self._on_unmap)
        if self.cfg.get("start_hidden") and self.tray is not None:
            self.root.withdraw()
        self._tick()

    # -- settings ---------------------------------------------------------

    def _apply_values(self, cfg: dict) -> None:
        self.interval = max(5, int(float(cfg["interval_minutes"]) * 60))
        self.break_seconds = max(5, int(cfg["break_seconds"]))
        self.delay_seconds = max(5, int(cfg["delay_seconds"]))
        self.grace_seconds = max(0, int(cfg["grace_seconds"]))
        self.steal_focus = bool(cfg["steal_focus"])
        self.primary_only = bool(cfg["primary_only"])
        self.idle_skip = bool(cfg["idle_skip"])
        self.idle_threshold = max(10.0, float(cfg["idle_minutes"]) * 60.0)
        self.minimize_to_tray = bool(cfg["minimize_to_tray"])

    def apply_settings(self, updated: dict, persist: bool = False) -> None:
        previous = self.interval
        self.cfg.update(updated)
        self._apply_values(self.cfg)
        if persist and not save_config(self.cfg):
            self.flash("Couldn't save settings to disk")
        if self.interval != previous and self.alert is None and self.break_win is None:
            self.next_at = time.monotonic() + self.interval
        self._refresh_subtitle()

    def open_settings(self) -> None:
        if self.settings_win is not None:
            self.settings_win._raise()
            return
        self.settings_win = SettingsWindow(self)

    def _on_callback_error(self, exc, value, tb) -> None:
        """Never let a callback blow up silently - and never leave you locked in."""
        detail = "".join(traceback.format_exception(exc, value, tb))
        if self.break_win is not None:
            # A crash mid-break must not trap you behind the lock screen.
            try:
                self.break_win.finish(completed=False)
            except Exception:
                pass
        report_fatal("%s hit an error:\n\n%s" % (APP_NAME, detail))

    # -- UI ---------------------------------------------------------------

    def _build_panel(self) -> None:
        tk.Label(
            self.root,
            text=APP_NAME,
            bg=BG,
            fg=FG,
            font=("Segoe UI", 14, "bold"),
        ).pack(pady=(20, 2))
        self.subtitle = tk.Label(
            self.root, text="", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)
        )
        self.subtitle.pack()
        self._refresh_subtitle()

        self.countdown = tk.Label(
            self.root, text="--:--", bg=BG, fg=ACCENT, font=("Segoe UI", 40, "bold")
        )
        self.countdown.pack(pady=(14, 0))
        self.status = tk.Label(
            self.root, text="until your next break", bg=BG, fg=FG_DIM,
            font=("Segoe UI", 10)
        )
        self.status.pack()

        snooze_row = tk.Frame(self.root, bg=BG)
        snooze_row.pack(pady=(16, 0))
        tk.Label(
            snooze_row, text="push back", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 6))
        for minutes in (5, 10):
            make_button(
                snooze_row,
                "+%d min" % minutes,
                lambda m=minutes: self.snooze(m),
                small=True,
            ).pack(side="left", padx=3)

        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(12, 0))
        make_button(row, "Break now", self.trigger_now, small=True).pack(
            side="left", padx=3
        )
        self.pause_btn = make_button(row, "Pause", self.toggle_pause, small=True)
        self.pause_btn.pack(side="left", padx=3)
        make_button(row, "Settings", self.open_settings, small=True).pack(
            side="left", padx=3
        )

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(pady=(8, 0))
        self.hide_btn = make_button(bottom, "Hide to tray", self.hide_panel, small=True)
        make_button(bottom, "Quit", self.quit, small=True).pack(side="right", padx=3)

        self.tally = tk.Label(
            self.root, text="", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)
        )
        self.tally.pack(pady=(14, 0))

    def _refresh_subtitle(self) -> None:
        minutes = self.interval / 60.0
        shown = int(minutes) if minutes == int(minutes) else round(minutes, 1)
        self.subtitle.config(
            text="%s min between breaks · %s break"
            % (shown, fmt_mmss(self.break_seconds))
        )

    def _refresh_tally(self) -> None:
        text = "breaks taken %d · ended early %d · delays %d" % (
            self.breaks_taken,
            self.breaks_skipped,
            self.delays,
        )
        if self.away_pauses:
            text += " · away %d" % self.away_pauses
        self.tally.config(text=text)

    def flash(self, message: str, seconds: float = 3.0) -> None:
        """Show a transient line under the countdown."""
        self._flash = (message, time.monotonic() + seconds)

    # -- scheduling -------------------------------------------------------

    def _idle(self):
        """Seconds since last input, polled at most once a second."""
        now = time.monotonic()
        if now - self._idle_cache[0] < 1.0:
            return self._idle_cache[1]
        value = idle_seconds()
        self._idle_cache = (now, value)
        return value

    def _tick(self) -> None:
        self._drain_tray_actions()
        now = time.monotonic()
        busy = self.alert is not None or self.break_win is not None

        self.away = False
        if self.idle_skip and not self.paused and not busy:
            idle = self._idle()
            if idle is not None and idle >= self.idle_threshold:
                self.away = True
        if self.away:
            if not self._was_away:
                self.away_pauses += 1
                self._away_remaining = max(0.0, self.next_at - now)
            # Away from the desk pauses the clock where it stood, so you pick up
            # the same countdown when you sit back down.
            self.next_at = now + self._away_remaining
        self._was_away = self.away

        if self.paused:
            self.countdown.config(text="paused", fg=DANGER)
            self.status.config(text="reminders are off")
        elif busy:
            self.countdown.config(text="now", fg=ACCENT)
            self.status.config(text="break in progress")
        elif self.away:
            self.countdown.config(text=fmt_long(self._away_remaining), fg=DANGER)
            self.status.config(text="paused - you're away from the desk")
        else:
            remaining = self.next_at - now
            if remaining <= 0:
                self.show_alert()
            else:
                self.countdown.config(text=fmt_long(remaining), fg=ACCENT)
                self.status.config(text="until your next break")

        message, until = self._flash
        if message and now < until:
            self.status.config(text=message)
        elif message:
            self._flash = ("", 0.0)

        self._refresh_tally()
        self._refresh_tray()
        self._tick_id = self.root.after(250, self._tick)

    # -- tray -------------------------------------------------------------

    def _start_tray(self) -> None:
        if SYSTEM != "Windows" or not self.use_tray:
            return
        try:
            tray = WindowsTray(self.tray_actions)
        except Exception:
            log_note("Tray icon unavailable:\n%s" % traceback.format_exc())
            return
        if tray.ok:
            global NOTIFY_HOOK
            self.tray = tray
            NOTIFY_HOOK = tray.show_message
            self.hide_btn.pack(side="left", padx=3)
        elif tray.error:
            # Not worth interrupting anyone over - the window still works.
            log_note("Tray icon unavailable:\n%s" % tray.error)

    def _refresh_tray(self) -> None:
        if self.tray is None:
            return
        self.tray.paused = self.paused
        if self.paused:
            tip = "%s - paused" % APP_NAME
        elif self.alert is not None or self.break_win is not None:
            tip = "%s - break in progress" % APP_NAME
        elif self.away:
            tip = "%s - paused, you're away" % APP_NAME
        else:
            tip = "%s - next break in %s" % (
                APP_NAME,
                fmt_long(self.next_at - time.monotonic()),
            )
        if tip != self._tray_tip:
            self._tray_tip = tip
            self.tray.set_tooltip(tip)

    def _drain_tray_actions(self) -> None:
        while True:
            try:
                action = self.tray_actions.get_nowait()
            except queue.Empty:
                return
            if action == "show":
                self.show_panel()
            elif action == "break":
                self.trigger_now()
            elif action == "pause":
                self.toggle_pause()
            elif action == "quit":
                self.quit()

    # -- window visibility ------------------------------------------------

    def show_panel(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def hide_panel(self) -> bool:
        if self.tray is None or not self.tray.ok:
            return False
        try:
            self.root.withdraw()
        except tk.TclError:
            return False
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.show_message(
                APP_NAME,
                "Still running. Right-click the tray icon for Break now, "
                "Pause or Quit.",
            )
        return True

    def close_panel(self) -> None:
        """The window's X button: to the tray if we have one, otherwise quit."""
        if self.break_win is not None:
            return
        if self.minimize_to_tray and self.hide_panel():
            return
        self.quit()

    def _on_unmap(self, event) -> None:
        if event.widget is not self.root or not self.minimize_to_tray:
            return
        try:
            if self.root.state() == "iconic":
                self.root.after(10, self.hide_panel)
        except tk.TclError:
            pass

    def show_alert(self) -> None:
        if self.alert is not None or self.break_win is not None:
            return
        self.alert = AlertWindow(self, self.grace_seconds)

    def delay_alert(self) -> None:
        self.alert = None
        self.delays += 1
        self.next_at = time.monotonic() + self.delay_seconds
        self.root.title("%s — delayed %s" % (APP_NAME, fmt_mmss(self.delay_seconds)))

    def begin_break(self) -> None:
        self.alert = None
        self.root.title(APP_NAME)
        self.break_win = BreakWindow(
            self, self.break_seconds, self.steal_focus, self.primary_only
        )

    def end_break(self, completed: bool) -> None:
        self.break_win = None
        if completed:
            self.breaks_taken += 1
        else:
            self.breaks_skipped += 1
        self.next_at = time.monotonic() + self.interval
        try:
            self.root.attributes("-topmost", False)
            self.root.lift()
        except tk.TclError:
            pass

    def snooze(self, minutes: float) -> None:
        """Push the next break back, e.g. when you're mid-meeting."""
        if self.break_win is not None:
            return
        if self.alert is not None:
            self.alert.close()
            self.alert = None
            self.next_at = time.monotonic()
        self.next_at = max(self.next_at, time.monotonic()) + minutes * 60
        if self.paused:
            self.paused = False
            self.pause_btn.config(text="Pause")
        self.flash("pushed back %g min" % minutes)

    def trigger_now(self) -> None:
        if self.alert is not None or self.break_win is not None:
            return
        self.paused = False
        self.pause_btn.config(text="Pause")
        self.show_alert()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_btn.config(text="Resume" if self.paused else "Pause")
        if not self.paused:
            self.next_at = time.monotonic() + self.interval

    def quit(self) -> None:
        if self.break_win is not None:
            # No quitting your way out of a break.
            return
        if self.tray is not None:
            self.tray.stop()
        try:
            if self._tick_id is not None:
                self.root.after_cancel(self._tick_id)
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Hourly reminder to get up and walk, with a locked 3-minute break "
            "timer. Settings are remembered in %s; flags below override them "
            "for this run only." % CONFIG_PATH
        )
    )
    # Defaults are None so we can tell "not given" from "given the same value",
    # and only override the saved settings for flags actually passed.
    parser.add_argument(
        "--interval", type=float, metavar="MINUTES", help="minutes between breaks"
    )
    parser.add_argument(
        "--break-length", type=int, metavar="SECONDS", help="length of the locked break"
    )
    parser.add_argument(
        "--delay",
        type=int,
        metavar="SECONDS",
        help="how long the emergency delay button postpones the alarm",
    )
    parser.add_argument(
        "--grace",
        type=int,
        metavar="SECONDS",
        help="seconds the alert waits before starting the break itself",
    )
    parser.add_argument(
        "--no-focus-steal",
        action="store_true",
        help="don't keep pulling focus back to the break window",
    )
    parser.add_argument(
        "--primary-only",
        action="store_true",
        help="cover only the primary monitor during a break",
    )
    parser.add_argument(
        "--no-idle-skip",
        action="store_true",
        help="remind me even when I've been away from the keyboard",
    )
    parser.add_argument(
        "--no-tray", action="store_true", help="don't use a system tray icon"
    )
    parser.add_argument(
        "--hidden", action="store_true", help="start hidden in the tray"
    )
    parser.add_argument(
        "--reset-settings",
        action="store_true",
        help="ignore the saved settings and start from the defaults",
    )
    return parser.parse_args(argv)


def settings_from(args: argparse.Namespace) -> dict:
    """Saved settings, with any explicitly passed flags layered on top."""
    cfg = dict(DEFAULTS) if args.reset_settings else load_config()
    overrides = {
        "interval_minutes": args.interval,
        "break_seconds": args.break_length,
        "delay_seconds": args.delay,
        "grace_seconds": args.grace,
    }
    for key, value in overrides.items():
        if value is not None:
            cfg[key] = value
    if args.no_focus_steal:
        cfg["steal_focus"] = False
    if args.primary_only:
        cfg["primary_only"] = True
    if args.no_idle_skip:
        cfg["idle_skip"] = False
    if args.no_tray:
        cfg["minimize_to_tray"] = False
        cfg["start_hidden"] = False
    if args.hidden:
        cfg["start_hidden"] = True
    return cfg


def main() -> int:
    args = parse_args()

    if tk is None:
        report_fatal(NO_TKINTER_HELP % (TK_IMPORT_ERROR,))
        return 2

    if SYSTEM == "Windows":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    try:
        ReminderApp(settings_from(args), use_tray=not args.no_tray).run()
    except Exception:
        report_fatal(
            "%s hit an unexpected error and had to stop.\n\n%s"
            % (APP_NAME, traceback.format_exc())
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
