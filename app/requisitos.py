"""Comprobacion de los requisitos minimos del equipo y del estado del motor de IA."""
import os
import platform
import shutil
import socket
import subprocess

import psutil

import config

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# modelos de TotalSegmentator que usa el pipeline (id de dataset -> tarea)
MODELOS = {291: "total", 292: "total", 293: "total", 294: "total", 295: "total", 298: "total", 299: "body",
           952: "abdominal_muscles", 117: "lung_vessels", 8: "liver_vessels", 305: "vertebrae_body",
           570: "liver_segments", 913: "lung_nodules", 591: "liver_lesions", 315: "pleural_pericard_effusion"}
TAREAS_MODELO = ("total", "body", "abdominal_muscles", "lung_vessels", "liver_vessels", "vertebrae_body",
                 "liver_segments", "lung_nodules", "liver_lesions", "pleural_pericard_effusion")
MODELOS_GB = 4.2
MOTOR_GB = {"gpu": 6.0, "cpu": 2.5}
MIN_DRIVER = 528.0  # PyTorch con CUDA 12.4 en Windows

OK, AVISO, ERROR = "ok", "aviso", "error"


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode, r.stdout.strip()
    except Exception as e:
        return -1, str(e)


def gpu_info():
    """Primera GPU NVIDIA: {'nombre', 'vram_gb', 'driver'} o None."""
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not os.path.exists(exe) and not shutil.which("nvidia-smi"):
        return None
    code, out = run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    if code or not out:
        return None
    name, mem, drv = [x.strip() for x in out.splitlines()[0].split(",")]
    try:
        return {"nombre": name, "vram_gb": float(mem) / 1024, "driver": drv, "driver_num": float(drv.split(".")[0] + "." + drv.split(".")[1])}
    except Exception:
        return {"nombre": name, "vram_gb": 0, "driver": drv, "driver_num": 0}


def gpu_usable(g=None):
    g = g if g is not None else gpu_info()
    return bool(g) and g["vram_gb"] >= 3.5 and g["driver_num"] >= MIN_DRIVER


def models_missing():
    base = os.path.join(config.MODELOS_DIR, "nnunet", "results")
    have = set()
    if os.path.isdir(base):
        for d in os.listdir(base):
            if d.startswith("Dataset") and d[7:10].isdigit():
                have.add(int(d[7:10]))
    return sorted({t for i, t in MODELOS.items() if i not in have}, key=TAREAS_MODELO.index)


def internet():
    for host in ("pypi.org", "github.com"):
        try:
            socket.create_connection((host, 443), timeout=4).close()
        except OSError:
            return False
    return True


def motor_status(cfg):
    """{'python', 'ok', 'cuda', 'version', 'detalle'} probando el motor de verdad."""
    py = cfg.motor_python()
    if not py:
        return {"python": None, "ok": False, "cuda": False, "detalle": "no instalado"}
    code, out = run([py, "-c", "import torch, totalsegmentator, importlib.metadata as m; "
                     "print(torch.__version__, torch.cuda.is_available(), m.version('TotalSegmentator'))"], timeout=120)
    if code:
        return {"python": py, "ok": False, "cuda": False, "detalle": "instalacion danada: " + out.splitlines()[-1][:200] if out else "no responde"}
    tv, cuda, ts = out.split()[-3:]
    return {"python": py, "ok": True, "cuda": cuda == "True", "version": f"TotalSegmentator {ts}, PyTorch {tv}",
            "detalle": ""}


def free_gb(path):
    p = path
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    return shutil.disk_usage(p or os.path.expanduser("~")).free / 1e9


