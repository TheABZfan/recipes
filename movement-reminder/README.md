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
**+5 / +10 min**, **Break now**, **Pause**, **Settings** and **Quit**. Close it
and forget about it — on Windows it tucks into the system tray and the break
still takes over the screen when it's due.

## Day-to-day

- **Push the next break back** with **+5 min** / **+10 min** on the panel, for
  when you're mid-meeting. They stack, so +5 twice is +10.
- **The tray icon** (Windows) shows the countdown when you hover it. Left-click
  reopens the window; right-click gives you Show / Break now / Pause / Quit.
  Closing or minimising the window hides it there rather than quitting — use
  **Quit** when you actually mean it.
- **Away from the desk?** If you haven't touched the keyboard or mouse for 5
  minutes when a break comes due, it doesn't fire — you're already up. The
  countdown restarts when you come back, so you get a full hour of sitting
  before the next one rather than an alarm at an empty chair.

## Settings

Click **Settings** on the panel. Everything is saved and reused next launch:

| Setting | Default | What it does |
| --- | --- | --- |
| Minutes between breaks | `60` | How often a break is due |
| Break length | `180s` | How long the lock screen holds you |
| "Delay" button postpones by | `120s` | The alert's emergency delay |
| Alert waits before starting | `20s` | Grace period before the break auto-starts |
| Count as away after | `5 min` | Idle time that counts as being away from the desk |
| Skip reminders while away | on | Don't nag an empty chair |
| Closing the window hides it in the tray | on | Off means the X button quits |
| Start hidden in the tray | off | Skip the window at launch |
| Break window keeps grabbing focus | on | Off makes the lock screen less aggressive |
| Break covers only the main monitor | off | On leaves your other screens usable |
| **Start automatically when I log in** | off | Ticking it sets up autostart for you |

Settings live in `%APPDATA%\MovementReminder\config.json` on Windows
(`~/.config/movement-reminder/config.json` elsewhere). Delete that file, or run
with `--reset-settings`, to go back to the defaults.

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

## Start it automatically at login

Tick **"Start automatically when I log in"** in Settings. On Windows that
creates a shortcut in your Startup folder pointing at `pythonw.exe`, so it
starts silently with no console flash; unticking it removes the shortcut. (On
Linux it writes `~/.config/autostart/movement-reminder.desktop` instead.)

Pair it with **"Start hidden in the tray"** and you'll never see the window
unless you ask for it.

If you'd rather do it by hand: <kbd>Win</kbd>+<kbd>R</kbd>, type
`shell:startup`, and put a shortcut to `Start Movement Reminder.bat` in there.

## Command-line flags

Flags override the saved settings **for that run only** — handy for testing
without disturbing your real setup.

| Flag | What it does |
| --- | --- |
| `--interval MINUTES` | Minutes between breaks |
| `--break-length SECONDS` | Length of the locked break |
| `--delay SECONDS` | How long the emergency delay button postpones the alarm |
| `--grace SECONDS` | How long the alert waits before starting the break itself |
| `--no-idle-skip` | Remind me even when I've been away from the keyboard |
| `--no-focus-steal` | Stop the break window yanking focus back every 0.4s |
| `--primary-only` | Cover only the main monitor |
| `--no-tray` | Plain window, no tray icon |
| `--hidden` | Start hidden in the tray |
| `--reset-settings` | Ignore the saved settings and use the defaults |

To try it without waiting an hour:

```
python movement_reminder.py --interval 1 --break-length 15 --grace 5
```

## What it can and can't lock down

The break window covers every monitor, sits above the taskbar, refuses
`Alt`+`F4` and the close button, un-minimises itself if something minimises it,
and grabs focus back roughly twice a second — so typing goes nowhere else while
it's up. What no ordinary program can block is the operating system itself:
<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Del</kbd>, the Windows lock screen and Task
Manager still work. That's deliberate on Windows' part, and it means Task
Manager is always the true last resort.
