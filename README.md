# Remote PC Control

Control your Windows PC from your phone, over your own Wi-Fi. The screen is
streamed to a web page, taps become clicks, and your phone keyboard types
straight into whatever field the PC is focused on.

No app to install on the phone — it is a web page.

## What it does

The screen streams as MJPEG at about 20 fps, with pinch to zoom and drag to pan.
Multi-monitor setups are handled: a button cycles through them.

Typing is the part worth explaining. The server watches which element has
keyboard focus on the PC; when that element accepts text, a badge appears on the
phone. Tap it and the phone keyboard opens, then every character goes through as
you type it — Enter and backspace included. If you were already in a text field,
a single tap on the screen opens the keyboard directly.

The reason it works that way rather than opening the keyboard by itself: iOS only
raises the keyboard when `focus()` happens inside a real user gesture. A timer
that notices "the PC is in a text field" cannot raise it on its own, so the badge
gives you the gesture to make.

## Gestures

| Gesture | Effect |
| --- | --- |
| Tap | Left click |
| Two taps, same spot | Right click |
| Press and hold, then move | Drag: move a window, select text |
| Two fingers, slide | Scroll wheel |
| Two fingers, spread | Zoom the view |
| One finger, slide when zoomed | Pan the view |
| One finger, pushed past the edge | Next screen, as if they sat side by side |

## Sound in the interactive view

Tap **Sound** to hear the PC while keeping mouse and keyboard control. The
button turns green and reads **Sound ON**; tap it again to stop. ffmpeg must be
installed, and the first tap takes about two seconds while ffmpeg opens the
audio device.

After that the sound runs roughly a fifth of a second behind, close enough to
the image to pass for live. Getting there meant leaving HLS: the button used to
play the same segmented stream as cinema mode, which put the sound three to
five seconds behind an image arriving in real time over MJPEG. The two came by
different roads and the gap was plain to hear.

Instead, ffmpeg now writes raw PCM to its standard output, the server relays it
over a WebSocket, and the phone schedules it into the Web Audio API as it
arrives. There is no container, no segment and nothing to buffer beyond the
scheduling margin the page keeps for itself — 120 ms, enough to absorb a Wi-Fi
hiccup without being heard. Measured on the local network, eight seconds of
listening delivers 8.00 seconds of audio: it runs at exactly real time, with
nothing accumulating.

The cost is bandwidth. 48 kHz stereo at 16 bits is 1.5 Mbit/s, against 128
kbit/s for the AAC inside the HLS stream. On a home network that is
inconsequential, and it is still less than the H.264 video the interactive view
used to download and throw away to get at the sound.

iOS requires a user gesture before any sound plays, and before an AudioContext
may even resume — which is exactly what the button provides. If the connection
drops, the button reads **Retry sound**; tap it again. The headset can be
selected from Control Center like any other iPhone audio output.

Cinema mode is untouched and still uses HLS. There, image and sound come from
the same stream, so they stay in step with each other; both are a few seconds
behind, which does not matter when you are only watching.

Pinch and two-finger scroll use the same number of fingers, so they are told
apart by the movement itself: a distance that changes clearly is a pinch, a
midpoint that travels while the distance holds is a scroll. Whichever crosses
its threshold first locks the gesture until you lift, otherwise it would waver
mid-move.

Pushing past the edge works without zooming as well. At scale 1 the view is
already at its limits, so the whole movement counts as overscroll — a hundred
and twenty pixels of it switches screens, once per slide, and a brief label
says which one you landed on since the button may be hidden.

The left click of the first tap is sent immediately, so a right click is always
preceded by a left one. Holding every click for 300ms to watch for a second
would add that delay to everything, which is felt at once on a remote control.

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

Building the desktop application on top also needs the .NET 10 SDK. The server
itself does not: it runs, and can be packaged, with Python alone.

## Run

```powershell
python app.py
```

It prints the address to open on your phone and the PIN.

Everything is adjustable at launch, since the next machine will not have your
screens, your audio devices or your network:

```powershell
python app.py --help
python app.py --pin 4821 --width 1280 --quality 80 --fps 25
python app.py --audio "Personal Mix (Elgato Virtual Audio)" --cinema-fps 60
```

The same settings also read from environment variables (`REMOTE_PIN`,
`STREAM_WIDTH`, `JPEG_QUALITY`, `FRAME_DELAY`, `CINEMA_AUDIO`), the command
line winning over both.

