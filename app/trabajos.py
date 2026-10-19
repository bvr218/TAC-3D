"""Cola de procesamiento: ejecuta pipeline/importar.py con el Python del motor y sigue su progreso."""
import json
import os
import re
import shutil
import subprocess
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

import config
import requisitos

STEP_LABELS = {
    "extraer": "Leer y convertir las imagenes DICOM",
    "seg_total": "IA: organos, huesos y vasos", "seg_body": "IA: contorno del cuerpo",
    "seg_abdominal_muscles": "IA: musculos de la pared", "seg_lung_vessels": "IA: vasos pulmonares",
    "seg_liver_vessels": "IA: vasos del higado", "seg_vertebrae_body": "IA: vertebras y discos",
    "seg_liver_segments": "IA: segmentos del higado", "seg_lung_nodules": "IA: nodulos y masas del pulmon",
    "seg_liver_lesions": "IA: lesiones del higado", "seg_pleural_pericard_effusion": "IA: liquido pleural y pericardico",
    "calculos": "Contenido interno, zonas de otra densidad y calculos", "mallas": "Generar modelos 3D", "control": "Imagenes de control",
}
SEG_STEPS = [k for k in STEP_LABELS if k.startswith("seg_")]
# segundos = fijo + por millon de voxeles, medido en una RTX 3050 de 4 GB; se corrige con cada estudio procesado
MODEL = {
    "extraer": (5, 0.6), "seg_total": (40, 3.0), "seg_body": (20, 1.0), "seg_abdominal_muscles": (30, 6.5),
    "seg_lung_vessels": (20, 2.0), "seg_liver_vessels": (20, 2.5), "seg_vertebrae_body": (20, 2.5),
    "seg_liver_segments": (20, 2.0), "seg_lung_nodules": (40, 8.0), "seg_liver_lesions": (40, 6.0),
    "seg_pleural_pericard_effusion": (30, 4.0), "calculos": (15, 4.5), "mallas": (120, 1.5), "control": (3, 0.05),
}
CPU_FACTOR = 10.0
KIND_LABEL = {"nuevo": "Importar estudio", "modelos": "Regenerar modelos 3D", "completo": "Repetir todo con IA"}


