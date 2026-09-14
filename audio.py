"""Son du PC en direct, hors de HLS.

Le bouton Son de la vue interactive lisait le meme flux HLS que le mode
cinema. HLS decoupe en segments et le lecteur en met plusieurs en tampon avant
de commencer : trois a cinq secondes de retard. Ce n est pas genant quand on
regarde une video, puisque l image subit le meme retard et reste synchrone avec
le son - mais dans la vue interactive l image arrive en MJPEG, donc en temps
reel, et le decalage entre les deux devient flagrant.

Ici ffmpeg ne produit ni conteneur ni segments : du PCM brut sur sa sortie
standard, relaye tel quel dans une WebSocket. Le navigateur le programme dans
l API Web Audio au fur et a mesure. Il n y a plus rien a mettre en tampon a
part la marge que l on choisit soi-meme, de l ordre de la centaine de
millisecondes.

Le prix est le debit : 48 kHz stereo en 16 bits font 1,5 Mbit/s, la ou l AAC du
flux HLS tenait dans 128 kbit/s. Sur un reseau local c est sans consequence, et
c est de toute facon moins que la video HLS que la vue interactive telechargeait
jusqu ici sans jamais l afficher.
"""

import queue
import subprocess
import threading

import cinema

RATE = 48000
CHANNELS = 2

# 4096 octets = 1024 trames a 48 kHz, soit 21 ms. Assez gros pour ne pas
# reveiller le lecteur en permanence, assez petit pour que la marge de
# programmation reste courte.
CHUNK = 4096

_lock = threading.Lock()
_proc = None
_readers = set()


def _spawn(device):
    """ffmpeg en PCM brut sur stdout, sans conteneur.

    audio_buffer_size est en millisecondes du cote DirectShow : la valeur par
    defaut met a elle seule plusieurs centaines de millisecondes dans la
    balance, ce qui ruinerait l interet de tout le reste.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "dshow", "-audio_buffer_size", "50", "-i", "audio=" + device,
        "-ac", str(CHANNELS), "-ar", str(RATE),
        "-f", "s16le", "-",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)


def _pump(proc):
    """Lit ffmpeg et distribue a chaque auditeur."""
    while True:
        data = proc.stdout.read(CHUNK)
        if not data:
            break
        with _lock:
            listeners = list(_readers)
        for q in listeners:
            try:
                q.put_nowait(data)
            except queue.Full:
                # Auditeur en retard : on jette plutot que d attendre. Faire
                # patienter la lecture ferait grossir le retard pour tout le
                # monde, ce qui est exactement le defaut qu on corrige ici.
                pass


def available():
    return cinema.ffmpeg_available() and bool(cinema.pick_audio_device())


def subscribe():
    """Ouvre une file pour un auditeur, en demarrant ffmpeg au premier."""
    global _proc
    device = cinema.pick_audio_device()
    if not device or not cinema.ffmpeg_available():
        return None

    # Une file bornee : si le telephone decroche, la memoire ne suit pas le
    # retard. Une centaine de morceaux font environ deux secondes.
    q = queue.Queue(maxsize=100)
    with _lock:
        _readers.add(q)
        if _proc is None or _proc.poll() is not None:
            _proc = _spawn(device)
            threading.Thread(target=_pump, args=(_proc,), daemon=True).start()
    return q


def unsubscribe(q):
    """Ferme un auditeur, en arretant ffmpeg quand il n y en a plus."""
    global _proc
    with _lock:
        _readers.discard(q)
        if _readers or _proc is None:
            return
        proc = _proc
        _proc = None
    try:
        proc.kill()
    except OSError:
        pass
