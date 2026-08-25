import ctypes
import io
import os
import secrets
import socket
import time

import mss
import pyautogui

import focus_detect
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session
from PIL import Image

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

PIN = os.environ.get("REMOTE_PIN") or "1312"
STREAM_WIDTH = int(os.environ.get("STREAM_WIDTH", 1600))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", 90))
FRAME_DELAY = float(os.environ.get("FRAME_DELAY", 1 / 20))  # ~20 fps cible

pyautogui.FAILSAFE = True  # jette la souris dans un coin de l'ecran pour tout stopper

with mss.mss() as _sct:
    MONITORS = _sct.monitors  # index 0 = tous les ecrans combines, 1..N = ecrans individuels
MONITOR_COUNT = len(MONITORS) - 1
current_monitor = {"idx": 1}

LOGIN_PAGE = """
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connexion</title></head><body style="font-family:sans-serif;text-align:center;margin-top:40vh;background:#111;color:#eee">
<form method="post">
<h2>Code PIN</h2>
<input name="pin" type="password" inputmode="numeric" autofocus style="font-size:1.5em;text-align:center;width:8em">
<br><br><button style="font-size:1.2em;padding:8px 20px">Se connecter</button>
</form>
{% if error %}<p style="color:#f66">Code incorrect</p>{% endif %}
</body></html>
"""

