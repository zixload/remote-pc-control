import ctypes
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta

import mss
import pyautogui

import audio
import cinema
import focus_detect
from flask import (Flask, Response, jsonify, redirect, render_template_string,
                   request, send_from_directory, session)
from flask_sock import Sock
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw

def session_key():
    """Cookie signing key, kept on disk.

    Drawn at random on every start, it invalidated the phone's cookie as soon
    as the server restarted: the page fell back to /login and its /focus poll
    answered 401 in a loop. With a shortcut pinned to the taskbar, that
    restart happens several times a day.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "remote-pc-control", "secret.key")
    try:
        with io.open(path, "rb") as f:
            key = f.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32).encode("ascii")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "wb") as f:
            f.write(key)
    except OSError:
        pass  # in-memory key: the PIN is retyped after a restart, no worse
    return key


def report_exception(exc):
    """An exception in a route used to end up in the Werkzeug traceback
    alone. The interface should show it in red, not have to infer it."""
    event("ERROR", "%s : %s" % (type(exc).__name__, exc), route=request.path)
    return jsonify(ok=False, error=str(exc)), 500


def event(level, message, **facts):
    """Emit a log line the interface knows how to classify.

    Werkzeug writes one line per request, focus polls included, one a second:
    in that stream a real problem goes unnoticed. Events that mean something
    are therefore prefixed, and the interface hides everything that is not.
    Written to stderr, like Werkzeug, so the ordering between the two sources
    is preserved.
    """
    payload = " " + json.dumps(facts, ensure_ascii=False) if facts else ""
    sys.stderr.write("@RPC|%s|%s%s\n" % (level, message, payload))
    sys.stderr.flush()


app = Flask(__name__)
app.secret_key = session_key()
app.permanent_session_lifetime = timedelta(days=30)
app.register_error_handler(Exception, report_exception)
sock = Sock(app)

# Plus de PIN par defaut ecrit dans le depot : celui qui s y trouvait etait
# public, donc il ne protegeait rien chez ceux qui ne le changeaient pas. Sans
# consigne, on en tire un au hasard et on l affiche au demarrage - il faut
# alors le lire pour s en servir, ce qui est precisement le but.
PIN = os.environ.get("REMOTE_PIN") or "%08d" % secrets.randbelow(10 ** 8)
STREAM_WIDTH = int(os.environ.get("STREAM_WIDTH", 1600))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", 90))
FRAME_DELAY = float(os.environ.get("FRAME_DELAY", 1 / 20))  # ~20 fps cible
CINEMA_BITRATE = os.environ.get("CINEMA_BITRATE", "6M")
CINEMA_FPS = int(os.environ.get("CINEMA_FPS", 30))

# Un seul dossier dans les deux sens plutot qu une boite d envoi et une de
# reception : ce qui arrive du telephone y atterrit, et tout ce qui s y trouve
# est proposable au telephone. Une seule chose a retenir, un seul endroit a
# ouvrir.
FILES_DIR = os.environ.get("REMOTE_FILES") or os.path.join(
    os.path.expanduser("~"), "Downloads", "Remote PC")

pyautogui.FAILSAFE = True  # jette la souris dans un coin de l'ecran pour tout stopper

with mss.mss() as _sct:
    MONITORS = _sct.monitors  # index 0 = tous les ecrans combines, 1..N = ecrans individuels
MONITOR_COUNT = len(MONITORS) - 1
current_monitor = {"idx": 1}

LOGIN_PAGE = """
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in</title></head><body style="font-family:sans-serif;text-align:center;margin-top:40vh;background:#111;color:#eee">
<form method="post">
<h2>PIN code</h2>
<input name="pin" type="password" inputmode="numeric" autofocus style="font-size:1.5em;text-align:center;width:8em">
<br><br><button style="font-size:1.2em;padding:8px 20px">Sign in</button>
</form>
{% if error %}<p style="color:#f66">Wrong code</p>{% endif %}
{% if wait %}<p style="color:#e8c77b">Too many attempts. Try again in {{ wait }} s.</p>{% endif %}
</body></html>
"""

MAIN_PAGE = """
<!doctype html><html><head>
<!-- viewport-fit=cover : le contenu s etend sous l encoche et sous la barre
     d etat, au lieu de s arreter a leurs marges. -->
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<!-- black-translucent : la barre d etat devient transparente et la page passe
     dessous. iOS n autorise jamais une page web a masquer l heure et la
     batterie, mais au moins rien ne reste en noir opaque au-dessus. -->
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#000000">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon.png">
<title>Remote PC</title>
<style>
  html,body{margin:0;height:100vh;height:100dvh;background:#000;overflow:hidden;font-family:sans-serif;color:#eee}
  #viewport{position:fixed;inset:0;height:100vh;height:100dvh;overflow:hidden;background:#000;touch-action:none}
  /* Sans ceci, un appui long sur l image ouvre la feuille iOS "Enregistrer
     dans Photos / Partager" : le geste de glisser-deposer ne parvenait jamais
     jusqu a la page. */
  #screen{position:absolute;top:0;left:0;width:100%;transform-origin:0 0;
    -webkit-touch-callout:none;-webkit-user-select:none;user-select:none;
    pointer-events:auto}
  #viewport{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
  /* Chaque commande est une pastille de verre, sur le modele du badge de
     saisie : meme fond, meme flou, meme rayon. Plus de barre pleine largeur -
     les pastilles flottent au-dessus de l'image, centrees en bas. */
  #handle{position:absolute;right:12px;
    bottom:calc(12px + env(safe-area-inset-bottom,0px));width:44px;height:44px;
    border-radius:22px;background:rgba(20,20,20,0.72);color:#fff;
    backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
    border:1px solid rgba(255,255,255,0.25);box-shadow:0 4px 16px rgba(0,0,0,0.4);
    font-size:1.3em;display:flex;align-items:center;justify-content:center;z-index:20}
  #controls{position:absolute;left:0;right:0;z-index:15;
    bottom:calc(12px + env(safe-area-inset-bottom,0px));
    /* Marges laterales : la poignee occupe le coin droit, on garde la
       symetrie pour que le centrage reste franc. */
    padding:0 56px;
    display:flex;flex-wrap:wrap;justify-content:center;gap:8px;
    transition:opacity .25s ease, transform .25s ease}
  #controls.hidden{opacity:0;transform:translateY(140%);pointer-events:none}
  #controls button{flex:0 0 auto;padding:10px 16px;font-size:0.85em;color:#fff;
    border-radius:20px;background:rgba(20,20,20,0.72);
    backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
    border:1px solid rgba(255,255,255,0.25);
    box-shadow:0 4px 16px rgba(0,0,0,0.4);
    -webkit-tap-highlight-color:transparent}
  #controls button:active{background:rgba(72,72,78,0.85)}
  /* Touches d'un seul signe : des ronds, plutot que des fleches etirees. */
  #controls button.ico{width:44px;padding:10px 0;text-align:center}
  /* order:-1 fait remonter le tiroir au-dessus de la rangee principale, et
     width:100% le force sur sa propre ligne. */
  #morePanel{order:-1;display:none;width:100%;flex-wrap:wrap;
    justify-content:center;gap:8px}
  #morePanel.shown{display:flex}
  /* Champ invisible mais reellement focalisable : iOS n'ouvre son clavier que
     pour un champ present dans la page. opacity:0 suffit, display:none ou
     visibility:hidden le rendraient infocalisable. */
  #ghost{position:fixed;bottom:0;left:0;width:1px;height:1px;opacity:0;
    border:0;padding:0;font-size:16px;z-index:1}
  /* 16px : en dessous, Safari zoome automatiquement sur le champ focalise. */
  #typeBadge{position:absolute;left:50%;transform:translateX(-50%);top:calc(12px + env(safe-area-inset-top,0px));
    z-index:25;padding:8px 14px;border-radius:20px;border:1px solid rgba(255,255,255,0.25);
    background:rgba(20,20,20,0.72);backdrop-filter:blur(8px);color:#fff;font-size:0.9em;
    display:none;box-shadow:0 4px 16px rgba(0,0,0,0.4)}
  #typeBadge.shown{display:block}
  body.typing #typeBadge{background:rgba(30,90,45,0.8)}
  /* Glisser en cours : la poignee vire au vert, seul repere possible
     puisqu il n y a pas de curseur visible sur le telephone. */
  body.dragging #handle{background:rgba(40,110,60,0.88)}
  /* Fiche d aide : meme verre que les pastilles, centree et au-dessus de
     tout. Elle se referme en tapotant n importe ou dessus. */
  #helpPanel{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
    z-index:30;display:none;max-width:min(420px,86vw);max-height:76dvh;overflow:auto;
    padding:18px 20px;border-radius:20px;border:1px solid rgba(255,255,255,0.25);
    background:rgba(18,18,20,0.9);backdrop-filter:blur(14px);
    -webkit-backdrop-filter:blur(14px);box-shadow:0 8px 40px rgba(0,0,0,0.6);
    font-size:0.9em;line-height:1.5}
  #helpPanel.shown{display:block}
  #helpPanel h3{margin:0 0 12px;font-size:1em;font-weight:600}
  #helpPanel dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:7px 12px}
  #helpPanel dt{color:#fff;white-space:nowrap}
  #helpPanel dd{margin:0;opacity:.62}
  #helpPanel .close{margin-top:14px;text-align:center;opacity:.5;font-size:.85em}
  /* Meme boite que l aide : meme flou, meme rayon, meme largeur. Un seul
     langage visuel pour tout ce qui se pose par-dessus l ecran distant. */
  #filesPanel{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
    z-index:30;display:none;width:min(420px,86vw);max-height:76dvh;overflow:auto;
    padding:18px 20px;border-radius:20px;border:1px solid rgba(255,255,255,0.25);
    background:rgba(18,18,20,0.9);backdrop-filter:blur(14px);
    -webkit-backdrop-filter:blur(14px);box-shadow:0 8px 40px rgba(0,0,0,0.6);
    font-size:0.9em;line-height:1.5}
  #filesPanel.shown{display:block}
  #filesPanel h3{margin:0 0 12px;font-size:1em;font-weight:600}
  #toast{position:absolute;left:50%;top:14%;transform:translateX(-50%);z-index:28;
    padding:9px 18px;border-radius:18px;font-size:.9em;pointer-events:none;
    background:rgba(18,18,20,0.88);border:1px solid rgba(255,255,255,0.22);
    backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
    opacity:0;transition:opacity .18s ease}
  #toast.shown{opacity:1}
  #filesPanel .row{display:flex;gap:8px;margin-bottom:10px}
  #filesPanel .row button{flex:1}
  #upProgress{min-height:1.2em;opacity:.7;font-size:.85em;margin-bottom:6px}
  #fileList{list-style:none;margin:0;padding:0}
  #fileList li{display:flex;align-items:center;gap:10px;padding:7px 0;
    border-top:1px solid rgba(255,255,255,0.1)}
  #fileList a{color:#8ab4f8;text-decoration:none;flex:1;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  #fileList .size{opacity:.5;font-size:.8em;white-space:nowrap}
  #fileList .del{background:none;border:0;color:#e0796a;font-size:1.2em;
    padding:0 4px;line-height:1}
  #fileList .muted{opacity:.5;border-top:0}