def check_all(cfg):
    """Lista de comprobaciones: (nombre, estado, detalle)."""
    res = []
    add = lambda n, s, d: res.append({"nombre": n, "estado": s, "detalle": d})

    win = platform.system() == "Windows"
    bits64 = platform.machine().endswith("64")
    rel = platform.release()
    add("Sistema operativo", OK if win and bits64 and rel in ("10", "11") else (ERROR if not bits64 else AVISO),
        f"{platform.system()} {rel} {platform.machine()}" + ("" if bits64 else " - se necesita Windows de 64 bits"))

    cores = psutil.cpu_count() or 1
    add("Procesador", OK if cores >= 8 else (AVISO if cores >= 4 else ERROR),
        f"{cores} nucleos logicos" + ("" if cores >= 8 else " - minimo 4, recomendado 8 o mas"))

    ram = psutil.virtual_memory().total / 1024 ** 3
    add("Memoria RAM", OK if ram >= 15 else (AVISO if ram >= 7.5 else ERROR),
        f"{ram:.0f} GB" + ("" if ram >= 15 else " - minimo 8 GB, recomendado 16 GB (los modelos grandes pueden fallar)"))

    g = gpu_info()
    if not g:
        add("Tarjeta grafica NVIDIA", AVISO, "No se encontro. Se usara el procesador: cada estudio tardara 1-3 horas en vez de ~25 min")
    elif g["vram_gb"] < 3.5:
        add("Tarjeta grafica NVIDIA", AVISO, f"{g['nombre']} con {g['vram_gb']:.1f} GB: se necesitan 4 GB. Se usara el procesador")
    elif g["driver_num"] < MIN_DRIVER:
        add("Tarjeta grafica NVIDIA", AVISO, f"{g['nombre']}: el controlador {g['driver']} es antiguo (minimo {MIN_DRIVER:.0f}). Actualizalo o se usara el procesador")
    else:
        add("Tarjeta grafica NVIDIA", OK, f"{g['nombre']}, {g['vram_gb']:.0f} GB, controlador {g['driver']}")

    lib = cfg.library
    fl = free_gb(lib)
    add("Espacio para estudios", OK if fl >= 20 else (AVISO if fl >= 5 else ERROR),
        f"{fl:.0f} GB libres en {os.path.splitdrive(lib)[0] or lib} - cada estudio ocupa ~1-2 GB")
    try:
        t = os.path.join(lib, ".prueba_escritura")
        open(t, "w").close()
        os.remove(t)
        add("Permisos de la carpeta de estudios", OK, lib)
    except OSError as e:
        add("Permisos de la carpeta de estudios", ERROR, f"No se puede escribir en {lib}: {e}")

    m = motor_status(cfg)
    miss = models_missing()
    if m["ok"]:
        dev = "GPU" if m["cuda"] else "procesador"
        add("Motor de IA", OK, f"{m['version']} - usa {dev}")
        if not m["cuda"] and gpu_usable(g):
            res[-1]["estado"] = AVISO
            res[-1]["detalle"] += ". Hay una GPU NVIDIA compatible: reinstala el motor para aprovecharla"
    else:
        need = MOTOR_GB["gpu" if gpu_usable(g) else "cpu"] + MODELOS_GB + 3
        fm = free_gb(config.MOTOR_DIR)
        add("Motor de IA", ERROR, f"No instalado ({m['detalle']}). Pulsa 'Instalar motor de IA': descarga ~{need - 3:.0f} GB")
        add("Espacio para el motor", OK if fm >= need else ERROR, f"{fm:.0f} GB libres, se necesitan ~{need:.0f} GB durante la instalacion")
    if miss:
        add("Modelos de IA", AVISO if m["ok"] else ERROR,
            "Faltan: " + ", ".join(miss) + f". Se descargan al instalar el motor (~{MODELOS_GB:.1f} GB en total)")
    else:
        add("Modelos de IA", OK, "Todos descargados")
    if not m["ok"] or miss:
        net = internet()
        add("Conexion a internet", OK if net else ERROR, "Disponible" if net else "Sin conexion: hace falta para descargar el motor y los modelos")
    return res, m, g


def summary(res):
    if any(r["estado"] == ERROR for r in res):
        return ERROR
    return AVISO if any(r["estado"] == AVISO for r in res) else OK
