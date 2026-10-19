"""Rutas y configuracion persistente de la app de escritorio."""
import json
import os
import sys

APP_NAME = "TAC 3D"
VERSION = "1.1.0"

FROZEN = getattr(sys, "frozen", False)
# recursos empaquetados (pipeline, visor, uv.exe); en desarrollo, la raiz del proyecto
RES = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(RES, "pipeline")
VISOR = os.path.join(RES, "visor")
APP_RES = os.path.join(RES, "app") if not FROZEN else RES

LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
DATA = os.path.join(LOCAL, "TAC3D")
LOGS = os.path.join(DATA, "registros")
TMP = os.path.join(DATA, "temporal")
MOTOR_DIR = os.path.join(DATA, "motor")
CONFIG_FILE = os.path.join(DATA, "config.json")
TIEMPOS_FILE = os.path.join(DATA, "tiempos.json")
MODELOS_DIR = os.path.join(os.path.expanduser("~"), ".totalsegmentator")

for _d in (DATA, LOGS, TMP):
    os.makedirs(_d, exist_ok=True)


def uv_exe():
    for p in (os.path.join(RES, "uv.exe"), os.path.join(APP_RES, "herramientas", "uv.exe")):
        if os.path.exists(p):
            return p
    return None


def motor_candidates():
    return [os.path.join(MOTOR_DIR, "venv", "Scripts", "python.exe"),
            os.path.join(LOCAL, "tac3d_env", "Scripts", "python.exe")]


def default_library():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    cands = [os.path.join(os.path.expanduser("~"), "Downloads", "TAC_3D", "estudios")]
    if not FROZEN:
        cands.insert(0, os.path.join(RES, "estudios"))
    for c in cands:
        if os.path.exists(os.path.join(c, "estudios.json")):
            return c
    return os.path.join(docs, "TAC3D", "estudios")


DEFAULTS = {"estudios": None, "motor_python": None, "dispositivo": "auto", "borrar_temporales": True}


class Config(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        try:
            self.update(json.load(open(CONFIG_FILE, encoding="utf-8")))
        except Exception:
            pass
        if not self.get("estudios"):
            self["estudios"] = default_library()
        os.makedirs(self["estudios"], exist_ok=True)

    def save(self):
        json.dump(dict(self), open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    @property
    def library(self):
        return self["estudios"]

    def motor_python(self):
        """Python del motor de IA ya instalado, o None."""
        cands = ([self["motor_python"]] if self.get("motor_python") else []) + motor_candidates()
        return next((c for c in cands if c and os.path.exists(c)), None)


def fmt_dur(sec):
    sec = max(0, int(round(sec)))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"
