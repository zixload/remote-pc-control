import ctypes
import io
import os
import secrets
import socket
import time

import mss
import pyautogui

import cinema
import focus_detect
from flask import (Flask, Response, jsonify, redirect, render_template_string,
                   request, send_from_directory, session)
from PIL import Image, ImageDraw

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

PIN = os.environ.get("REMOTE_PIN") or "1312"
STREAM_WIDTH = int(os.environ.get("STREAM_WIDTH", 1600))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", 90))
FRAME_DELAY = float(os.environ.get("FRAME_DELAY", 1 / 20))  # ~20 fps cible
CINEMA_BITRATE = os.environ.get("CINEMA_BITRATE", "6M")
CINEMA_FPS = int(os.environ.get("CINEMA_FPS", 30))

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
  body.cinema #controls, body.cinema #handle{display:none}
  body.cinema #screen{height:100%;object-fit:contain}
  /* Champ invisible mais reellement focalisable : iOS n'ouvre son clavier que
     pour un champ present dans la page. opacity:0 suffit, display:none ou
     visibility:hidden le rendraient infocalisable. */
  #ghost{position:fixed;bottom:0;left:0;width:1px;height:1px;opacity:0;
    border:0;padding:0;font-size:16px;z-index:1}
  /* 16px : en dessous, Safari zoome automatiquement sur le champ focalise. */
  #typeBadge{position:absolute;left:50%;transform:translateX(-50%);bottom:calc(70px + env(safe-area-inset-bottom,0px));
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
</style></head>
<body>
<div id="viewport">
  <img id="screen" src="/stream">
  <div id="controls">
    <button onclick="enterCinema()">Plein ecran</button>
    <button onclick="nextMonitor()" id="monBtn">Ecran</button>
    <button onclick="location.href='/cinema'">Cinema</button>
    <button class="ico" onclick="toggleHelp()">i</button>
    <button onclick="toggleMore()">Plus &#9662;</button>
    <div id="morePanel">
      <button onclick="key('enter')">Enter</button>
      <button onclick="key('esc')">Esc</button>
      <button onclick="key('tab')">Tab</button>
      <button class="ico" onclick="key('up')">&uarr;</button>
      <button class="ico" onclick="key('down')">&darr;</button>
      <button class="ico" onclick="key('left')">&larr;</button>
      <button class="ico" onclick="key('right')">&rarr;</button>
      <button onclick="key('backspace')">&larr;Del</button>
      <button onclick="rclick()">Clic droit</button>
    </div>
  </div>
  <button id="handle" onclick="toggleControls()">&#8942;</button>
  <div id="helpPanel" onclick="toggleHelp()">
    <h3>Comment ca marche</h3>
    <dl>
      <dt>Tapoter</dt><dd>clic gauche</dd>
      <dt>Deux tapotements</dt><dd>clic droit, au meme endroit</dd>
      <dt>Appui long puis glisser</dt><dd>garde le bouton enfonce : deplacer une
        fenetre, selectionner du texte</dd>
      <dt>Deux doigts qui glissent</dt><dd>molette</dd>
      <dt>Pincer</dt><dd>zoomer la vue</dd>
      <dt>Un doigt, vue zoomee</dt><dd>deplacer la vue</dd>
      <dt>Badge clavier</dt><dd>apparait quand le PC est dans un champ texte ;
        appuyer ouvre le clavier du telephone</dd>
      <dt>Cinema</dt><dd>diffusion video avec son, vrai plein ecran, sans
        interaction et avec quelques secondes de retard</dd>
    </dl>
    <div class="close">Tapoter pour fermer</div>
  </div>
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
            touch.target.closest('#controls, #handle, #typeBadge, #ghost, #helpPanel'));
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
  if (document.body.classList.contains('cinema')) return;
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
  if (document.body.classList.contains('cinema')) return;
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