</style></head>
<body>
<div id="viewport">
  <img id="screen" src="/stream">
  <div id="controls">
    <button onclick="nextMonitor()" id="monBtn">Screen</button>
    <button onclick="location.href='/cinema'">Cinema</button>
    <button onclick="toggleFiles()">Files</button>
    <button class="ico" onclick="toggleHelp()">i</button>
    <button onclick="toggleMore()">More &#9662;</button>
    <div id="morePanel">
      <button onclick="key('enter')">Enter</button>
      <button onclick="key('esc')">Esc</button>
      <button onclick="key('tab')">Tab</button>
      <button class="ico" onclick="key('up')">&uarr;</button>
      <button class="ico" onclick="key('down')">&darr;</button>
      <button class="ico" onclick="key('left')">&larr;</button>
      <button class="ico" onclick="key('right')">&rarr;</button>
      <button onclick="key('backspace')">&larr;Del</button>
      <button onclick="rclick()">Right click</button>
    </div>
  </div>
  <button id="handle" onclick="toggleControls()">&#8942;</button>
  <div id="helpPanel" onclick="toggleHelp()">
    <h3>How it works</h3>
    <dl>
      <dt>Tap</dt><dd>left click</dd>
      <dt>Two taps</dt><dd>right click, on the same spot</dd>
      <dt>Press and hold, then move</dt><dd>keeps the button down: move a
        window, select text</dd>
      <dt>Two fingers sliding</dt><dd>scroll wheel</dd>
      <dt>Pinch</dt><dd>zoom the view</dd>
      <dt>One finger, zoomed in</dt><dd>pan the view</dd>
      <dt>Push past the edge</dt><dd>slide sideways until the view stops, and
        keep going: the next screen comes in. Works zoomed out too</dd>
      <dt>Keyboard badge</dt><dd>appears when the PC is in a text field;
        tapping it opens the phone keyboard</dd>
      <dt>Cinema</dt><dd>video with sound, real full screen, no interaction and
        a few seconds behind</dd>
      <dt>Sound</dt><dd>the PC audio plays on its own, about a fifth of a
        second behind</dd>
    </dl>
    <div class="close">Tap to close</div>
  </div>
  <div id="filesPanel">
    <h3>Files</h3>
    <div class="row">
      <button onclick="filePick.click()">Send from phone</button>
      <button onclick="loadFiles()">Refresh</button>
    </div>
    <div id="upProgress"></div>
    <ul id="fileList"></ul>
    <div class="close" onclick="toggleFiles()" style="margin-top:14px;
         text-align:center;opacity:.5;font-size:.85em">Tap to close</div>
  </div>
  <input type="file" id="filePick" multiple style="display:none">
  <div id="toast"></div>
  <div id="typeBadge" onclick="startTyping()">&#9000; Type</div>
  <input id="ghost" autocomplete="off" autocorrect="off" autocapitalize="off"
         spellcheck="false" enterkeyhint="enter">
