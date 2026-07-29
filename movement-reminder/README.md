# Movement Reminder

A small desktop app that nags you to get up and walk every hour.

Every hour it plays a sound and pops up an alert. If you ignore it for 20
seconds the break starts by itself: a full-screen countdown that stays on top,
keeps pulling focus back to itself and can't be minimised or closed until the
3 minutes are up.

Two escape hatches, exactly as intended:

- **Delay 2 minutes** — on the alert, before the break starts. Pushes the whole
  thing back by 2 minutes.
- **Emergency: end break early** — on the lock screen. Ends the 3 minutes
  immediately and schedules the next break an hour out.

## Running it

Needs Python 3.8 or newer with tkinter, which is included in the standard
Windows installer from [python.org](https://www.python.org/downloads/) — tick
**"Add python.exe to PATH"** during setup.

- **Windows**: double-click `Start Movement Reminder.bat` (runs without a
  console window).
- **Anything else**: `python3 movement_reminder.py`
  (on Debian/Ubuntu you may need `sudo apt install python3-tk` first).

A small control panel opens showing the countdown to the next break, with
**Break now**, **Pause** and **Quit**. Minimise it and forget about it — the
break takes over the screen on its own when it's due.

## If nothing happens when you launch it

A brief console flash is normal — the batch file hands off to `pythonw` (which
has no console) and exits. If no window appears after that, run
**`Troubleshoot.bat`**: it runs the app with a visible console, prints which
Python it found, and stays open so you can read the error. The app also writes
failures to `movement-reminder-error.log` next to the script, and shows them in
a message box.

The two usual causes on Windows:

- **The Microsoft Store placeholder.** Windows ships stub `python.exe` /
  `pythonw.exe` in `%LOCALAPPDATA%\Microsoft\WindowsApps` that sit on your PATH
  even if you installed Python from python.org. The stub `pythonw.exe` exits
  silently — a perfect "nothing happened". Either install from
  [python.org](https://www.python.org/downloads/), or turn the aliases off under
  **Settings → Apps → Advanced app settings → App execution aliases**. The
  launcher now prefers the `py`/`pyw` launcher, which sidesteps this entirely.
- **Python without tkinter.** Re-run the python.org installer, choose
  **Modify**, and tick **"tcl/tk and IDLE"**.

## Start it automatically at login (Windows)

1. Press <kbd>Win</kbd>+<kbd>R</kbd>, type `shell:startup`, press Enter.
2. Right-drag `Start Movement Reminder.bat` into that folder and choose
   **Create shortcuts here**.

## Options

```
python movement_reminder.py --interval 45 --break-length 120
```

| Flag | Default | What it does |
| --- | --- | --- |
| `--interval MINUTES` | `60` | Minutes between breaks |
| `--break-length SECONDS` | `180` | Length of the locked break |
| `--delay SECONDS` | `120` | How long the emergency delay button postpones the alarm |
| `--grace SECONDS` | `20` | How long the alert waits before starting the break itself |
| `--no-focus-steal` | off | Stop the break window yanking focus back every 0.4s |
| `--primary-only` | off | Cover only the main monitor (Windows spans all screens by default) |

To try it without waiting an hour:

```
python movement_reminder.py --interval 1 --break-length 15 --grace 5
```

To change the defaults permanently, either edit the `set FLAGS=` line in
`Start Movement Reminder.bat` or change the `default=` values in
`parse_args()` near the bottom of `movement_reminder.py`.

## What it can and can't lock down

The break window covers every monitor, sits above the taskbar, refuses
`Alt`+`F4` and the close button, un-minimises itself if something minimises it,
and grabs focus back roughly twice a second — so typing goes nowhere else while
it's up. What no ordinary program can block is the operating system itself:
<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Del</kbd>, the Windows lock screen and Task
Manager still work. That's deliberate on Windows' part, and it means Task
Manager is always the true last resort.