function toggleHelp(){
  document.getElementById('helpPanel').classList.toggle('shown');
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
<button id="go">Lancer le direct</button>
<p id="msg">Sans interaction : l ecran est diffuse en video, ce qui permet le vrai
plein ecran d iOS. Compte trois a cinq secondes de retard.</p>
<a href="/">Revenir a la vue interactive</a>
<script>
const v = document.getElementById('v'), go = document.getElementById('go'),
      msg = document.getElementById('msg');

go.addEventListener('click', async () => {
  go.disabled = true; msg.textContent = 'Demarrage de la capture...';
  try {
    const r = await fetch('/cinema/start', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { msg.textContent = d.error || 'Echec du demarrage.'; go.disabled = false; return; }
    v.src = '/hls/stream.m3u8';
    v.load();
    await v.play();
    msg.textContent = 'En direct. Utilise le bouton plein ecran du lecteur.';
    /* Sur iPhone, seul un <video> peut prendre tout l ecran, et seulement
       depuis un geste. On tente ici, et le bouton natif du lecteur reste le
       recours si le geste a expire pendant le demarrage. */
    if (v.webkitEnterFullscreen) { try { v.webkitEnterFullscreen(); } catch(e){} }
  } catch (e) {
    msg.textContent = 'Erreur : ' + e;
  }
  go.disabled = false;
});

window.addEventListener('pagehide', () => navigator.sendBeacon('/cinema/stop'));
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
        return jsonify(ok=False, error="ffmpeg introuvable dans le PATH.")
    cinema.start(MONITORS[current_monitor["idx"]], index=current_monitor["idx"],
                 bitrate=CINEMA_BITRATE, fps=CINEMA_FPS)
    if not cinema.wait_for_playlist():
        cinema.stop()
        return jsonify(ok=False, error="La capture n a pas demarre.")
    return jsonify(ok=True)


@app.route("/cinema/stop", methods=["POST"])
def cinema_stop():
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
        description="Controle du PC depuis un telephone, sur le reseau local.")
    parser.add_argument("--pin", default=PIN,
                        help="code d acces (defaut : variable REMOTE_PIN, sinon 1312)")
    parser.add_argument("--port", type=int, default=5000, help="port d ecoute")
    parser.add_argument("--host", default="0.0.0.0",
                        help="interface d ecoute ; 127.0.0.1 pour n exposer que la machine")
    parser.add_argument("--width", type=int, default=STREAM_WIDTH,
                        help="largeur du flux interactif en pixels")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY,
                        help="qualite JPEG du flux interactif, 1 a 95")
    parser.add_argument("--fps", type=int, default=int(round(1 / FRAME_DELAY)),
                        help="images par seconde du flux interactif")
    parser.add_argument("--audio", metavar="NOM",
                        help="peripherique audio du mode cinema ; 'none' pour couper")
    parser.add_argument("--cinema-bitrate", default="6M",
                        help="debit video du mode cinema")
    parser.add_argument("--cinema-fps", type=int, default=30,
                        help="images par seconde du mode cinema")
    parser.add_argument("--list-audio", action="store_true",
                        help="mesure et affiche le niveau de chaque peripherique, puis quitte")
    return parser.parse_args()


def list_audio(seconds=2):
    """Affiche les peripheriques avec leur niveau reel.

    Un peripherique peut s ouvrir sans erreur et ne porter que du silence : les
    mix virtuels auxquels aucune source n est affectee sont dans ce cas. Le seul
    moyen de savoir lequel choisir est de les ecouter quelques secondes, en
    laissant tourner un son sur la machine pendant la mesure.
    """
    names = cinema.list_audio_devices()
    if not names:
        print("Aucun peripherique audio DirectShow detecte.")
        return
    print("Mesure sur %d secondes chacun - laisse un son tourner pendant ce temps.\n"
          % seconds)
    for name in names:
        level = cinema.measure_device(name, seconds)
        if level is None:
            verdict = "illisible"
        elif level < -80:
            verdict = "silence"
        else:
            verdict = "%.0f dB" % level
        print("  %-46s %s" % (name, verdict))
    print("\nReprends le nom exact avec --audio \"...\"")


if __name__ == "__main__":
    args = parse_args()

    if args.list_audio:
        list_audio()
        raise SystemExit(0)

    PIN = args.pin
    STREAM_WIDTH = args.width
    JPEG_QUALITY = args.quality
    FRAME_DELAY = 1 / max(1, args.fps)
    CINEMA_BITRATE = args.cinema_bitrate
    CINEMA_FPS = args.cinema_fps
    if args.audio is not None:
        cinema.set_audio_device(args.audio)

    device = cinema.pick_audio_device()
    print("=" * 60)
    print("PIN de connexion : %s" % PIN)
    print("Depuis ton telephone (meme reseau) : http://%s:%d" % (local_ip(), args.port))
    print("Ecrans detectes  : %d" % MONITOR_COUNT)
    print("Flux interactif  : %dpx, qualite %d, ~%d i/s"
          % (STREAM_WIDTH, JPEG_QUALITY, args.fps))
    if not cinema.ffmpeg_available():
        print("Mode cinema      : indisponible (ffmpeg absent du PATH)")
    else:
        capture = "ddagrab (GPU)" if cinema._has_ddagrab() else "gdigrab (processeur)"
        print("Mode cinema      : %s, %s a %d i/s" % (capture, args.cinema_bitrate, args.cinema_fps))
        print("Son du cinema    : %s" % (device or "aucun - voir --list-audio"))
    print("=" * 60)
    app.run(host=args.host, port=args.port, threaded=True)