</div>
<script>
window.onerror = function(msg, url, line, col, err){
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#c00;color:#fff;padding:10px;font-size:13px;z-index:99999;white-space:pre-wrap;font-family:monospace';
  d.textContent = 'JS error, line ' + line + ': ' + msg;
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

/* Pousser la vue au-dela du bord fait passer a l ecran voisin, comme si les
   ecrans du PC etaient poses cote a cote. Cela marche aussi sans zoom : la vue
   est alors deja a ses limites, donc tout glissement est du debordement. */
const EDGE_SWITCH = 120;      /* pixels de debordement avant de basculer */
let monitorTotal = 1, edgeSwitched = false;

function maybeSwitchScreen(overscroll){
  if (edgeSwitched || monitorTotal < 2) return;
  if (Math.abs(overscroll) < EDGE_SWITCH) return;
  edgeSwitched = true;        /* une seule bascule par glissement */
  /* Doigt vers la gauche : on pousse l ecran courant hors du cadre par la
     gauche, donc le suivant arrive par la droite. */
  nextMonitor(overscroll < 0 ? 1 : -1);
}

let toastTimer = null;
function toast(text){
  const el = document.getElementById('toast');
  el.textContent = text;
  el.classList.add('shown');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('shown'), 1200);
}

function isControlTouch(touch){
  /* Tout ce qui est liste ici echappe au tactile de l'ecran distant. Le badge
     en fait partie : sans cela, le tap dessus etait pris pour un tap sur
     l'ecran, le preventDefault du touchend supprimait le clic de synthese -
     donc startTyping() n'etait jamais appele - et le clic partait au PC, ce qui
     sortait justement du champ texte ou l'on voulait ecrire. */
  return !!(touch && touch.target && touch.target.closest &&
            touch.target.closest('#controls, #handle, #typeBadge, #ghost, #helpPanel, #filesPanel'));
}

/* ---- gestes ----
   Un doigt : deplacement de la vue, tapotement = clic, appui long = glisser.
   Deux doigts : pincement pour zoomer, ou glissement pour la molette.

   Les deux gestes a deux doigts partagent le meme nombre de doigts, on les
   separe donc au mouvement : un ecart qui change nettement est un pincement,
   un milieu qui se deplace sans que l ecart bouge est un defilement. Le
   premier qui franchit son seuil verrouille le mode jusqu au relachement,
   sinon le geste hesiterait en cours de route. */
const SCROLL_STEP = 34;      /* pixels de glissement par cran de molette */
const LONG_PRESS_MS = 500;
let twoMode = null, twoStart = null, scrollAcc = 0;
let longPress = null, dragMode = false, lastMoveSent = 0;

function midClient(t1, t2){
  return {x: (t1.clientX + t2.clientX) / 2, y: (t1.clientY + t2.clientY) / 2};
}
function frac(cx, cy){
  const r = screenImg.getBoundingClientRect();
  return {x: (cx - r.left) / r.width, y: (cy - r.top) / r.height};
}
function inScreen(f){ return f.x >= 0 && f.x <= 1 && f.y >= 0 && f.y <= 1; }

function sendScroll(amount, f){
  fetch('/scroll', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({amount: amount, x: f.x, y: f.y})});
}
function sendMouse(action, f){
  const body = {action: action};
  if (f) { body.x = f.x; body.y = f.y; }
  fetch('/mouse', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
}
function cancelLongPress(){
  if (longPress) { clearTimeout(longPress); longPress = null; }
}
function endDrag(){
  if (!dragMode) return;
  dragMode = false;
  document.body.classList.remove('dragging');
  sendMouse('up');
}

viewport.addEventListener('touchstart', e => {
  if (isControlTouch(e.touches[0])) { dragStart = null; return; }
  if (e.touches.length === 2) {
    cancelLongPress();
    twoStart = {d: dist(e.touches[0], e.touches[1]), m: midClient(e.touches[0], e.touches[1])};
    twoMode = null;
    scrollAcc = 0;
    pinchStartDist = twoStart.d;
    pinchStartScale = scale;
    dragStart = null;
  } else if (e.touches.length === 1) {
    dragStart = {x: e.touches[0].clientX, y: e.touches[0].clientY};
    dragStartTxTy = {x: tx, y: ty};
    moved = false;
    edgeSwitched = false;
    touchStartTime = Date.now();
    cancelLongPress();
    const f = frac(dragStart.x, dragStart.y);
    if (inScreen(f)) {
      /* Appui long : on enfonce le bouton et on ne le relache qu au lever du
         doigt. C est ce qui permet de trainer une fenetre ou de selectionner
         du texte, impossible avec un clic seul. */
      longPress = setTimeout(() => {
        longPress = null;
        if (moved) return;
        dragMode = true;
        document.body.classList.add('dragging');
        sendMouse('down', f);
      }, LONG_PRESS_MS);
    }
  }
}, {passive: true});

viewport.addEventListener('touchmove', e => {
  if (isControlTouch(e.touches[0])) return;
  e.preventDefault();

  if (e.touches.length === 2 && twoStart) {
    const d = dist(e.touches[0], e.touches[1]);
    const mc = midClient(e.touches[0], e.touches[1]);
    if (twoMode === null) {
      if (Math.abs(d - twoStart.d) > 28) twoMode = 'pinch';
      else if (Math.abs(mc.y - twoStart.m.y) > 18) twoMode = 'scroll';
    }

    if (twoMode === 'scroll') {
      scrollAcc += mc.y - twoStart.m.y;
      twoStart.m = mc;
      const crans = Math.trunc(scrollAcc / SCROLL_STEP);
      if (crans !== 0) {
        scrollAcc -= crans * SCROLL_STEP;
        const f = frac(mc.x, mc.y);
        /* Doigts vers le bas = contenu vers le bas = molette vers le haut,
           comme le defilement naturel d iOS. */
        if (inScreen(f)) sendScroll(crans, f);
      }
      return;
    }
    if (twoMode !== 'pinch') return;

    const m = mid(e.touches[0], e.touches[1]);
    const newScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, pinchStartScale * (d / pinchStartDist)));
    const localX = (m.x - tx) / scale, localY = (m.y - ty) / scale;
    scale = newScale;
    tx = m.x - localX * scale;
    ty = m.y - localY * scale;
    applyTransform();
    return;
  }

  if (e.touches.length === 1 && dragStart) {
    const dx = e.touches[0].clientX - dragStart.x;
    const dy = e.touches[0].clientY - dragStart.y;
    if (Math.hypot(dx, dy) > 8) {
      moved = true;
      if (!dragMode) cancelLongPress();
    }

    if (dragMode) {
      const now = Date.now();
      if (now - lastMoveSent > 60) {     /* on n inonde pas le reseau */
        lastMoveSent = now;
        const f = frac(e.touches[0].clientX, e.touches[0].clientY);
        if (inScreen(f)) sendMouse('move', f);
      }
      return;
    }

    if (scale > 1 || moved) {
      const wantedX = dragStartTxTy.x + dx;
      tx = wantedX;
      ty = dragStartTxTy.y + dy;
      applyTransform();
      /* applyTransform ramene tx dans ses bornes ; ce qu il a refuse est
         exactement le debordement, et c est lui qui fait changer d ecran. */
      maybeSwitchScreen(wantedX - tx);
    }
  }
}, {passive: false});