MAIN_PAGE = """
<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<title>Remote PC</title>
<style>
  html,body{margin:0;height:100vh;height:100dvh;background:#000;overflow:hidden;font-family:sans-serif;color:#eee}
  #viewport{position:fixed;inset:0;height:100vh;height:100dvh;overflow:hidden;background:#000;touch-action:none}
  #screen{position:absolute;top:0;left:0;width:100%;transform-origin:0 0}
  #handle{position:absolute;right:10px;bottom:10px;width:46px;height:46px;border-radius:50%;
    background:rgba(20,20,20,0.5);color:#fff;border:1px solid rgba(255,255,255,0.3);
    font-size:1.4em;display:flex;align-items:center;justify-content:center;z-index:20}
  #controls{position:absolute;left:0;right:0;bottom:0;z-index:15;
    background:rgba(15,15,15,0.55);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
    padding:8px;display:flex;flex-wrap:wrap;gap:6px;
    transition:opacity .25s ease, transform .25s ease}
  #controls.hidden{opacity:0;transform:translateY(100%);pointer-events:none}
  #controls button{flex:1;min-width:42px;padding:10px 4px;font-size:0.85em;
    background:rgba(255,255,255,0.14);color:#fff;border:1px solid rgba(255,255,255,0.25);
    border-radius:8px}
  #morePanel{display:none;width:100%;flex-wrap:wrap;gap:6px;margin-top:6px}
  #morePanel.shown{display:flex}
  body.cinema #controls, body.cinema #handle{display:none}
  body.cinema #screen{height:100%;object-fit:contain}
  /* Champ invisible mais reellement focalisable : iOS n'ouvre son clavier que
     pour un champ present dans la page. opacity:0 suffit, display:none ou
     visibility:hidden le rendraient infocalisable. */
  #ghost{position:fixed;bottom:0;left:0;width:1px;height:1px;opacity:0;
    border:0;padding:0;font-size:16px;z-index:1}
  /* 16px : en dessous, Safari zoome automatiquement sur le champ focalise. */
  #typeBadge{position:absolute;left:50%;transform:translateX(-50%);bottom:74px;
    z-index:25;padding:8px 14px;border-radius:20px;border:1px solid rgba(255,255,255,0.25);
    background:rgba(20,20,20,0.72);backdrop-filter:blur(8px);color:#fff;font-size:0.9em;
    display:none;box-shadow:0 4px 16px rgba(0,0,0,0.4)}
  #typeBadge.shown{display:block}
  body.typing #typeBadge{background:rgba(30,90,45,0.8)}
</style></head>
<body>
<div id="viewport">
  <img id="screen" src="/stream">
  <div id="controls">
    <button onclick="enterCinema()">Plein ecran</button>
    <button onclick="nextMonitor()" id="monBtn">Ecran</button>
    <button onclick="toggleMore()">Plus &#9662;</button>
    <div id="morePanel">
      <button onclick="key('enter')">Enter</button>
      <button onclick="key('esc')">Esc</button>
      <button onclick="key('tab')">Tab</button>
      <button onclick="key('up')">&uarr;</button>
      <button onclick="key('down')">&darr;</button>
      <button onclick="key('left')">&larr;</button>
      <button onclick="key('right')">&rarr;</button>
      <button onclick="key('backspace')">&larr;Del</button>
      <button onclick="rclick()">Clic droit</button>
    </div>
  </div>
  <button id="handle" onclick="toggleControls()">&#8942;</button>
  <div id="typeBadge" onclick="startTyping()">&#9000; Type</div>
  <input id="ghost" autocomplete="off" autocorrect="off" autocapitalize="off"
         spellcheck="false" enterkeyhint="enter">
</div>
<script>
window.onerror = function(msg, url, line, col, err){
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#c00;color:#fff;padding:10px;font-size:13px;z-index:99999;white-space:pre-wrap;font-family:monospace';
  d.textContent = 'Erreur JS ligne ' + line + ': ' + msg;
  document.body.appendChild(d);
};

const viewport = document.getElementById('viewport');
const screenImg = document.getElementById('screen');
const controls = document.getElementById('controls');
let scale = 1, tx = 0, ty = 0;
const MIN_SCALE = 1, MAX_SCALE = 4;

function applyTransform(){
  const cw = viewport.clientWidth, ch = viewport.clientHeight;
  const bw = screenImg.clientWidth, bh = screenImg.clientHeight;
  const contentW = bw * scale, contentH = bh * scale;
  tx = contentW <= cw ? (cw - contentW) / 2 : Math.min(0, Math.max(cw - contentW, tx));
  ty = contentH <= ch ? (ch - contentH) / 2 : Math.min(0, Math.max(ch - contentH, ty));
  screenImg.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
}


function resetZoom(){ scale = 1; tx = 0; ty = 0; applyTransform(); }

function sendClick(fx, fy, button){
  fetch('/click', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({x:fx, y:fy, button:button || 'left'})});
}

function dist(t1, t2){ return Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY); }
function mid(t1, t2){
  const r = viewport.getBoundingClientRect();
  return {x: (t1.clientX + t2.clientX) / 2 - r.left, y: (t1.clientY + t2.clientY) / 2 - r.top};
}

let pinchStartDist = 0, pinchStartScale = 1;
let lastTap = {t: 0, x: 0, y: 0};
let dragStart = null, dragStartTxTy = null, moved = false, touchStartTime = 0;

function isControlTouch(touch){
  /* Tout ce qui est liste ici echappe au tactile de l'ecran distant. Le badge
     en fait partie : sans cela, le tap dessus etait pris pour un tap sur
     l'ecran, le preventDefault du touchend supprimait le clic de synthese -
     donc startTyping() n'etait jamais appele - et le clic partait au PC, ce qui
     sortait justement du champ texte ou l'on voulait ecrire. */
  return !!(touch && touch.target && touch.target.closest &&
            touch.target.closest('#controls, #handle, #typeBadge, #ghost'));
}

viewport.addEventListener('touchstart', e => {
  if (document.body.classList.contains('cinema')) return;
  if (isControlTouch(e.touches[0])) { dragStart = null; return; }
  if (e.touches.length === 2) {
    pinchStartDist = dist(e.touches[0], e.touches[1]);
    pinchStartScale = scale;
    dragStart = null;
  } else if (e.touches.length === 1) {
    dragStart = {x: e.touches[0].clientX, y: e.touches[0].clientY};
    dragStartTxTy = {x: tx, y: ty};
    moved = false;
    touchStartTime = Date.now();
  }
}, {passive: true});

viewport.addEventListener('touchmove', e => {
  if (document.body.classList.contains('cinema')) return;
  if (isControlTouch(e.touches[0])) return;
  e.preventDefault();
  if (e.touches.length === 2) {
    const d = dist(e.touches[0], e.touches[1]);
    const m = mid(e.touches[0], e.touches[1]);
    const newScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, pinchStartScale * (d / pinchStartDist)));
    const localX = (m.x - tx) / scale, localY = (m.y - ty) / scale;
    scale = newScale;
    tx = m.x - localX * scale;
    ty = m.y - localY * scale;
    applyTransform();
  } else if (e.touches.length === 1 && dragStart) {
    const dx = e.touches[0].clientX - dragStart.x;
    const dy = e.touches[0].clientY - dragStart.y;
    if (Math.hypot(dx, dy) > 8) moved = true;
    if (scale > 1 || moved) {
      tx = dragStartTxTy.x + dx;
      ty = dragStartTxTy.y + dy;
      applyTransform();
    }
  }
}, {passive: false});

viewport.addEventListener('touchend', e => {
  if (document.body.classList.contains('cinema')) { e.preventDefault(); exitCinema(); return; }
  if (isControlTouch(e.changedTouches[0])) return;
  e.preventDefault();
  if (e.touches.length === 0 && dragStart && !moved && Date.now() - touchStartTime < 400) {
    const r = screenImg.getBoundingClientRect();
    const fx = (dragStart.x - r.left) / r.width;
    const fy = (dragStart.y - r.top) / r.height;
    if (fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1) {
      /* Deux tapotements rapproches au meme endroit valent un clic droit.
         Le clic gauche du premier tapotement est envoye tout de suite :
         retarder chaque clic de 300 ms pour guetter un second ajouterait
         ce delai a toutes les interactions, ce qui se sent tout de suite
         sur une commande a distance. */
      const now = Date.now();
      const proche = Math.hypot(dragStart.x - lastTap.x,
                                dragStart.y - lastTap.y) < 40;
      if (now - lastTap.t < 320 && proche) {
        sendClick(fx, fy, 'right');
        lastTap.t = 0;          /* un troisieme tapotement repart de zero */
      } else {
        sendClick(fx, fy, 'left');
        lastTap = {t: now, x: dragStart.x, y: dragStart.y};
        /* Si le PC etait deja dans un champ texte, on ouvre le clavier
           maintenant : on est encore dans le geste, seule fenetre ou iOS
           l'autorise. Attendre la reponse du serveur la refermerait. */
        if (pcTextField) startTyping();
      }
    }
  }
  if (e.touches.length === 0) dragStart = null;
}, {passive: false});

screenImg.addEventListener('click', e => {
  if (document.body.classList.contains('cinema')) { exitCinema(); return; }
  const r = screenImg.getBoundingClientRect();
  sendClick((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
});

function enterCinema(){
  document.body.classList.add('cinema');
  scale = 1; tx = 0; ty = 0;
  screenImg.style.transform = '';
  controls.classList.add('hidden');
  clearTimeout(hideTimer);
}
function exitCinema(){
  document.body.classList.remove('cinema');
  resetZoom();
  showControls();
}

window.addEventListener('resize', applyTransform);
applyTransform();

let hideTimer = null;
function showControls(){
  controls.classList.remove('hidden');
  clearTimeout(hideTimer);
  if (!document.activeElement || document.activeElement.id !== 'ghost') {
    hideTimer = setTimeout(() => controls.classList.add('hidden'), 3000);
  }
}
function toggleControls(){
  if (controls.classList.contains('hidden')) showControls();
  else { controls.classList.add('hidden'); clearTimeout(hideTimer); }
}
controls.addEventListener('touchstart', showControls, {passive: true});
controls.addEventListener('click', showControls);
showControls();

function toggleMore(){
  document.getElementById('morePanel').classList.toggle('shown');
  showControls();
}

function rclick(){ sendClick(0.5, 0.5, 'right'); }
function key(name){
  fetch('/key', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({key:name})});
}
function nextMonitor(){
  fetch('/monitor/next', {method:'POST'}).then(r => r.json()).then(d => {
    document.getElementById('monBtn').textContent = 'Ecran ' + d.current + '/' + d.total;
    resetZoom();
  });
}
fetch('/monitors').then(r => r.json()).then(d => {
  document.getElementById('monBtn').textContent = 'Ecran ' + d.current + '/' + d.total;
});

/* ================= clavier natif =================
   Le champ #ghost sert d'appat a clavier : iOS n'ouvre le sien que pour un
   champ focalise, et seulement si focus() part d'un vrai geste utilisateur.
   Un minuteur qui detecte "le PC est dans un champ texte" ne peut donc pas
   faire surgir le clavier tout seul - d'ou le badge sur lequel on appuie.

   Le champ garde deux espaces insecables en permanence. Sans cette matiere a
   supprimer, un champ vide n'emet aucun evenement de suppression et la touche
   retour arriere du telephone ne remonterait jamais. */
const ghost = document.getElementById('ghost');
const badge = document.getElementById('typeBadge');
const SENTINEL = '  ';
let pcTextField = false, typing = false;

function resetGhost(){
  ghost.value = SENTINEL;
  try { ghost.setSelectionRange(SENTINEL.length, SENTINEL.length); } catch(e){}
}

function startTyping(){
  resetGhost();
  ghost.focus();
}

function sendChars(text){
  if (!text) return;
  fetch('/type', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text: text})});
}

ghost.addEventListener('focus', () => {
  typing = true; document.body.classList.add('typing'); updateBadge();
});
ghost.addEventListener('blur', () => {
  typing = false; document.body.classList.remove('typing'); updateBadge();
});

/* La suppression est lue ici, l'insertion dans 'input' : traiter les deux au
   meme endroit ferait taper chaque caractere en double. */
ghost.addEventListener('beforeinput', e => {
  if (e.inputType === 'deleteContentBackward') key('backspace');
});

ghost.addEventListener('input', () => {
  const v = ghost.value;
  if (v.length > SENTINEL.length) {
    sendChars(v.startsWith(SENTINEL) ? v.slice(SENTINEL.length)
                                     : v);   /* correction auto ou dictee : on envoie la valeur entiere */
  }
  resetGhost();   // suppression : rien a envoyer, beforeinput s'en est charge
});

ghost.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); key('enter'); }
});

function updateBadge(){
  /* Regle unique : hors d'un champ de saisie, aucun badge. Tester d'abord
     l'etat du clavier le laissait affiche quand le PC quittait le champ
     alors que le clavier du telephone etait encore ouvert. */
  if (!pcTextField) { badge.classList.remove('shown'); return; }
  badge.textContent = typing ? '⌨' : '⌨ Type';
  badge.classList.add('shown');
}

function pollFocus(){
  if (document.hidden) return;
  fetch('/focus').then(r => r.json()).then(d => {
    pcTextField = !!d.active;
    updateBadge();
  }).catch(() => {});
}
setInterval(pollFocus, 900);
pollFocus();
</script>
</body></html>
"""


