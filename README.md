# Remote PC Control

Control your Windows PC from your phone, over your own Wi-Fi. The screen is
streamed to a web page, taps become clicks, and your phone keyboard types
straight into whatever field the PC is focused on.

No app to install on the phone — it is a web page.

## What it does

The screen streams as MJPEG at about 20 fps, with pinch to zoom, drag to pan and
a full-screen mode. Multi-monitor setups are handled: a button cycles through
them.

Typing is the part worth explaining. The server watches which element has
keyboard focus on the PC; when that element accepts text, a badge appears on the
phone. Tap it and the phone keyboard opens, then every character goes through as
you type it — Enter and backspace included. If you were already in a text field,
a single tap on the screen opens the keyboard directly.

The reason it works that way rather than opening the keyboard by itself: iOS only
raises the keyboard when `focus()` happens inside a real user gesture. A timer
that notices "the PC is in a text field" cannot raise it on its own, so the badge
gives you the gesture to make.

## Requirements

Windows, Python 3.10 or later, and a phone on the same network.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

`comtypes` is what gives access to UI Automation. Without it the tool still runs,
falling back to the Win32 caret, which only sees native windows — browsers,
Electron apps and Store apps draw their own caret and would go unnoticed.

## Run

```powershell
python app.py
```

It prints the address to open on your phone and the PIN. Both are configurable
through environment variables: `REMOTE_PIN`, `STREAM_WIDTH`, `JPEG_QUALITY`,
`FRAME_DELAY`.

## Full screen on iPhone

Open the address in Safari, then **Share → Add to Home Screen**. Launched from
that icon, the page opens with no address bar and no tab bar, in either
orientation, and looks like an app.

That detour exists because `requestFullscreen()` is not available on iPhone
Safari — only a `<video>` element can take over the screen there, which is how
YouTube does it. A screen stream drawn into an `<img>` has no such option, so
the home screen route is the only way to be rid of Safari's chrome.

The clock and battery stay visible: iOS never lets a web page hide its status
bar. The page does draw underneath it rather than stopping short of it.

To check what the focus detection sees in your own applications, run it on its
own and click around:

```powershell
python focus_detect.py
```

It prints a line whenever the focused element changes, telling you whether it is
considered a text field and which mechanism answered.

## Security

Read this before leaving it running.

Anyone who reaches the port gets **full mouse and keyboard control** of the
machine. The only barrier is a PIN, sent in clear text over plain HTTP, and the
default one is published in this repository — change it:

```powershell
$env:REMOTE_PIN = "your-own-pin"
python app.py
```

This is built for a home network. Do not forward the port, and do not run it on
a network you do not control. The server is Flask's development server, which is
not written for exposure.

Moving the mouse into a screen corner triggers pyautogui's failsafe and stops
input immediately.