viewport.addEventListener('touchend', e => {
  if (isControlTouch(e.changedTouches[0])) return;
  e.preventDefault();
  cancelLongPress();

  if (e.touches.length < 2) { twoMode = null; twoStart = null; }

  if (dragMode) {
    endDrag();
    dragStart = null;
    return;
  }

  if (e.touches.length === 0 && dragStart && !moved && Date.now() - touchStartTime < 400) {
    const f = frac(dragStart.x, dragStart.y);
    if (inScreen(f)) {
      /* Deux tapotements rapproches au meme endroit valent un clic droit. Le
         clic gauche du premier part tout de suite : retarder chaque clic de
         300 ms pour guetter un second ajouterait ce delai a toutes les
         interactions, ce qui se sent aussitot sur une commande a distance. */
      const now = Date.now();
      const proche = Math.hypot(dragStart.x - lastTap.x, dragStart.y - lastTap.y) < 40;
      if (now - lastTap.t < 320 && proche) {
        sendClick(f.x, f.y, 'right');
        lastTap.t = 0;          /* un troisieme tapotement repart de zero */
      } else {
        sendClick(f.x, f.y, 'left');
        lastTap = {t: now, x: dragStart.x, y: dragStart.y};
        /* Si le PC etait deja dans un champ texte, on ouvre le clavier
           maintenant : on est encore dans le geste, seule fenetre ou iOS
           l autorise. Attendre la reponse du serveur le refermerait. */
        if (pcTextField) startTyping();
      }
    }
  }
  if (e.touches.length === 0) dragStart = null;
}, {passive: false});

