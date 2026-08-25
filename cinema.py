# -*- coding: utf-8 -*-
"""Mode cinema : l ecran diffuse en HLS, pour un vrai plein ecran sur iPhone.

Le flux MJPEG de la vue interactive est affiche dans une balise <img>, et sur
iPhone une image ne peut pas passer en plein ecran : requestFullscreen()
n existe pas sur Safari iPhone, seul un element <video> obtient l ecran entier.
C est ainsi que YouTube masque la barre d etat.

On produit donc, a la demande, un vrai flux video que le lecteur natif accepte :
ffmpeg capture l ecran et ecrit un HLS que Safari lit sans bibliotheque. Le prix
a payer est la latence - de l ordre de trois a cinq secondes, le temps que
plusieurs segments soient ecrits puis mis en tampon - ce qui exclut toute
interaction, mais ne gene pas pour regarder.

ffmpeg tourne seulement pendant la lecture : un surveillant l arrete des que le
telephone cesse de reclamer des segments.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time

SEGMENT_SECONDS = 1
PLAYLIST = "stream.m3u8"

# Sans nouvelle demande de segment pendant ce delai, on considere que personne
# ne regarde. Encoder l ecran en continu pour rien couterait cher.
IDLE_TIMEOUT = 25.0

_lock = threading.Lock()
_state = {"proc": None, "dir": None, "last_seen": 0.0, "encoder": None}


def _pick_encoder():
    """Encodeur materiel si la machine en a un, sinon le logiciel.

    Encoder l ecran en continu sur le processeur pendant qu on joue serait le
    meilleur moyen de faire tomber les images de la partie.
    """
    if _state["encoder"]:
        return _state["encoder"]
    try:
        listing = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=15).stdout
    except Exception:
        listing = ""
    for name, args in (
        ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll"]),
        ("h264_amf", ["-c:v", "h264_amf", "-usage", "lowlatency"]),
        ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "veryfast"]),
    ):
        if name in listing:
            _state["encoder"] = args
            return args
    _state["encoder"] = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency"]
    return _state["encoder"]


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def is_running():
    proc = _state["proc"]
    return proc is not None and proc.poll() is None


def directory():
    return _state["dir"]


def touch():
    """Signale qu un segment vient d etre reclame."""
    _state["last_seen"] = time.time()


def start(monitor, bitrate="6M", fps=30):
    """Demarre la capture d un ecran. monitor vient de mss : left/top/width/height."""
    with _lock:
        if is_running():
            touch()
            return _state["dir"]

        out_dir = tempfile.mkdtemp(prefix="remote-hls-")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "gdigrab", "-framerate", str(fps),
            "-offset_x", str(int(monitor["left"])),
            "-offset_y", str(int(monitor["top"])),
            "-video_size", "%dx%d" % (int(monitor["width"]), int(monitor["height"])),
            "-i", "desktop",
        ]
        cmd += _pick_encoder()
        cmd += [
            "-pix_fmt", "yuv420p",
            # Une image cle par segment : sans cela un segment peut commencer
            # sans reference et le lecteur affiche un ecran gris.
            "-g", str(fps * SEGMENT_SECONDS), "-sc_threshold", "0",
            "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
            "-f", "hls",
            "-hls_time", str(SEGMENT_SECONDS),
            "-hls_list_size", "4",
            # delete_segments : le dossier ne grossit pas indefiniment.
            # omit_endlist : le lecteur sait que le direct continue.
            "-hls_flags", "delete_segments+omit_endlist",
            os.path.join(out_dir, PLAYLIST),
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        _state.update({"proc": proc, "dir": out_dir, "last_seen": time.time()})

        threading.Thread(target=_watchdog, daemon=True).start()
        return out_dir


def wait_for_playlist(timeout=12.0):
    """Attend que ffmpeg ait ecrit de quoi commencer la lecture.

    Renvoyer l adresse trop tot ferait echouer le lecteur sur un 404, et Safari
    n y revient pas de lui-meme.
    """
    out_dir = _state["dir"]
    if not out_dir:
        return False
    playlist = os.path.join(out_dir, PLAYLIST)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(playlist):
            try:
                with open(playlist, encoding="utf-8") as f:
                    if f.read().count(".ts") >= 2:
                        return True
            except OSError:
                pass
        if not is_running():
            return False
        time.sleep(0.25)
    return False


def stop():
    with _lock:
        proc = _state["proc"]
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        out_dir = _state["dir"]
        _state.update({"proc": None, "dir": None, "last_seen": 0.0})
        if out_dir:
            shutil.rmtree(out_dir, ignore_errors=True)


def _watchdog():
    while True:
        time.sleep(2.0)
        if not is_running():
            return
        if time.time() - _state["last_seen"] > IDLE_TIMEOUT:
            stop()
            return