def load_factors():
    try:
        return json.load(open(config.TIEMPOS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def expected(step, voxels, device, factors=None):
    a, b = MODEL.get(step, (30, 1.0))
    t = a + b * voxels / 1e6
    if device == "cpu" and step.startswith("seg_"):
        t *= CPU_FACTOR
    f = (factors if factors is not None else load_factors()).get(device, {}).get(step, 1.0)
    return t * f


def default_plan(kind):
    if kind == "modelos":
        return ["calculos", "mallas", "control"]
    return (["extraer"] if kind == "nuevo" else []) + SEG_STEPS + ["calculos", "mallas", "control"]


def estimate(kind, voxels, device):
    f = load_factors()
    return sum(expected(s, voxels, device, f) for s in default_plan(kind))


def update_index(library):
    items = []
    for sid in sorted(os.listdir(library)) if os.path.isdir(library) else []:
        mf = os.path.join(library, sid, "web", "manifest.json")
        if not os.path.exists(mf):
            continue
        try:
            e = json.load(open(mf, encoding="utf-8")).get("estudio", {})
        except Exception:
            continue
        items.append({"id": sid, "titulo": e.get("titulo", sid), "subtitulo": e.get("subtitulo", ""),
                      "paciente": e.get("paciente", "")})
    json.dump(items, open(os.path.join(library, "estudios.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


class Job:
    _n = 0

    def __init__(self, kind, sid, entrada=None, serie=None, voxeles=0, nombre="", reemplazar=False):
        Job._n += 1
        self.num = Job._n
        self.kind, self.sid, self.entrada, self.serie = kind, sid, entrada, serie
        self.nombre = nombre or sid
        self.reemplazar = reemplazar
        self.estado = "cola"          # cola, procesando, listo, error, cancelado
        self.plan = default_plan(kind)
        self.etapa = None
        self.t_etapa = None
        self.durs = {}
        self.voxeles = voxeles
        self.device = "gpu"
        self.inicio = self.fin = None
        self.error = ""
        self.ultima = ""
        self.log_path = None
        self.factors = load_factors()

    def args(self):
        script = os.path.join(config.PIPELINE, "importar.py")
        if self.kind == "nuevo":
            a = [script, self.entrada, "--id", self.sid]
            if self.serie:
                a += ["--serie", self.serie]
            if self.reemplazar:
                a.append("--forzar")
            return a
        return [script, "--rehacer", self.sid] + (["--forzar"] if self.kind == "completo" else [])

    def exp(self, step):
        return expected(step, self.voxeles or 40e6, self.device, self.factors)

    def progress(self):
        """(porcentaje 0-100, segundos restantes estimados, fraccion de la etapa actual)."""
        if self.estado == "listo":
            return 100.0, 0, 1.0
        if self.estado == "cola" or not self.inicio:
            return 0.0, sum(self.exp(s) for s in self.plan), 0.0
        total = sum(self.exp(s) for s in self.plan)
        done = sum(self.exp(s) for s in self.plan if s in self.durs)
        # si las etapas terminadas fueron mas lentas o rapidas de lo previsto, corrige el resto igual
        real = sum(self.durs.values())
        ratio = min(3.0, max(0.33, real / done)) if done > 30 else 1.0
        cur, frac, left = 0.0, 0.0, 0.0
        if self.etapa and self.etapa not in self.durs:
            e = self.exp(self.etapa) * ratio
            el = time.time() - self.t_etapa
            frac = min(0.97, el / e) if e else 0
            cur = self.exp(self.etapa) * frac
            left += max(e - el, e * 0.05)
        left += sum(self.exp(s) * ratio for s in self.plan if s not in self.durs and s != self.etapa)
        return min(99.5, 100 * (done + cur) / total), left, frac


class Runner(QObject):
    changed = Signal(object)
    finished = Signal(object)
    output = Signal(object, str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.jobs = []
        self.current = None
        self.proc = None
        self._buf = ""
        self._log = None

    def busy(self):
        return self.current is not None

    def pending(self):
        return [j for j in self.jobs if j.estado == "cola"]

    def job_for(self, sid):
        for j in reversed(self.jobs):
            if j.sid == sid and j.estado in ("cola", "procesando"):
                return j
        return None

    def add(self, job):
        if self.job_for(job.sid):
            return False
        self.jobs.append(job)
        self.changed.emit(job)
        self._next()
        return True

    def remove(self, job):
        if job.estado == "cola":
            self.jobs.remove(job)
            self.changed.emit(job)

    def device(self):
        d = self.cfg.get("dispositivo", "auto")
        if d in ("gpu", "cpu"):
            return d
        return "gpu" if requisitos.gpu_usable() else "cpu"

    def _next(self):
        if self.current:
            return
        nxt = next((j for j in self.jobs if j.estado == "cola"), None)
        if not nxt:
            return
        py = self.cfg.motor_python()
        if not py:
            nxt.estado, nxt.error = "error", "El motor de IA no esta instalado (pagina 'Equipo y motor')"
            self.finished.emit(nxt)
            return self._next()
        j = self.current = nxt
        j.estado, j.inicio, j.device = "procesando", time.time(), self.device()
        j.log_path = os.path.join(config.LOGS, f"{time.strftime('%Y%m%d-%H%M%S')}_{j.sid}.log")
        self._log = open(j.log_path, "w", encoding="utf-8")
        self._log.write(f"{KIND_LABEL[j.kind]}: {j.sid}\n{py} {' '.join(j.args())}\n\n")

        env = QProcessEnvironment.systemEnvironment()
        for k, v in {"TAC3D_ESTUDIOS": self.cfg.library, "TAC3D_TMP": config.TMP, "TAC3D_DEVICE": j.device,
                     "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                     "TQDM_MININTERVAL": "2"}.items():
            env.insert(k, v)
        p = self.proc = QProcess(self)
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.setWorkingDirectory(config.PIPELINE)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._failed_start)
        self._buf = ""
        p.start(py, j.args())
        self.changed.emit(j)

    def _failed_start(self, err):
        if err == QProcess.FailedToStart and self.current:
            self.current.error = "No se pudo iniciar el motor de IA"
            self._done(-1, None)

    def _read(self):
        j = self.current
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buf += data
        parts = re.split(r"[\r\n]", self._buf)
        self._buf = parts.pop()
        for line in parts:
            self._line(j, line.rstrip())
        if self._buf.strip():
            j.ultima = self._buf.strip()[-160:]
        self.changed.emit(j)

    def _line(self, j, line):
        if not line:
            return
        if line.startswith("@@"):
            cmd, _, val = line[2:].partition(" ")
            if cmd == "PLAN":
                j.plan = [s for s in val.split(",") if s]
            elif cmd == "ETAPA":
                now = time.time()
                if j.etapa and j.etapa not in j.durs:
                    j.durs[j.etapa] = now - j.t_etapa
                j.etapa, j.t_etapa = val, now
            elif cmd == "VOXELES":
                j.voxeles = int(val)
            elif cmd == "DISPOSITIVO":
                j.device = val
            elif cmd == "ERROR":
                j.error = val
            elif cmd == "FIN":
                j.fin_ok = True
            return
        if re.search(r"\d+%\|", line):  # barras tqdm: solo se muestra la ultima
            j.ultima = line[-160:]
            return
        j.ultima = line[-160:]
        if self._log:
            self._log.write(line + "\n")
        self.output.emit(j, line)

    def _done(self, code, status):
        j = self.current
        if j is None:
            return
        if self._buf.strip():
            self._line(j, self._buf.strip())
        now = time.time()
        if j.etapa and j.etapa not in j.durs:
            j.durs[j.etapa] = now - j.t_etapa
        j.fin = now
        if j.estado == "cancelado":
            pass
        elif code == 0 and getattr(j, "fin_ok", False):
            j.estado = "listo"
            self._learn(j)
        else:
            j.estado = "error"
            if not j.error:
                j.error = f"El proceso termino con codigo {code}. Revisa el registro"
        if self._log:
            self._log.write(f"\n== {j.estado.upper()} en {config.fmt_dur(j.fin - j.inicio)} {j.error}\n")
            self._log.close()
            self._log = None
        if j.kind == "nuevo" and j.estado != "listo" and not j.reemplazar:
            sdir = os.path.join(self.cfg.library, j.sid)
            if not os.path.exists(os.path.join(sdir, "trabajo", "ct.nii.gz")):
                shutil.rmtree(sdir, ignore_errors=True)
        try:
            update_index(self.cfg.library)
        except OSError:
            pass
        if self.cfg.get("borrar_temporales", True):
            shutil.rmtree(os.path.join(config.TMP, j.sid), ignore_errors=True)
        self.current = None
        self.proc = None
        self.finished.emit(j)
        self.changed.emit(j)
        self._next()

    def cancel(self, job=None):
        j = job or self.current
        if j is None:
            return
        if j.estado == "cola":
            j.estado = "cancelado"
            self.changed.emit(j)
            return
        if j is self.current and self.proc:
            j.estado = "cancelado"
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.processId())], capture_output=True,
                           creationflags=requisitos.NO_WINDOW)

    def _learn(self, j):
        """Ajusta los tiempos estimados de este equipo con lo que tardo de verdad."""
        f = load_factors()
        d = f.setdefault(j.device, {})
        for s, real in j.durs.items():
            if s not in MODEL or real < 2:
                continue
            r = real / expected(s, j.voxeles, j.device, {})
            d[s] = round(min(10.0, max(0.1, 0.5 * d[s] + 0.5 * r if s in d else r)), 3)
        json.dump(f, open(config.TIEMPOS_FILE, "w", encoding="utf-8"), indent=1)
