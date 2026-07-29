#!/usr/bin/env python3
"""Hourly movement reminder.

Every hour it makes a noise and pops up an alert telling you to get up and
walk. The alert has an emergency "delay 2 minutes" button. If you don't touch
it, the break starts on its own: a full-screen window that stays on top, keeps
itself focused and refuses to be minimised or closed for 3 minutes. That window
has an emergency "end break early" button.

Standard library only (Python 3.8+ with tkinter). Run it with:

    python movement_reminder.py

Handy flags for trying it out without waiting an hour:

    python movement_reminder.py --interval 1 --break-length 15 --grace 5
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
import tkinter as tk

SYSTEM = platform.system()

# Palette
BG = "#0f141c"
BG_PANEL = "#161d29"
FG = "#e8edf5"
FG_DIM = "#8b98ad"
ACCENT = "#4ade80"
DANGER = "#f0883e"

APP_NAME = "Movement Reminder"


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


def notify(title: str, message: str) -> None:
    """Desktop notification, on top of the app's own popup."""
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
        wrap.place(relx=0.5, rely=0.5, anchor="center")

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
            import ctypes

            metrics = ctypes.windll.user32.GetSystemMetrics
            x = metrics(76)  # SM_XVIRTUALSCREEN
            y = metrics(77)  # SM_YVIRTUALSCREEN
            width = metrics(78)  # SM_CXVIRTUALSCREEN
            height = metrics(79)  # SM_CYVIRTUALSCREEN
            if width <= 0 or height <= 0:
                return False
            self.win.overrideredirect(True)
            self.win.geometry("%dx%d+%d+%d" % (width, height, x, y))
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
# Control panel + scheduler
# --------------------------------------------------------------------------

class ReminderApp:
    def __init__(self, args: argparse.Namespace):
        self.interval = max(1, int(args.interval * 60))
        self.break_seconds = max(5, int(args.break_length))
        self.delay_seconds = max(5, int(args.delay))
        self.grace_seconds = max(0, int(args.grace))
        self.steal_focus = not args.no_focus_steal
        self.primary_only = args.primary_only

        self.paused = False
        self.alert: AlertWindow | None = None
        self.break_win: BreakWindow | None = None
        self.breaks_taken = 0
        self.breaks_skipped = 0
        self.delays = 0
        self.next_at = time.monotonic() + self.interval

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        center(self.root, 380, 260)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._build_panel()
        self._tick()

    # -- UI ---------------------------------------------------------------

    def _build_panel(self) -> None:
        tk.Label(
            self.root,
            text=APP_NAME,
            bg=BG,
            fg=FG,
            font=("Segoe UI", 14, "bold"),
        ).pack(pady=(20, 2))
        tk.Label(
            self.root,
            text="%d min between breaks · %s break"
            % (self.interval // 60, fmt_mmss(self.break_seconds)),
            bg=BG,
            fg=FG_DIM,
            font=("Segoe UI", 9),
        ).pack()

        self.countdown = tk.Label(
            self.root, text="--:--", bg=BG, fg=ACCENT, font=("Segoe UI", 40, "bold")
        )
        self.countdown.pack(pady=(14, 0))
        self.status = tk.Label(
            self.root, text="until your next break", bg=BG, fg=FG_DIM,
            font=("Segoe UI", 10)
        )
        self.status.pack()

        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(20, 0))
        make_button(row, "Break now", self.trigger_now, small=True).pack(
            side="left", padx=4
        )
        self.pause_btn = make_button(row, "Pause", self.toggle_pause, small=True)
        self.pause_btn.pack(side="left", padx=4)
        make_button(row, "Quit", self.quit, small=True).pack(side="left", padx=4)

        self.tally = tk.Label(
            self.root, text="", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)
        )
        self.tally.pack(pady=(16, 0))

    def _refresh_tally(self) -> None:
        self.tally.config(
            text="breaks taken %d · ended early %d · delays %d"
            % (self.breaks_taken, self.breaks_skipped, self.delays)
        )

    # -- scheduling -------------------------------------------------------

    def _tick(self) -> None:
        busy = self.alert is not None or self.break_win is not None
        if self.paused:
            self.countdown.config(text="paused", fg=DANGER)
            self.status.config(text="reminders are off")
        elif busy:
            self.countdown.config(text="now", fg=ACCENT)
            self.status.config(text="break in progress")
        else:
            remaining = self.next_at - time.monotonic()
            if remaining <= 0:
                self.show_alert()
            else:
                self.countdown.config(text=fmt_long(remaining), fg=ACCENT)
                self.status.config(text="until your next break")
        self._refresh_tally()
        self.root.after(250, self._tick)

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
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hourly reminder to get up and walk, with a locked 3-minute break timer."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        metavar="MINUTES",
        help="minutes between breaks (default: 60)",
    )
    parser.add_argument(
        "--break-length",
        type=int,
        default=180,
        metavar="SECONDS",
        help="length of the locked break in seconds (default: 180)",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=120,
        metavar="SECONDS",
        help="how long the emergency delay button postpones the alarm (default: 120)",
    )
    parser.add_argument(
        "--grace",
        type=int,
        default=20,
        metavar="SECONDS",
        help="seconds the alert waits before starting the break itself (default: 20)",
    )
    parser.add_argument(
        "--no-focus-steal",
        action="store_true",
        help="don't keep pulling focus back to the break window",
    )
    parser.add_argument(
        "--primary-only",
        action="store_true",
        help="cover only the primary monitor during a break (Windows spans all by default)",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    if SYSTEM == "Windows":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    ReminderApp(args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