screenImg.addEventListener('click', e => {
  const r = screenImg.getBoundingClientRect();
  sendClick((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
});

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

function toggleHelp(){
  document.getElementById('helpPanel').classList.toggle('shown');
  showControls();
}

/* ================= sound in the interactive view =================
   Raw PCM over a WebSocket, scheduled into Web Audio. See audio.py.

   Sound is always on: there is no button. The one unavoidable constraint is
   iOS, which refuses to start audio - or even resume an AudioContext - outside
   a user gesture. So it arms itself on the first touch of the screen, the same
   touch you make to start controlling the PC, and you never think about it. */
let audioCtx = null, audioSock = null, playAt = 0;

/* Marge de programmation. Trop courte, le moindre hoquet du Wi-Fi se fait
   entendre ; trop longue, on recree le retard qu on vient d enlever. */
const LEAD = 0.12;
const MAX_AHEAD = 0.6;

function playChunk(data){
  const pcm = new Int16Array(data);
  const frames = pcm.length / 2;
  if (!frames || !audioCtx) return;

  /* Le tampon porte sa propre frequence : sur iPhone le contexte tourne
     souvent a 44,1 kHz, et Web Audio reechantillonne tout seul plutot que de
     nous obliger a le faire ici. */
  const buf = audioCtx.createBuffer(2, frames, 48000);
  const left = buf.getChannelData(0), right = buf.getChannelData(1);
  for (let i = 0; i < frames; i++) {
    left[i] = pcm[2 * i] / 32768;
    right[i] = pcm[2 * i + 1] / 32768;
  }

  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(audioCtx.destination);

  const now = audioCtx.currentTime;
  /* En retard : on repart de maintenant, quitte a sauter un peu de son.
     En avance de plus d une demi-seconde : le reseau a rendu une rafale et le
     tampon a enfle, on se recale pour ne pas garder ce retard indefiniment. */
  if (playAt < now + 0.02 || playAt > now + MAX_AHEAD) playAt = now + LEAD;
  src.start(playAt);
  playAt += buf.duration;
}

function openSound(){
  const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
  audioSock = new WebSocket(scheme + location.host + '/audio');
  audioSock.binaryType = 'arraybuffer';
  audioSock.onmessage = e => playChunk(e.data);
  audioSock.onopen = () => { playAt = 0; };
  /* Coupure reseau ou reveil : on oublie la socket, le prochain contact la
     rouvrira via armSound. */
  audioSock.onclose = () => { audioSock = null; };
  audioSock.onerror = () => {};
}

/* Appelee a chaque contact de l ecran : arme le son au premier, le relance
   ensuite s il s est endormi. Doit s executer dans le geste et sans attente
   reseau, sinon iOS considere l autorisation expiree - d ou resume() lance
   sans await. */
function armSound(){
  try {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      /* Sans ceci, iOS traite le Web Audio comme un effet sonore et le coupe
         des que l interrupteur silence du telephone est active - ce que le
         mode HLS video ne subissait pas. 'playback' le declare comme un media,
         qui joue malgre l interrupteur. Safari 16.4+, ignore ailleurs. */
      try { if (navigator.audioSession) navigator.audioSession.type = 'playback'; }
      catch (e) {}
    }
    if (audioCtx.state === 'suspended') audioCtx.resume();
    if (!audioSock) openSound();
  } catch (e) {
    /* Pas de Web Audio sur cet appareil : on n insiste pas, le reste marche. */
  }
}

function stopSound(){
  if (audioSock) { audioSock.onclose = null; audioSock.close(); audioSock = null; }
  playAt = 0;
}

/* Ecran verrouille ou onglet ferme : la WebSocket part avec la page, et
   ffmpeg s arrete cote PC des que le dernier auditeur disparait. */
window.addEventListener('pagehide', stopSound);

/* iOS ne debloque le son que dans un vrai geste, et un simple touchstart sur
   l ecran n y suffit pas toujours la ou un clic de bouton oui - d ou le son
   qui n arrivait qu apres avoir touche un bouton. On arme donc sur le premier
   de plusieurs evenements. Et openSound est lance des le chargement pour que
   ffmpeg soit deja chaud quand le geste debloque la lecture : sinon le son ne
   viendrait que deux secondes plus tard, le temps que la capture demarre. */
['pointerdown', 'touchstart', 'touchend', 'click'].forEach(function(ev){
  document.addEventListener(ev, armSound, {passive: true});
});
openSound();

function rclick(){ sendClick(0.5, 0.5, 'right'); }
function key(name){
  fetch('/key', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({key:name})});
}
function nextMonitor(delta){
  fetch('/monitor/next', {method:'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({delta: delta === undefined ? 1 : delta}),
  }).then(r => r.json()).then(d => {
    monitorTotal = d.total;
    document.getElementById('monBtn').textContent = 'Screen ' + d.current + '/' + d.total;
    resetZoom();
    /* La bascule peut venir d un glissement alors que la barre est masquee :
       sans ce message, l ecran change sans qu on sache pourquoi. */
    toast('Screen ' + d.current + ' / ' + d.total);
  });
}
fetch('/monitors').then(r => r.json()).then(d => {
  monitorTotal = d.total;
  document.getElementById('monBtn').textContent = 'Screen ' + d.current + '/' + d.total;
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

/* Chaine de setTimeout plutot qu'un setInterval : en arriere-plan iOS met
   les intervalles en attente puis vide la file d'un coup au retour, ce qui
   envoyait une vingtaine de sondages dans la meme seconde. Ici le suivant
   n'est arme qu'une fois le precedent termine, donc rien ne s'empile. */
let focusTimer = null;

function scheduleFocusPoll(delay){
  clearTimeout(focusTimer);
  focusTimer = setTimeout(pollFocus, delay === undefined ? 900 : delay);
}

const filePick = document.getElementById('filePick');
filePick.addEventListener('change', () => uploadFiles(filePick.files));

function toggleFiles(){
  const panel = document.getElementById('filesPanel');
  const show = !panel.classList.contains('shown');
  panel.classList.toggle('shown', show);
  if (show) loadFiles();
}

function humanSize(n){
  if (n < 1024) return n + ' B';
  if (n < 1048576) return Math.round(n / 1024) + ' KB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
  return (n / 1073741824).toFixed(2) + ' GB';
}

async function loadFiles(){
  const list = document.getElementById('fileList');
  list.innerHTML = '<li class="muted">Loading...</li>';
  try {
    const r = await fetch('/files');
    if (r.status === 401) { location.href = '/login'; return; }
    const d = await r.json();
    list.innerHTML = '';
    if (!d.files.length) {
      list.innerHTML = '<li class="muted">Nothing here yet.</li>';
      return;
    }
    for (const f of d.files) {
      const li = document.createElement('li');
      const a = document.createElement('a');
      /* download + as_attachment cote serveur : sans les deux, Safari ouvre
         l image dans l onglet au lieu de proposer de l enregistrer. */
      a.href = '/files/get/' + encodeURIComponent(f.name);
      a.setAttribute('download', f.name);
      a.textContent = f.name;
      const size = document.createElement('span');
      size.className = 'size';
      size.textContent = humanSize(f.size);
      const del = document.createElement('button');
      del.className = 'del';
      del.textContent = '×';
      del.onclick = () => removeFile(f.name);
      li.appendChild(a); li.appendChild(size); li.appendChild(del);
      list.appendChild(li);
    }
  } catch (e) {
    list.innerHTML = '<li class="muted">Could not read the folder.</li>';
  }
}

function uploadFiles(files){
  if (!files || !files.length) return;
  const form = new FormData();
  for (const f of files) form.append('file', f);
  const bar = document.getElementById('upProgress');

  /* XMLHttpRequest et pas fetch : fetch ne rapporte pas l avancement de
     l envoi, et une video de plusieurs centaines de megaoctets sans barre de
     progression donne l impression que l application a plante. */
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/files/upload');
  xhr.upload.onprogress = e => {
    if (e.lengthComputable)
      bar.textContent = 'Sending ' + Math.round(e.loaded / e.total * 100) + '%';
  };
  xhr.onload = () => {
    bar.textContent = xhr.status === 200 ? 'Sent.' : 'Upload failed.';
    filePick.value = '';
    loadFiles();
    setTimeout(() => { bar.textContent = ''; }, 4000);
  };
  xhr.onerror = () => { bar.textContent = 'Upload failed.'; };
  bar.textContent = 'Sending 0%';
  xhr.send(form);
}

async function removeFile(name){
  await fetch('/files/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name}),
  });
  loadFiles();
}

function pollFocus(){
  if (document.hidden) { scheduleFocusPoll(); return; }
  fetch('/focus').then(r => {
    if (r.status === 401) {
      /* Session expiree : sans cet arret la page sondait /focus a la seconde
         indefiniment, en pure perte, sans jamais dire de se reconnecter. */
      clearTimeout(focusTimer);
      location.href = '/login';
      return null;
    }
    return r.json();
  }).then(d => {
    if (!d) return;
    pcTextField = !!d.active;
    updateBadge();
    scheduleFocusPoll();
  }).catch(() => scheduleFocusPoll());
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) scheduleFocusPoll(0);
});

