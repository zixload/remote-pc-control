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

# Peripheriques de capture audio, par ordre de preference. Les mix virtuels
# portent ce que joue le PC ; un micro porterait la piece. On ne retient donc
# jamais un micro par defaut : diffuser sans le vouloir le son de la piece
# serait une mauvaise surprise. CINEMA_AUDIO impose un choix, "none" coupe.
AUDIO_PREFERENCES = (
    # Personal Mix avant Stream Mix : mesure au niveau sonore, Stream Mix et
    # Chat Mix sortent du silence numerique tant que Wave Link ne leur affecte
    # aucune source, alors que Personal Mix porte ce que la machine joue.
    "Personal Mix (Elgato Virtual Audio)",
    "Stream Mix (Elgato Virtual Audio)",
    "Stereo Mix",
    "What U Hear",
    "Virtual Audio",
    "VB-Audio",
    "CABLE Output",
)


def list_audio_devices():
    """Noms des peripheriques audio vus par DirectShow."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow",
             "-i", "dummy"],
            capture_output=True, text=True, timeout=20).stderr
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        if "(audio)" in line:
            first = line.find(chr(34))
            last = line.find(chr(34), first + 1)
            if first >= 0 and last > first:
                names.append(line[first + 1:last])
    return names


# Impose depuis la ligne de commande, prioritaire sur tout le reste.
FORCED_AUDIO = None


def set_audio_device(name):
    """name vide ou "none" coupe le son."""
    global FORCED_AUDIO
    FORCED_AUDIO = name


def measure_device(name, seconds=2):
    """Niveau moyen en dB, ou None si la mesure echoue.

    Un peripherique peut exister, s ouvrir sans erreur et ne porter que du
    silence numerique : c est le cas des mix virtuels auxquels aucune source
    n est affectee. Les lister ne suffit donc pas, il faut les ecouter.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "dshow", "-i", "audio=" + name,
             "-t", str(seconds), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=seconds + 20).stderr
    except Exception:
        return None
    for line in out.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except ValueError:
                return None
    return None


def pick_audio_device():
    forced = (FORCED_AUDIO if FORCED_AUDIO is not None
              else os.environ.get("CINEMA_AUDIO", "")).strip()
    if forced:
        return None if forced.lower() in ("none", "off", "0") else forced
    found = list_audio_devices()
    for wanted in AUDIO_PREFERENCES:
        for name in found:
            if wanted.lower() in name.lower():
                return name
    return None


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


def _has_ddagrab():
    """Desktop Duplication est-il disponible comme filtre ffmpeg ?

    gdigrab recopie l ecran par le processeur et n arrive pas a tenir la
    cadence : sur un banc a 30 images par seconde il en rendait 143 en cinq
    secondes au lieu de 150, avec une cadence irreguliere. Un debit variable
    dans du HLS donne une image saccadee. ddagrab passe par le GPU et sort
    exactement 150 images a 30/1 constant.
    """
    if "dda" in _state:
        return _state["dda"]
    try:
        listing = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=15).stdout
    except Exception:
        listing = ""
    _state["dda"] = "ddagrab" in listing
    return _state["dda"]


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


def start(monitor, index=1, bitrate="6M", fps=30):
    """Demarre la capture. monitor vient de mss, index est son rang (1..N)."""
    with _lock:
        if is_running():
            touch()
            return _state["dir"]

        out_dir = tempfile.mkdtemp(prefix="remote-hls-")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

        if _has_ddagrab():
            # ddagrab designe l ecran par son rang sur l adaptateur, en partant
            # de zero, la ou mss reserve l indice 0 a la vue combinee.
            cmd += [
                "-init_hw_device", "d3d11va",
                "-filter_complex",
                "ddagrab=output_idx=%d:framerate=%d[v]" % (max(0, index - 1), fps),
            ]
            video_map = ["-map", "[v]"]
            audio_input_index = 0   # le filtre n est pas une entree
        else:
            cmd += [
                "-f", "gdigrab", "-framerate", str(fps),
                "-offset_x", str(int(monitor["left"])),
                "-offset_y", str(int(monitor["top"])),
                "-video_size", "%dx%d" % (int(monitor["width"]), int(monitor["height"])),
                "-i", "desktop",
            ]
            video_map = ["-map", "0:v"]
            audio_input_index = 1

        device = pick_audio_device()
        if device:
            cmd += ["-f", "dshow", "-i", "audio=" + device]

        cmd += video_map
        if device:
            cmd += ["-map", "%d:a" % audio_input_index]

        cmd += _pick_encoder()
        cmd += [
            # Une image cle par segment : sans cela un segment peut commencer
            # sans reference et le lecteur affiche un ecran gris.
            "-g", str(fps * SEGMENT_SECONDS), "-sc_threshold", "0",
            "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
        ]
        if device:
            # aresample=async : la carte son et la capture d ecran ont des
            # horloges distinctes, et sans reechantillonnage le son derive
            # lentement par rapport a l image.
            cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                    "-af", "aresample=async=1:first_pts=0"]
        cmd += [
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
