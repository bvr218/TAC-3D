"""Instalacion del motor de IA (Python + PyTorch + TotalSegmentator + modelos) con uv.

No necesita Python en el equipo: uv descarga un Python propio dentro de %LOCALAPPDATA%\\TAC3D\\motor.
Se puede usar sin interfaz:  python motor.py [--cpu] [--destino CARPETA]
"""
import os
import shutil
import subprocess
import threading
import time

import config
import requisitos

TORCH = ["torch==2.6.0", "torchvision==0.21.0"]
TORCH_INDEX = {"gpu": "https://download.pytorch.org/whl/cu124", "cpu": "https://download.pytorch.org/whl/cpu"}
TORCH_GB = {"gpu": 2.7, "cpu": 0.3}


class Cancelado(Exception):
    pass


def dir_size(path):
    total = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


class Installer:
    """Ejecuta los pasos y avisa con on_progress(fraccion_total, texto_paso) y on_log(linea)."""

    def __init__(self, device, dest=config.MOTOR_DIR, on_progress=None, on_log=None):
        self.device = device
        self.dest = dest
        self.venv = os.path.join(dest, "venv")
        self.python = os.path.join(self.venv, "Scripts", "python.exe")
        self.cache = os.path.join(config.DATA, "uv_cache")
        self.on_progress = on_progress or (lambda f, t: None)
        self.on_log = on_log or print
        self.proc = None
        self.cancelled = False
        self.uv = config.uv_exe()
        self.env = dict(os.environ, UV_PYTHON_INSTALL_DIR=os.path.join(dest, "python"), UV_CACHE_DIR=self.cache,
                        UV_PYTHON_PREFERENCE="only-managed", UV_NO_PROGRESS="1", PYTHONIOENCODING="utf-8",
                        UV_HTTP_TIMEOUT="300")
        tasks = requisitos.models_missing()
        self.steps = [
            ("python", "Descargando Python 3.12", 3),
            ("venv", "Creando el entorno del motor", 1),
            ("torch", f"Descargando PyTorch ({'GPU NVIDIA' if device == 'gpu' else 'procesador'})", 40 if device == "gpu" else 8),
            ("libs", "Instalando TotalSegmentator y librerias", 15),
            ("modelos", "Descargando los modelos de IA", 30 if tasks else 0),
            ("verificar", "Verificando la instalacion", 2),
        ]

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True,
                           creationflags=requisitos.NO_WINDOW)

    def _run(self, cmd, watch=None, expected=0, base=0.0, weight=0.0, label=""):
        """Ejecuta un comando mostrando su salida; si watch, avanza segun lo que crece esa carpeta."""
        if self.cancelled:
            raise Cancelado()
        self.on_log("> " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        start = dir_size(watch) if watch and os.path.isdir(watch) else 0
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env,
                                     creationflags=requisitos.NO_WINDOW, text=True, encoding="utf-8", errors="replace")
        stop = threading.Event()

        def watcher():
            while not stop.wait(2):
                if watch and expected:
                    got = (dir_size(watch) - start) / 1e9 if os.path.isdir(watch) else 0
                    frac = min(0.97, got / expected)
                    self.on_progress(base + weight * frac, f"{label} ({got:.2f} de ~{expected:.1f} GB)")
        t = threading.Thread(target=watcher, daemon=True)
        t.start()
        try:
            for line in self.proc.stdout:
                line = line.rstrip()
                if line:
                    self.on_log(line)
            code = self.proc.wait()
        finally:
            stop.set()
        if self.cancelled:
            raise Cancelado()
        if code:
            raise RuntimeError(f"El paso '{label}' fallo (codigo {code}). Revisa el registro")

    def run(self):
        if not self.uv:
            raise RuntimeError("Falta uv.exe junto al programa")
        total = sum(w for _, _, w in self.steps)
        done = 0.0
        for key, label, w in self.steps:
            base, weight = done / total, w / total
            self.on_progress(base, label)
            self.on_log(f"\n== {label}")
            getattr(self, "step_" + key)(base, weight, label)
            done += w
        self.on_progress(1.0, "Motor de IA instalado")
        shutil.rmtree(self.cache, ignore_errors=True)
        return self.python

    def step_python(self, base, weight, label):
        self._run([self.uv, "python", "install", "3.12"], label=label)

    def step_venv(self, base, weight, label):
        if os.path.isdir(self.venv):
            shutil.rmtree(self.venv, ignore_errors=True)
        self._run([self.uv, "venv", self.venv, "--python", "3.12", "--seed"], label=label)

    def step_torch(self, base, weight, label):
        self._run([self.uv, "pip", "install", "--python", self.python, *TORCH, "--index-url", TORCH_INDEX[self.device]],
                  watch=self.cache, expected=TORCH_GB[self.device], base=base, weight=weight, label=label)

    def step_libs(self, base, weight, label):
        req = os.path.join(config.APP_RES, "motor_requisitos.txt")
        self._run([self.uv, "pip", "install", "--python", self.python, "-r", req],
                  watch=self.cache, expected=0.6, base=base, weight=weight, label=label)

    def step_modelos(self, base, weight, label):
        tasks = requisitos.models_missing()
        n = max(1, len(tasks))
        for i, t in enumerate(tasks):
            b = base + weight * i / n
            self._run([self.python, "-m", "totalsegmentator.bin.totalseg_download_weights", "-t", t],
                      watch=config.MODELOS_DIR, expected=requisitos.MODELOS_GB / n * 1.6, base=b, weight=weight / n,
                      label=f"{label}: {t} ({i + 1}/{n})")
        left = requisitos.models_missing()
        if left:
            raise RuntimeError("No se pudieron descargar los modelos: " + ", ".join(left))

    def step_verificar(self, base, weight, label):
        code, out = requisitos.run([self.python, "-c", "import torch, totalsegmentator, nibabel, pydicom, trimesh, "
                                    "fast_simplification, skimage, scipy; print(torch.cuda.is_available())"], timeout=300)
        if code:
            raise RuntimeError("La verificacion fallo: " + out[-300:])
        cuda = out.strip().endswith("True")
        self.on_log(f"PyTorch ve la GPU: {'si' if cuda else 'no'}")
        if self.device == "gpu" and not cuda:
            self.on_log("aviso: PyTorch no detecta la GPU; se usara el procesador")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--destino", default=config.MOTOR_DIR)
    a = ap.parse_args()
    dev = "cpu" if a.cpu or not requisitos.gpu_usable() else "gpu"
    last = [0]

    def prog(f, t):
        if time.time() - last[0] > 5:
            print(f"[{f * 100:5.1f}%] {t}", flush=True)
            last[0] = time.time()
    print("python:", Installer(dev, a.destino, prog).run())