pollFocus();
</script>
</body></html>
"""


CINEMA_PAGE = """
<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Cinema</title>
<style>
  html,body{margin:0;height:100dvh;background:#000;color:#eee;font-family:sans-serif;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px}
  video{width:100%;max-height:70dvh;background:#000}
  button{padding:12px 24px;font-size:1em;color:#fff;border-radius:22px;
    background:rgba(20,20,20,0.72);backdrop-filter:blur(8px);
    -webkit-backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,0.25);
    box-shadow:0 4px 16px rgba(0,0,0,0.4)}
  p{opacity:.6;font-size:.85em;text-align:center;margin:0 24px;line-height:1.5}
  a{color:#8ab4f8}
</style></head>
<body>
<video id="v" playsinline webkit-playsinline controls></video>
<button id="go">Start the stream</button>
<p id="msg">No interaction: the screen is sent as video, which is what allows
real iOS full screen. Expect three to five seconds of delay.</p>
<a href="/">Back to the interactive view</a>
<script>
const v = document.getElementById('v'), go = document.getElementById('go'),
      msg = document.getElementById('msg');

go.addEventListener('click', async () => {
  go.disabled = true; msg.textContent = 'Starting the capture...';
  try {
    const r = await fetch('/cinema/start', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { msg.textContent = d.error || 'Failed to start.'; go.disabled = false; return; }
    v.src = '/hls/stream.m3u8';
    v.load();
    await v.play();
    msg.textContent = 'Live. Use the player full-screen button.';
    /* On iPhone only a <video> can take the whole screen, and only from a
       gesture. Try here, and the player's own button stays the fallback if
       the gesture expired while starting. */
    if (v.webkitEnterFullscreen) { try { v.webkitEnterFullscreen(); } catch(e){} }
  } catch (e) {
    msg.textContent = 'Error: ' + e;
  }
  go.disabled = false;
});

window.addEventListener('pagehide', () => navigator.sendBeacon('/cinema/stop'));
</script>
</body></html>
"""


# Sans compteur, /login encaisse environ 430 essais par seconde : un PIN a
# quatre chiffres, soit 10 000 combinaisons, tombe en une douzaine de secondes.
# Le journal montrait deja les tentatives, mais rien ne les ralentissait.
LOCK_AFTER = 5          # essais rates tolerés avant le premier blocage
LOCK_SECONDS = 30       # duree du premier blocage, doublee a chaque recidive
LOCK_MAX = 15 * 60      # plafond : inutile de bannir pour la journee

_attempts = {}          # ip -> [rates, bloque_jusqu_a, duree_suivante]
_attempts_lock = threading.Lock()


def lock_remaining(ip):
    """Secondes restantes avant de pouvoir reessayer, 0 si la voie est libre."""
    with _attempts_lock:
        entry = _attempts.get(ip)
        if not entry:
            return 0
        return max(0, int(entry[1] - time.time()))


def note_failure(ip):
    """Compte un echec et renvoie la duree de blocage s il vient d etre pose.

    Le blocage double a chaque nouvelle serie : une erreur de frappe coute
    trente secondes, un script en coute des milliers avant d avoir parcouru
    une fraction de l espace.
    """
    with _attempts_lock:
        entry = _attempts.setdefault(ip, [0, 0.0, LOCK_SECONDS])
        entry[0] += 1
        if entry[0] < LOCK_AFTER:
            return 0
        entry[0] = 0
        entry[1] = time.time() + entry[2]
        posed = entry[2]
        entry[2] = min(entry[2] * 2, LOCK_MAX)
        return int(posed)


def note_success(ip):
    with _attempts_lock:
        _attempts.pop(ip, None)


def logged_in():
    return session.get("ok") is True


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = request.remote_addr or "?"
    if request.method == "POST":
        waiting = lock_remaining(ip)
        if waiting:
            return render_template_string(LOGIN_PAGE, error=False, wait=waiting), 429

        # compare_digest plutot que == : la comparaison ne s arrete pas au
        # premier caractere different, donc le temps de reponse ne renseigne
        # pas sur le nombre de caracteres corrects.
        if secrets.compare_digest(request.form.get("pin", ""), PIN):
            note_success(ip)
            session.permanent = True
            session["ok"] = True
            event("INFO", "Phone connected", ip=ip)
            return redirect("/")

        locked = note_failure(ip)
        # Deliberately visible: anyone on the network can try their luck,
        # and this is the only sign of it we would ever notice.
        if locked:
            event("WARN", "PIN refused - address locked out", ip=ip, seconds=locked)
            return render_template_string(LOGIN_PAGE, error=False, wait=locked), 429
        event("WARN", "PIN refused", ip=ip)
        return render_template_string(LOGIN_PAGE, error=True)
    return render_template_string(LOGIN_PAGE, error=False,
                                  wait=lock_remaining(ip))


@app.route("/")
def index():
    if not logged_in():
        return redirect("/login")
    return render_template_string(MAIN_PAGE)


# ======================
# Installation sur l ecran d accueil
# ======================
# Sur iPhone, requestFullscreen() n existe pas : seul un element <video>
# obtient un vrai plein ecran, ce que YouTube exploite. Un flux MJPEG dans une
# balise <img> n y a pas droit. Le seul moyen de se debarrasser de la barre
# d onglets de Safari est donc d installer la page sur l ecran d accueil :
# lancee depuis son icone, elle s ouvre sans aucune interface de navigateur.


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "Remote PC",
        "short_name": "Remote PC",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        # any : laisse le telephone suivre sa propre rotation. iOS ignore de
        # toute facon le verrouillage d orientation demande par une page web.
        "orientation": "any",
        "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}],
    })


@app.route("/icon.png")
def icon():
    """Icone d ecran d accueil, dessinee plutot que livree en binaire."""
    size = 180
    img = Image.new("RGB", (size, size), (14, 14, 16))
    draw = ImageDraw.Draw(img)
    # Un ecran, et un curseur dessus.
    draw.rounded_rectangle([size * 0.16, size * 0.22, size * 0.84, size * 0.62],
                           radius=size // 18, outline=(235, 235, 235),
                           width=max(3, size // 30))
    draw.rounded_rectangle([size * 0.42, size * 0.62, size * 0.58, size * 0.72],
                           radius=size // 40, fill=(235, 235, 235))
    draw.rounded_rectangle([size * 0.28, size * 0.72, size * 0.72, size * 0.78],
                           radius=size // 40, fill=(235, 235, 235))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), mimetype="image/png")


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
    # delta : +1 depuis le bouton, mais un debordement vers la gauche demande
    # l ecran precedent, sinon pousser dans un sens et dans l autre reviendrait
    # au meme et le geste n aurait aucun sens.
    delta = (request.get_json(silent=True) or {}).get("delta", 1)
    delta = 1 if int(delta) >= 0 else -1
    current_monitor["idx"] = (current_monitor["idx"] - 1 + delta) % MONITOR_COUNT + 1
    event("INFO", "Screen %d of %d" % (current_monitor["idx"], MONITOR_COUNT))
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


MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120       # un cran de molette, unite fixee par Windows


def scroll_wheel(clicks):
    """Molette par SendInput plutot que par pyautogui.

    pyautogui.scroll passe par mouse_event, une API heritee que beaucoup
    d applications modernes ignorent : la souris se deplacait bien, mais rien
    ne defilait. SendInput emprunte le meme chemin qu une vraie molette.
    """
    if not clicks:
        return 0
    item = _INPUT()
    item.type = 0   # INPUT_MOUSE
    item.mi = _MOUSEINPUT(0, 0, clicks * WHEEL_DELTA, MOUSEEVENTF_WHEEL, 0, None)
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT))
    if sent != 1:
        print("SendInput molette : refuse (code %d)"
              % ctypes.windll.kernel32.GetLastError())
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


def _to_screen(data):
    """Coordonnees fractionnaires du telephone -> pixels de l ecran choisi."""
    mon = MONITORS[current_monitor["idx"]]
    return (mon["left"] + int(data["x"] * mon["width"]),
            mon["top"] + int(data["y"] * mon["height"]))


@app.route("/scroll", methods=["POST"])
def scroll():
    if not logged_in():
        return jsonify(ok=False), 401
    data = request.get_json()
    # La molette va a la fenetre sous le curseur, pas a celle qui a le focus :
    # sans deplacer la souris, le defilement partirait au mauvais endroit.
    if data.get("x") is not None:
        pyautogui.moveTo(*_to_screen(data))
    scroll_wheel(int(data.get("amount", 0)))
    return jsonify(ok=True)


@app.route("/mouse", methods=["POST"])
def mouse():
    """Bouton maintenu, pour trainer une fenetre ou selectionner du texte."""
    if not logged_in():
        return jsonify(ok=False), 401
    data = request.get_json()
    action = data.get("action")
    if data.get("x") is not None:
        pyautogui.moveTo(*_to_screen(data))
    if action == "down":
        pyautogui.mouseDown(button=data.get("button", "left"))
    elif action == "up":
        pyautogui.mouseUp(button=data.get("button", "left"))
    return jsonify(ok=True)

@app.route("/cinema")
def cinema_page():
    if not logged_in():
        return redirect("/login")
    return render_template_string(CINEMA_PAGE)


@app.route("/cinema/start", methods=["POST"])
def cinema_start():
    if not logged_in():
        return jsonify(ok=False), 401
    if not cinema.ffmpeg_available():
        event("ERROR", "Cinema mode refused: ffmpeg is not in PATH")
        return jsonify(ok=False, error="ffmpeg introuvable dans le PATH.")
    cinema.start(MONITORS[current_monitor["idx"]], index=current_monitor["idx"],
                 bitrate=CINEMA_BITRATE, fps=CINEMA_FPS)
    if not cinema.wait_for_playlist():
        cinema.stop()
        event("ERROR", "Cinema mode: the ffmpeg capture did not start")
        return jsonify(ok=False, error="La capture n a pas demarre.")
    event("INFO", "Cinema mode started", screen=current_monitor["idx"])
    return jsonify(ok=True)


@app.route("/cinema/stop", methods=["POST"])
def cinema_stop():
    if cinema.is_running():
        event("INFO", "Cinema mode stopped")
    cinema.stop()
    return jsonify(ok=True)


@app.route("/hls/<path:name>")
def hls_file(name):
    if not logged_in():
        return jsonify(ok=False), 401
    out_dir = cinema.directory()
    if not out_dir:
        return jsonify(ok=False), 404
    # Chaque segment reclame prolonge la diffusion : le surveillant coupe
    # ffmpeg des que le telephone cesse de demander.
    cinema.touch()
    return send_from_directory(out_dir, name)


@sock.route("/audio")
def audio_socket(ws):
    """Diffuse le son du PC en PCM brut.

    Rien n est mis en tampon ici : chaque morceau part des qu il sort de
    ffmpeg. La seule marge est celle que le navigateur s accorde pour
    programmer la lecture, et elle se compte en dizaines de millisecondes.
    """
    if not logged_in():
        return
    q = audio.subscribe()
    if q is None:
        event("ERROR", "Live sound unavailable: no ffmpeg or no audio device")
        return

    event("INFO", "Sound started", ip=request.remote_addr)
    try:
        while True:
            ws.send(q.get())
    except Exception:
        # Le telephone a ferme l onglet, verrouille l ecran ou change de
        # reseau : ce n est pas une panne, juste la fin de l ecoute.
        pass
    finally:
        audio.unsubscribe(q)
        event("INFO", "Sound stopped", ip=request.remote_addr)


def files_dir():
    """Le dossier partage, cree a la demande plutot qu au demarrage : il ne
    doit exister que si on s en sert."""
    os.makedirs(FILES_DIR, exist_ok=True)
    return FILES_DIR


def safe_name(name):
    """Ramene un nom de fichier a quelque chose qui ne peut pas sortir du
    dossier. secure_filename vide certains noms entierement - un nom purement
    accentue, par exemple - d ou le repli sur un nom neutre."""
    cleaned = secure_filename(name or "")
    return cleaned or "file"


def human_size(n):
    """Taille lisible. Un fichier de 26 octets annonce en kilo-octets entiers
    affichait 0, ce qui laissait croire a un envoi vide."""
    if n < 1024:
        return "%d B" % n
    if n < 1048576:
        return "%d KB" % round(n / 1024)
    if n < 1073741824:
        return "%.1f MB" % (n / 1048576)
    return "%.2f GB" % (n / 1073741824)


def unique_path(directory, name):
    """Ajoute un rang plutot que d ecraser : deux photos prises a la suite
    portent souvent le meme nom, et perdre la premiere serait une surprise
    desagreable."""
    base, ext = os.path.splitext(name)
    candidate, n = name, 2
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = "%s (%d)%s" % (base, n, ext)
        n += 1
    return os.path.join(directory, candidate), candidate


@app.route("/files")
def files_list():
    if not logged_in():
        return jsonify(ok=False), 401
    directory = files_dir()
    items = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        info = os.stat(path)
        items.append({"name": name, "size": info.st_size, "at": info.st_mtime})
    # Le plus recent en premier : c est presque toujours celui qu on vient de
    # deposer et qu on veut recuperer.
    items.sort(key=lambda i: i["at"], reverse=True)
    return jsonify(ok=True, files=items, dir=directory)


@app.route("/files/upload", methods=["POST"])
def files_upload():
    if not logged_in():
        return jsonify(ok=False), 401
    directory = files_dir()
    saved = []
    for item in request.files.getlist("file"):
        if not item.filename:
            continue
        path, name = unique_path(directory, safe_name(item.filename))
        item.save(path)
        saved.append(name)
        event("INFO", "File received", name=name, size=human_size(os.path.getsize(path)))
    if not saved:
        return jsonify(ok=False, error="No file in the request."), 400
    return jsonify(ok=True, saved=saved)


@app.route("/files/get/<path:name>")
def files_get(name):
    if not logged_in():
        return jsonify(ok=False), 401
    # as_attachment : sans cela Safari affiche l image dans l onglet au lieu de
    # proposer de l enregistrer, et on perd le fichier.
    try:
        return send_from_directory(files_dir(), safe_name(name), as_attachment=True)
    except Exception:
        # Nom fantaisiste ou fichier efface entre la liste et le clic : une
        # erreur 500 laisserait croire a une panne du serveur.
        return jsonify(ok=False, error="No such file."), 404


@app.route("/files/delete", methods=["POST"])
def files_delete():
    if not logged_in():
        return jsonify(ok=False), 401
    name = safe_name((request.get_json(silent=True) or {}).get("name", ""))
    path = os.path.join(files_dir(), name)
    try:
        os.remove(path)
    except OSError as exc:
        return jsonify(ok=False, error=str(exc)), 404
    event("INFO", "File deleted", name=name)
    return jsonify(ok=True)


def parse_tailscale_status(text):
    """Tire l adresse de la sortie de `tailscale status --json`.

    Separee pour pouvoir etre eprouvee sans Tailscale installe : c est la
    partie qui peut se tromper, l appel au binaire n a rien d interessant.
    """
    if not text:
        return None
    try:
        self_info = json.loads(text).get("Self") or {}
    except (ValueError, AttributeError):
        return None

    # Le nom MagicDNS est prefere a l adresse : il ne change pas, la ou une
    # adresse peut etre reattribuee.
    name = (self_info.get("DNSName") or "").rstrip(".")
    if name and self_info.get("Online"):
        return name
    for addr in self_info.get("TailscaleIPs") or []:
        if ":" not in addr:          # on laisse l IPv6 de cote
            return addr
    return None


def tailscale_address():
    """Adresse du PC sur le reseau prive Tailscale, ou None.

    Le serveur n a rien de special a faire pour etre joignable de l exterieur :
    il ecoute deja sur toutes les interfaces, donc sur celle de Tailscale des
    qu elle existe. La seule chose qui manquait etait de connaitre cette
    adresse pour pouvoir l afficher - sinon il faut la taper a la main.

    On prefere le nom MagicDNS a l adresse : il ne change pas, la ou une
    adresse peut etre reattribuee.
    """
    exe = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                       "Tailscale", "tailscale.exe")
    if not os.path.exists(exe):
        exe = "tailscale"

    def run(args):
        try:
            out = subprocess.run([exe] + args, capture_output=True, text=True,
                                 timeout=4,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    parsed = parse_tailscale_status(run(["status", "--json"]))
    if parsed:
        return parsed

    first = (run(["ip", "-4"]) or "").splitlines()
    return first[0].strip() if first else None


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def parse_args():
    """Tout est reglable au lancement : la machine d en face n aura ni les
    memes ecrans, ni les memes peripheriques audio, ni le meme reseau."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Control the PC from a phone, over the local network.")
    parser.add_argument("--pin", default=PIN,
                        help="access code (default: REMOTE_PIN, else a random one)")
    parser.add_argument("--port", type=int, default=5000, help="port to listen on")
    parser.add_argument("--host", default="0.0.0.0",
                        help="interface to listen on; 127.0.0.1 exposes the machine only")
    parser.add_argument("--width", type=int, default=STREAM_WIDTH,
                        help="width of the interactive stream, in pixels")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY,
                        help="JPEG quality of the interactive stream, 1 to 95")
    parser.add_argument("--fps", type=int, default=int(round(1 / FRAME_DELAY)),
                        help="frames per second of the interactive stream")
    parser.add_argument("--audio", metavar="NAME",
                        help="audio device for cinema mode; 'none' to stay silent")
    parser.add_argument("--cinema-bitrate", default="6M",
                        help="video bitrate of cinema mode")
    parser.add_argument("--cinema-fps", type=int, default=30,
                        help="frames per second of cinema mode")
    parser.add_argument("--monitor", type=int, default=1, metavar="N",
                        help="screen streamed at startup, 1 to N")
    parser.add_argument("--files-dir", metavar="PATH", default=FILES_DIR,
                        help="folder shared with the phone in both directions")
    parser.add_argument("--list-audio", action="store_true",
                        help="measure and print the level of every device, then exit")
    parser.add_argument("--json", action="store_true",
                        help="with --list-audio: JSON output, for the interface")
    return parser.parse_args()


def list_audio(seconds=2, as_json=False):
    """Affiche les peripheriques avec leur niveau reel.

    Un peripherique peut s ouvrir sans erreur et ne porter que du silence : les
    mix virtuels auxquels aucune source n est affectee sont dans ce cas. Le seul
    moyen de savoir lequel choisir est de les ecouter quelques secondes, en
    laissant tourner un son sur la machine pendant la mesure.
    """
    names = cinema.list_audio_devices()
    if as_json:
        # The interface needs the names to fill its list; the measurement
        # itself costs two seconds per device.
        print(json.dumps([{"name": n, "level": cinema.measure_device(n, seconds)}
                          for n in names], ensure_ascii=False))
        return
    if not names:
        print("No DirectShow audio device found.")
        return
    print("Measuring %d seconds each - leave a sound playing meanwhile.\n"
          % seconds)
    for name in names:
        level = cinema.measure_device(name, seconds)
        if level is None:
            verdict = "unreadable"
        elif level < -80:
            verdict = "silence"
        else:
            verdict = "%.0f dB" % level
        print("  %-46s %s" % (name, verdict))
    print("\nPass the exact name back with --audio \"...\"")


if __name__ == "__main__":
    args = parse_args()

    if args.list_audio:
        list_audio(as_json=args.json)
        raise SystemExit(0)

    PIN = args.pin
    STREAM_WIDTH = args.width
    JPEG_QUALITY = args.quality
    FRAME_DELAY = 1 / max(1, args.fps)
    CINEMA_BITRATE = args.cinema_bitrate
    CINEMA_FPS = args.cinema_fps
    if args.audio is not None:
        cinema.set_audio_device(args.audio)

    current_monitor["idx"] = min(max(1, args.monitor), MONITOR_COUNT)
    FILES_DIR = args.files_dir

    device = cinema.pick_audio_device()
    tailnet = tailscale_address()
    remote_url = "http://%s:%d" % (tailnet, args.port) if tailnet else None
    ffmpeg = cinema.ffmpeg_available()
    capture = None
    if ffmpeg:
        capture = "ddagrab (GPU)" if cinema._has_ddagrab() else "gdigrab (CPU)"

    # One event carries every startup fact, as JSON: without it the
    # interface would have to scrape the banner below, and would break at the
    # first reworded word.
    event("READY", "Server started",
          url="http://%s:%d" % (local_ip(), args.port),
          pin=PIN, port=args.port,
          monitors=MONITOR_COUNT, monitor=current_monitor["idx"],
          width=STREAM_WIDTH, quality=JPEG_QUALITY, fps=args.fps,
          ffmpeg=ffmpeg, capture=capture, audio=device, files=FILES_DIR,
          remote=remote_url)

    print("=" * 60)
    print("Connection PIN : %s" % PIN)
    print("From your phone (same network) : http://%s:%d" % (local_ip(), args.port))
    print("Screens found  : %d" % MONITOR_COUNT)
    print("Interactive    : %dpx, quality %d, ~%d fps"
          % (STREAM_WIDTH, JPEG_QUALITY, args.fps))
    if not cinema.ffmpeg_available():
        print("Cinema mode    : unavailable (ffmpeg not in PATH)")
    else:
        capture = "ddagrab (GPU)" if cinema._has_ddagrab() else "gdigrab (CPU)"
        print("Cinema mode    : %s, %s at %d fps" % (capture, args.cinema_bitrate, args.cinema_fps))
        print("Cinema sound   : %s" % (device or "none - see --list-audio"))
    print("Shared folder  : %s" % FILES_DIR)
    print("Remote access  : %s" % (remote_url or "none - Tailscale not detected"))
    print("=" * 60)
    app.run(host=args.host, port=args.port, threaded=True)