## The desktop application

Everything above describes the server. There is also a small Windows
application in `shell\` that launches it, shows what it is doing and keeps the
settings — because a pinned taskbar icon passes no command-line arguments, and
without it the PIN, the stream width and the audio device were unreachable
outside a terminal.

```powershell
.\build.ps1
```

That builds both halves: `dist\remote-pc-server.exe`, the Python server carried
whole by PyInstaller, and `shell\bin\Release\net10.0-windows10.0.19041.0\Remote
PC.exe`, the interface. The interface is the one to pin: start it once,
right-click its taskbar button, **Pin to taskbar**. Pass `-ServerOnly` or
`-ShellOnly` to rebuild just one side.

The window opens on the address as a **QR code**, with the PIN beside it. The
phone reads the code from the camera rather than being fed an IP by hand, which
matters because DHCP changes that IP without warning. A start/stop button and
the state of the machine — screens found, stream settings, capture backend,
audio device — sit under it.

Closing the window does not stop the server: it folds into the notification
area and keeps serving, which is the point when the phone is being used from
another room. **Quit** in the tray menu is what really stops it. Should the
interface be killed outright, the server goes with it: the two are bound by a
Windows job object, so no invisible process is left offering keyboard and
mouse to the network.

### The log

The panel at the bottom is collapsed by default and holds only events that mean
something — a phone connecting, a PIN refused, ffmpeg starting or failing, an
exception. **Show all** adds the HTTP requests behind them.

That split exists because Werkzeug logs one line per request, and the focus
poll alone produces one every second: a real error is invisible in that flood.
The server tags its own events (see `event()` in `app.py`) and the interface
hides everything untagged. While the panel is collapsed, a counter marks
warnings and errors you have not looked at.

### Files

One folder is shared in both directions, `%USERPROFILE%\Downloads\Remote PC` by
default and settable with `--files-dir`. What the phone sends lands there, and
everything in it is offered back to the phone. One place to remember, one folder
to open.

On the phone, the **Files** button lists what is on the PC — tap a name to save
it — and **Send from phone** opens the iOS picker, so photos, videos and
anything in Files go across with a progress figure while they upload. On the
desktop side, the **Files** page lists the same folder with sizes and dates,
opens it in Explorer, and accepts files dropped anywhere on the page: copying
into the folder *is* sending to the phone, so there is no separate action to
learn.

Names arriving from the phone are stripped to something that cannot escape the
folder, and a name already taken gets a rank rather than overwriting — two
photos taken in a row often carry the same name, and silently losing the first
would be a poor surprise. Dropped folders are skipped rather than copied
recursively: dropping a directory tree onto a phone transfer is far more likely
to be a slip than an intention.

### Settings

PIN, port, stream width, JPEG quality, frame rate, starting screen, cinema
bitrate and frame rate, and the audio device. They are written to
`%LOCALAPPDATA%\remote-pc-control\shell.json` and passed to the server as
arguments at each start, so Python stays the single owner of its defaults.
Saving while the server runs changes nothing until it restarts, and the window
says so.

**Measure** next to the audio device runs the same two-second measurement as
`--list-audio` and shows the level beside each name. Worth using rather than
guessing: a virtual mix with no source assigned opens without error and carries
nothing but digital silence.

## Standalone server

The server alone can still be built and run without the interface:

```powershell
.\build.ps1 -ServerOnly
```

It produces `dist\remote-pc-server.exe`, about 25 MB, carrying Python and every
dependency. Run from a terminal it prints the PIN and the address, and
command-line flags work on it as on the source (`& ".\dist\remote-pc-server.exe"
--pin 4821`).

Restarting it does not log the phone out. The cookie signing key is kept in
`%LOCALAPPDATA%\remote-pc-control\secret.key` rather than drawn at each start,
so a session lasts thirty days across as many restarts as you like. Delete that
file to invalidate every phone at once.

ffmpeg is *not* bundled — cinema mode and sound still expect it in the `PATH`,
same as when running from source.

Being unsigned, both executables may draw a SmartScreen warning on first launch
(**More info → Run anyway**), and an antivirus may take an interest in them:
they are fresh unsigned binaries that inject keyboard and mouse input.

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

### Cinema mode

If you only want to watch — no clicking, no typing — the **Cinema** button
serves the screen as a real HLS video stream instead of an image stream. A
`<video>` element *can* take the whole screen on iPhone, status bar included,
so this gives the same full screen as YouTube.

It needs `ffmpeg` in your `PATH`. Hardware encoding is used when available
(NVIDIA, AMD or Intel), falling back to libx264.

The trade-off is latency: three to five seconds, the time for segments to be
written and buffered. That rules out interaction, which is exactly why this
mode drops it. ffmpeg only runs while the phone is asking for segments and
stops on its own about 25 seconds after you leave.

Sound comes from a virtual audio device: Elgato Virtual Audio, Stereo Mix,
VB-Cable and the like. A microphone is never picked by default - broadcasting
the room by accident would be a poor surprise.

Pick the right one by measuring rather than guessing:

```powershell
python app.py --list-audio
```

It opens each device for two seconds and reports its level, so play something
while it runs. This matters more than it sounds: a virtual mix with no source
assigned to it opens without error and carries nothing but digital silence.
Listing device names alone will not tell you which is which. Then pass the one
with signal to `--audio`, or `none` to go silent.

Capture uses the Desktop Duplication API through ffmpeg's `ddagrab` when
available, falling back to `gdigrab`. The difference is not subtle: over five
seconds at 30 fps, gdigrab delivered 143 frames at an uneven rate while
ddagrab delivered exactly 150 at a constant 30. Variable frame rate inside HLS
is what makes the picture stutter.

To check what the focus detection sees in your own applications, run it on its
own and click around:

```powershell
python focus_detect.py
```

It prints a line whenever the focused element changes, telling you whether it is
considered a text field and which mechanism answered.

## Reaching it from outside the house

Do not forward a port to this. Flask's development server, a PIN in clear over
HTTP, and full keyboard and mouse control of the machine is the worst
combination to publish. Nothing here is written to survive the open internet.

The way to use it from elsewhere is not to expose it at all, but to bring the
phone onto a private network with the PC. [Tailscale](https://tailscale.com)
does that: both devices join an encrypted WireGuard mesh, only devices signed
into your account can see each other, and nothing is reachable publicly. No port
is opened on the router.

Install it on the PC and on the phone, sign into the same account on both, and
that is the whole setup. The server needs no change: it already listens on every
interface, so it answers on the Tailscale one as soon as that exists. The phone
simply uses the tailnet address instead of the `192.168.x` one. HTTP in clear
stops being a concern on that path, since WireGuard already encrypts everything
between the two devices.

The desktop application detects it and shows a second **Remote access** card
with its own QR code, preferring the MagicDNS name (`machine.tail1234.ts.net`)
over the address, since the name does not change. The card stays hidden when no
private network is found, rather than promising something that is not there.

## Security

Read this before leaving it running.

Anyone who gets past the PIN has **full mouse and keyboard control** of the
machine. The PIN is therefore the whole of the security, and it is worth
knowing what it is worth.

Attempts are rate-limited: five failures from one address, then a lockout that
starts at thirty seconds and doubles on each repeat, up to fifteen minutes. A
correct PIN is refused during a lockout too, otherwise the limit would be
trivial to walk around. A successful sign-in clears the counter, so a mistyped
PIN costs you nothing once you get it right.

That matters more than it sounds. Measured on the local network before the
limit existed, `/login` answered about 430 attempts a second: a four-digit PIN,
ten thousand combinations, fell in twelve seconds on average. With the lockout
the same exhaustive search takes about three weeks. At six digits it is out of
reach entirely.

There is no default PIN any more. The desktop application asks for one on its
first launch, before anything listens on the network, and refuses to go on
without at least eight digits — a hundred million possibilities, which against
that lockout is centuries of guessing. It offers to generate one, and you read
it off the home page next to the QR code whenever you need it, so there is
nothing to memorise.

Asking on the PC rather than from the phone is deliberate. Whoever is at this
keyboard already has the machine; letting the first phone that connects choose
the PIN would hand it to whoever reached the port first.

Run from the command line without `--pin` or `REMOTE_PIN`, the server draws a
random eight-digit PIN and prints it at startup. The constant that used to live
in this file protected nobody who never changed it.

The comparison uses `secrets.compare_digest` rather than `==`, so the response
time carries no information about how many characters were right.

This remains a tool for a network you control. The server is Flask's
development server, which is not written for exposure, and the rate limit
protects the PIN rather than the rest of the surface. Moving the mouse into a
screen corner triggers pyautogui's failsafe and stops input immediately.