def logged_in():
    return session.get("ok") is True


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("pin") == PIN:
            session["ok"] = True
            return redirect("/")
        return render_template_string(LOGIN_PAGE, error=True)
    return render_template_string(LOGIN_PAGE, error=False)


@app.route("/")
def index():
    if not logged_in():
        return redirect("/login")
    return render_template_string(MAIN_PAGE)


def gen_frames():
    with mss.mss() as sct:
        while True:
            t0 = time.time()
            monitor = MONITORS[current_monitor["idx"]]
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            if img.width > STREAM_WIDTH:
                ratio = STREAM_WIDTH / img.width
                img = img.resize((STREAM_WIDTH, int(img.height * ratio)), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY)
            frame = buf.getvalue()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            elapsed = time.time() - t0
            if elapsed < FRAME_DELAY:
                time.sleep(FRAME_DELAY - elapsed)


@app.route("/stream")
def stream():
    if not logged_in():
        return redirect("/login")
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/monitors")
def monitors_info():
    if not logged_in():
        return jsonify(ok=False), 401
    return jsonify(total=MONITOR_COUNT, current=current_monitor["idx"])


@app.route("/monitor/next", methods=["POST"])
def next_monitor():
    if not logged_in():
        return jsonify(ok=False), 401
    current_monitor["idx"] = current_monitor["idx"] % MONITOR_COUNT + 1
    return jsonify(ok=True, current=current_monitor["idx"], total=MONITOR_COUNT)


