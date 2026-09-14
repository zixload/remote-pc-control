# Remote PC Control

Control your Windows PC from your phone over your own network. The screen
streams to a web page, taps become clicks, and your phone keyboard types into
whatever the PC is focused on. Nothing to install on the phone — it is a web
page.

## Quick start

```powershell
py -3 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

It prints an address and a PIN. Open the address in your phone's browser, enter
the PIN, and you are in. For a proper app icon, use **Share → Add to Home
Screen** in Safari.

## Desktop app (optional)

A small Windows app launches the server, shows QR codes for the address, and
keeps your settings. Needs the .NET 10 SDK.

```powershell
.\build.ps1
```

This builds `dist\remote-pc-server.exe` (the server) and the interface under
`shell\bin\Release\...\Remote PC.exe`. Pin the interface to your taskbar. It
asks for a PIN on first launch, then shows a QR code to scan — including a
second one for remote access when Tailscale is running.

## Gestures

| Gesture | Effect |
| --- | --- |
| Tap | Left click |
| Two taps, same spot | Right click |
| Press and hold, then move | Drag |
| Two fingers, slide | Scroll |
| Two fingers, spread | Zoom |
| One finger, slide when zoomed | Pan |
| Push past the edge | Next screen |

The button bar also has **Sound**, **Files**, a keyboard, and a **Cinema** mode
(full-screen video, no interaction, a few seconds behind).

## Features

- **Sound** — hear the PC in the interactive view, ~0.2s behind. Needs ffmpeg.
- **Files** — one shared folder both ways: send from the phone, save to it, or
  drag files onto the desktop app. Defaults to `Downloads\Remote PC`.
- **Cinema** — real full-screen HLS video with sound. Needs ffmpeg.
- **Remote access** — reach the PC from anywhere with [Tailscale](https://tailscale.com):
  install it on both devices, sign into the same account, and use the tailnet
  address. Nothing is exposed publicly.

## Options

Everything is adjustable, on the command line or in the desktop app's settings:

```powershell
python app.py --pin 12345678 --width 1280 --quality 80 --fps 25
python app.py --audio "Personal Mix (Elgato Virtual Audio)"
python app.py --list-audio     # measure each audio device's level
python app.py --help
```

## Security

Anyone who gets past the PIN has **full control** of the machine, so the PIN is
the whole of the security:

- No default PIN. The desktop app asks for one (eight digits minimum); the
  command line generates a random one if you don't pass `--pin`.
- Login attempts are rate-limited (five tries, then a lockout that doubles from
  30s to 15 min), so the PIN can't be brute-forced.

This is built for a network you control. **Do not forward a port to it** — use
Tailscale for remote access instead. The server is Flask's development server,
not meant for public exposure. Being unsigned, the executables may trigger a
SmartScreen warning on first launch.
