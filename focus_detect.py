# -*- coding: utf-8 -*-
"""Detection du champ de saisie actif sur le PC.

Sert a savoir quand le telephone doit ouvrir son clavier : si le curseur du PC
est dans un champ texte, on veut pouvoir taper directement plutot que de passer
par une boite de dialogue.

Deux mecanismes, dans cet ordre.

UI Automation d'abord. C'est la seule API qui voit les champs des navigateurs,
des applications Electron et des applications du Store : ces cadres ne creent
pas de caret Win32, ils dessinent le leur. UIA interroge l'arbre d'accessibilite
et repond quel que soit le processus qui demande.

Le caret Win32 ensuite, en repli. GetGUIThreadInfo signale un caret sur le
thread au premier plan. Ca ne couvre que les fenetres natives, mais ca ne
demande aucune dependance et ca reste vrai pour le Bloc-notes, l'explorateur ou
une invite de commande.

comtypes est optionnel : sans lui le module fonctionne en mode Win32 seul.
"""

import ctypes
import ctypes.wintypes as wintypes
import threading
import time

# ======================
# Repli : caret Win32
# ======================
GUI_CARETBLINKING = 0x00000002


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", _RECT)]


def _win32_caret():
    """(actif, titre de la fenetre) d'apres le caret du thread au premier plan."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False, ""
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            return False, ""
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        active = bool(info.hwndCaret) or bool(info.flags & GUI_CARETBLINKING)
        return active, title.value
    except Exception:
        return False, ""


# ======================
# UI Automation
# ======================
# Types de controle consideres comme saisissables.
UIA_EDIT = 50004
UIA_DOCUMENT = 50030
UIA_COMBOBOX = 50003
# Motifs : un element qui expose Value ou Text accepte du texte.
UIA_VALUE_PATTERN = 10002
UIA_TEXT_PATTERN = 10014

_uia = None
_uia_failed = False
_uia_lock = threading.Lock()


def _get_uia():
    """Instancie le client UI Automation une seule fois, par thread appelant.

    COM doit etre initialise dans le thread qui l'utilise ; Flask sert les
    requetes depuis un pool, d'ou l'initialisation paresseuse protegee.
    """
    global _uia, _uia_failed
    if _uia_failed:
        return None
    with _uia_lock:
        if _uia is not None:
            return _uia
        try:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            # GetModule genere le binding a partir de la DLL systeme la premiere
            # fois ; sans lui, comtypes.gen.UIAutomationClient n'existe pas.
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation

            _uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
            return _uia
        except Exception:
            _uia_failed = True
            return None


def _uia_focus():
    """(actif, description) d'apres l'element focalise selon UI Automation."""
    automation = _get_uia()
    if automation is None:
        return None
    try:
        import comtypes
        comtypes.CoInitialize()
        element = automation.GetFocusedElement()
        if not element:
            return False, ""

        control_type = element.CurrentControlType
        name = element.CurrentName or ""
        editable = control_type in (UIA_EDIT, UIA_DOCUMENT, UIA_COMBOBOX)

        if not editable:
            # Certains champs web ne s'annoncent pas comme Edit mais exposent
            # quand meme un motif de saisie.
            for pattern in (UIA_VALUE_PATTERN, UIA_TEXT_PATTERN):
                try:
                    if element.GetCurrentPattern(pattern):
                        editable = True
                        break
                except Exception:
                    continue

        if editable:
            # Un champ en lecture seule n'accepte rien : inutile d'ouvrir le
            # clavier du telephone pour une zone de texte non modifiable.
            try:
                from comtypes.gen.UIAutomationClient import IUIAutomationValuePattern
                value = element.GetCurrentPattern(UIA_VALUE_PATTERN)
                if value:
                    value = value.QueryInterface(IUIAutomationValuePattern)
                    if value.CurrentIsReadOnly:
                        editable = False
            except Exception:
                pass

        return bool(editable), name[:60]
    except Exception:
        return None


# ======================
# Interface publique
# ======================
_cache = {"time": 0.0, "value": None}
CACHE_TTL = 0.25   # le telephone interroge souvent ; UIA coute quelques ms


def text_field_focused(force=False):
    """Renvoie {"active", "source", "detail"}.

    active : le curseur du PC est dans une zone de saisie.
    source : quelle methode a repondu, utile pour diagnostiquer.
    """
    now = time.time()
    if not force and _cache["value"] is not None and now - _cache["time"] < CACHE_TTL:
        return _cache["value"]

    result = None
    uia = _uia_focus()
    if uia is not None:
        active, detail = uia
        result = {"active": active, "source": "uia", "detail": detail}
    else:
        active, detail = _win32_caret()
        result = {"active": active, "source": "win32", "detail": detail}

    _cache["time"] = now
    _cache["value"] = result
    return result


if __name__ == "__main__":
    # Diagnostic : montre en direct ce qui est detecte quand tu cliques
    # d'une application a l'autre.
    print("Clique dans differentes fenetres. Ctrl+C pour arreter.\n")
    last = None
    try:
        while True:
            state = text_field_focused(force=True)
            line = "%-6s %-5s %s" % (
                "SAISIE" if state["active"] else "-",
                state["source"],
                state["detail"])
            if line != last:
                print(line)
                last = line
            time.sleep(0.35)
    except KeyboardInterrupt:
        pass