@app.route("/click", methods=["POST"])
def click():
    if not logged_in():
        return jsonify(ok=False), 401
    data = request.get_json()
    mon = MONITORS[current_monitor["idx"]]
    if not data.get("current"):
        x = mon["left"] + int(data["x"] * mon["width"])
        y = mon["top"] + int(data["y"] * mon["height"])
        pyautogui.moveTo(x, y)
    pyautogui.click(button=data.get("button", "left"))
    return jsonify(ok=True)


KEY_MAP = {
    "enter": "enter", "esc": "esc", "tab": "tab",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "backspace": "backspace",
}


@app.route("/key", methods=["POST"])
def key_press():
    if not logged_in():
        return jsonify(ok=False), 401
    name = request.get_json().get("key")
    mapped = KEY_MAP.get(name)
    if mapped:
        pyautogui.press(mapped)
    return jsonify(ok=True)


# ======================
# Saisie de texte
# ======================
# pyautogui.write ne sait taper que l'ASCII : il traduit chaque caractere en
# code de touche, donc les accents, les hangeul ou une apostrophe typographique
# passent a la trappe. SendInput avec KEYEVENTF_UNICODE injecte directement le
# point de code, sans se soucier de la disposition du clavier.
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):
    """Jamais utilisee, mais indispensable a la taille de la structure.

    SendInput compare le cbSize recu a sa propre definition de INPUT et rejette
    l appel au moindre ecart : il renvoie 0 et rien n est tape. L union de INPUT
    contient aussi MOUSEINPUT, plus grande que KEYBDINPUT ; l omettre donnait 32
    octets la ou Windows en attend 40.
    """
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def type_unicode(text):
    """Tape le texte tel quel, quel que soit l'alphabet."""
    events = []
    for char in text:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            item = _INPUT()
            item.type = 1  # INPUT_KEYBOARD
            item.ki = _KEYBDINPUT(0, ord(char), flags, 0, None)
            events.append(item)
    if not events:
        return
    array = (_INPUT * len(events))(*events)
    sent = ctypes.windll.user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))
    if sent != len(events):
        # SendInput echoue en silence : il renvoie un compte plus faible sans
        # rien lever. C'est ainsi qu'une structure mal dimensionnee a pu ne
        # jamais rien taper sans qu'aucune erreur n'apparaisse.
        print("SendInput: %d/%d evenements injectes (code %d)"
              % (sent, len(events), ctypes.windll.kernel32.GetLastError()))
    return sent


@app.route("/type", methods=["POST"])
def type_text():
    if not logged_in():
        return jsonify(ok=False), 401
    text = request.get_json().get("text", "")
    if text:
        type_unicode(text)
    return jsonify(ok=True)


@app.route("/focus")
def focus_state():
    """Le curseur du PC est-il dans une zone de saisie ?

    Le telephone interroge cette route en boucle pour savoir s'il doit proposer
    son clavier. Voir focus_detect.py pour les deux mecanismes de detection.
    """
    if not logged_in():
        return jsonify(ok=False), 401
    return jsonify(focus_detect.text_field_focused())


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    print("=" * 50)
    print(f"PIN de connexion : {PIN}")
    print(f"Depuis ton telephone (meme Wi-Fi) va sur : http://{local_ip()}:5000")
    print(f"Ecrans detectes : {MONITOR_COUNT}")
    print(f"Qualite stream : largeur={STREAM_WIDTH}px qualite={JPEG_QUALITY} delai={FRAME_DELAY:.3f}s (~{1/FRAME_DELAY:.0f} fps cible)")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, threaded=True)
