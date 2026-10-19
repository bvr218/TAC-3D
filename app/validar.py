"""Validacion de un estudio (zip o carpeta DICOM) antes de procesarlo.

Lee solo las cabeceras DICOM (sin pixeles ni descomprimir a disco), agrupa por serie, elige la serie
igual que el pipeline y revisa todo lo que puede hacer fallar o empeorar la reconstruccion.
"""
import json
import os
import re
import shutil
import zipfile

import numpy as np
import pydicom
from pydicom.errors import InvalidDicomError

OK, AVISO, ERROR, INFO = "ok", "aviso", "error", "info"

PHASE_PREF = ("PORTAL", "VENOS", "NEFRO", "SIMPLE", "SIN CONTRASTE", "ARTER", "TARDIA", "EXCRET")
SUPPORTED_TS = {
    "1.2.840.10008.1.2": "sin comprimir", "1.2.840.10008.1.2.1": "sin comprimir",
    "1.2.840.10008.1.2.2": "sin comprimir (big endian)", "1.2.840.10008.1.2.1.99": "deflate",
    "1.2.840.10008.1.2.5": "RLE", "1.2.840.10008.1.2.4.50": "JPEG", "1.2.840.10008.1.2.4.51": "JPEG",
    "1.2.840.10008.1.2.4.57": "JPEG sin perdida", "1.2.840.10008.1.2.4.70": "JPEG sin perdida",
    "1.2.840.10008.1.2.4.80": "JPEG-LS", "1.2.840.10008.1.2.4.81": "JPEG-LS",
    "1.2.840.10008.1.2.4.90": "JPEG 2000", "1.2.840.10008.1.2.4.91": "JPEG 2000",
}
SKIP_EXT = (".txt", ".xml", ".html", ".htm", ".exe", ".dll", ".inf", ".ini", ".js", ".css", ".png", ".jpg",
            ".jpeg", ".gif", ".bmp", ".pdf", ".ico", ".json", ".bat", ".cmd", ".chm", ".cab", ".msi", ".zip")
ABDOMEN = ("ABD", "PELV", "URO", "RENAL", "RINON", "COLIC", "HIGADO", "HEPAT", "TAP", "TORAX-ABD", "BODY", "CUERPO")
TORAX = ("TORAX", "CHEST", "THORAX", "PULMON", "LUNG")
DOC_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


class Cancelado(Exception):
    pass


def study_id(path):
    stem = os.path.basename(os.path.normpath(path))
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    stem = re.sub(r"^(study|estudio)[-_ ]*", "", stem, flags=re.I)
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "estudio"


def _sources(path):
    """(nombre, abrir(), bytes) de cada archivo candidato, y el tamano total sin comprimir."""
    if os.path.isdir(path):
        items = []
        for dp, _, fs in os.walk(path):
            for f in fs:
                p = os.path.join(dp, f)
                items.append((os.path.relpath(p, path), (lambda p=p: open(p, "rb")), os.path.getsize(p)))
        return items, sum(i[2] for i in items), None
    z = zipfile.ZipFile(path)
    infos = [i for i in z.infolist() if not i.is_dir()]
    if any(i.flag_bits & 0x1 for i in infos):
        raise ValueError("El zip esta protegido con contrasena")
    return [(i.filename, (lambda i=i: z.open(i)), i.file_size) for i in infos], sum(i.file_size for i in infos), z


def _normal(iop):
    iop = np.array(iop, float)
    return np.cross(iop[:3], iop[3:])


def choose(series):
    cand = [s for s in series if s["util"]]
    if not cand:
        return None

    def phase(s):
        txt = (s["descripcion"] + " " + s["carpeta"]).upper()
        return next((i for i, k in enumerate(PHASE_PREF) if k in txt), len(PHASE_PREF))
    return min(cand, key=lambda s: ("ORIGINAL" not in s["tipo"], max(s["grosor"], 1.0), phase(s), -s["n"]))


def validate(path, library, progress=None, cancelled=lambda: False):
    """Devuelve {'checks': [...], 'series': [...], 'elegida': uid, 'info': {...}, 'ok': bool, ...}."""
    out = {"checks": [], "series": [], "elegida": None, "info": {}, "ok": False, "ruta": path,
           "id": study_id(path), "voxeles": 0}
    add = lambda n, s, d="": out["checks"].append({"nombre": n, "estado": s, "detalle": d})

    if not os.path.exists(path):
        add("Archivo", ERROR, "No existe: " + path)
        return out
    if os.path.isfile(path) and not zipfile.is_zipfile(path):
        add("Formato", ERROR, "Solo se aceptan archivos .zip o carpetas con imagenes DICOM")
        return out
    try:
        items, total_bytes, zf = _sources(path)
    except (zipfile.BadZipFile, ValueError, OSError) as e:
        add("Archivo legible", ERROR, f"No se pudo abrir: {e}")
        return out
    out["bytes"] = total_bytes
    add("Archivo legible", OK, f"{len(items)} archivos, {total_bytes / 1e6:.0f} MB sin comprimir")

    series, docs, bad, unreadable, studies = {}, 0, 0, 0, {}
    for k, (name, opener, size) in enumerate(items):
        if cancelled():
            raise Cancelado()
        if progress and k % 25 == 0:
            progress(k, len(items))
        low = name.lower()
        if low.endswith(SKIP_EXT) or os.path.basename(low) == "dicomdir" or size < 256:
            bad += 1
            continue
        try:
            with opener() as fh:
                d = pydicom.dcmread(fh, stop_before_pixels=True)
        except (InvalidDicomError, EOFError):
            bad += 1
            continue
        except Exception:
            unreadable += 1
            continue
        sop = str(getattr(d, "SOPClassUID", ""))
        if sop == DOC_SOP or getattr(d, "Modality", "") == "DOC":
            docs += 1
            continue
        uid = getattr(d, "SeriesInstanceUID", None)
        if uid is None:
            bad += 1
            continue
        studies.setdefault(str(getattr(d, "StudyInstanceUID", "")), d)
        s = series.get(uid)
        if s is None:
            ts = str(getattr(getattr(d, "file_meta", None), "TransferSyntaxUID", "") or "1.2.840.10008.1.2")
            s = series[uid] = {
                "uid": uid, "hdr": d, "n": 0, "pos": [], "dims": set(), "ts": ts,
                "modalidad": str(getattr(d, "Modality", "")),
                "descripcion": str(getattr(d, "SeriesDescription", "")).strip(),
                "numero": int(getattr(d, "SeriesNumber", 0) or 0),
                "grosor": float(getattr(d, "SliceThickness", 0) or 0),
                "tipo": [str(x).upper() for x in getattr(d, "ImageType", [])],
                "carpeta": os.path.basename(os.path.dirname(name.replace("\\", "/"))) or "(raiz)",
                "iop": list(getattr(d, "ImageOrientationPatient", []) or []),
                "spacing": [float(x) for x in getattr(d, "PixelSpacing", []) or []],
            }
        s["n"] += 1
        s["dims"].add((int(getattr(d, "Rows", 0) or 0), int(getattr(d, "Columns", 0) or 0)))
        if s["iop"] and hasattr(d, "ImagePositionPatient"):
            s["pos"].append(float(np.dot(np.array(d.ImagePositionPatient, float), _normal(s["iop"]))))
    if progress:
        progress(len(items), len(items))

    n_dicom = sum(s["n"] for s in series.values())
    if not series:
        add("Imagenes DICOM", ERROR, "No se encontro ninguna imagen DICOM" + (f" ({unreadable} archivos danados)" if unreadable else ""))
        return out
    det = f"{n_dicom} imagenes en {len(series)} series"
    if bad:
        det += f"; {bad} archivos que no son imagenes se ignoran"
    add("Imagenes DICOM", OK, det)
    if unreadable:
        add("Archivos danados", AVISO, f"{unreadable} archivos DICOM no se pudieron leer y se ignoran")

    for s in series.values():
        axial = len(s["iop"]) == 6 and abs(_normal(s["iop"])[2]) > 0.8
        s["util"] = s["modalidad"] == "CT" and axial and s["n"] >= 30 and "LOCALIZER" not in s["tipo"]
    mods = sorted({s["modalidad"] for s in series.values()})
    if "CT" not in mods:
        add("Tomografia (TC)", ERROR, "El estudio no es una tomografia: modalidad " + ", ".join(mods)
            + ". Este programa solo reconstruye TAC")
        return out
    add("Tomografia (TC)", OK, "Modalidad CT")
    if len(studies) > 1:
        add("Un solo estudio", AVISO, f"El archivo contiene {len(studies)} estudios distintos; se usara uno solo")

    ordered = sorted(series.values(), key=lambda s: s["numero"])
    out["series"] = [{"uid": s["uid"], "numero": s["numero"], "descripcion": s["descripcion"] or s["carpeta"],
                      "n": s["n"], "grosor": s["grosor"], "util": s["util"], "modalidad": s["modalidad"]}
                     for s in ordered]
    best = choose(ordered)
    if not best:
        add("Serie axial utilizable", ERROR, "No hay ninguna serie axial de TAC con al menos 30 cortes")
        return out
    out["elegida"] = best["uid"]
    out["_series"] = {s["uid"]: s for s in ordered}
    for c in check_series(best):
        out["checks"].append(c)

    d = best["hdr"]
    g = lambda k: str(getattr(d, k, "") or "").strip()
    date = g("StudyDate")
    age = g("PatientAge")
    out["info"] = {"paciente": re.sub(r"\s+", " ", g("PatientName").replace("^", " ")).strip(),
                   "sexo": g("PatientSex"), "edad": str(int(age[:3])) if age[:3].isdigit() else age,
                   "fecha": f"{date[6:8]}/{date[4:6]}/{date[:4]}" if len(date) == 8 else date,
                   "estudio": g("StudyDescription"), "institucion": g("InstitutionName"),
                   "uid_estudio": g("StudyInstanceUID")}

    txt = (g("StudyDescription") + " " + g("BodyPartExamined") + " " + " ".join(s["descripcion"] for s in ordered)).upper()
    if any(k in txt for k in ABDOMEN):
        add("Zona del cuerpo", OK, "Abdomen / pelvis")
    elif any(k in txt for k in TORAX):
        add("Zona del cuerpo", AVISO, "Parece solo de torax: los organos abdominales saldran incompletos")
    else:
        add("Zona del cuerpo", AVISO, f"No reconozco la zona ('{g('StudyDescription') or g('BodyPartExamined') or 'sin descripcion'}'). "
            "El programa esta pensado para torax, abdomen y pelvis")

    add("Informe del radiologo", INFO if docs else INFO, "Incluido (PDF): se guardara con el estudio" if docs else "No viene incluido en el archivo")

    # espacio: descompresion temporal + volumen + segmentaciones + mallas
    need = total_bytes * 1.1 / 1e9 + 1.5
    free = shutil.disk_usage(_existing(library)).free / 1e9
    add("Espacio en disco", OK if free >= need + 2 else (AVISO if free >= need else ERROR),
        f"{free:.0f} GB libres, el estudio necesita ~{need:.1f} GB")

    dup = find_duplicate(library, out["id"], out["info"]["uid_estudio"])
    if dup:
        add("Estudio repetido", AVISO, f"Ya existe en la biblioteca como '{dup}'. Si continuas se reemplazara")
        out["duplicado"] = dup

    out["voxeles"] = voxels(best)
    out["ok"] = not any(c["estado"] == ERROR for c in out["checks"])
    return out


def voxels(s):
    rows, cols = max(s["dims"]) if s["dims"] else (512, 512)
    return rows * cols * s["n"]


def check_series(s):
    res = []
    add = lambda n, st, d="": res.append({"nombre": n, "estado": st, "detalle": d})
    name = s["descripcion"] or s["carpeta"]
    add("Serie elegida", OK, f"#{s['numero']} {name}: {s['n']} cortes de {s['grosor']:g} mm")

    if s["ts"] not in SUPPORTED_TS:
        add("Compresion de las imagenes", ERROR, f"Formato de compresion no soportado ({s['ts']})")
    else:
        t = SUPPORTED_TS[s["ts"]]
        add("Compresion de las imagenes", OK, t[0].upper() + t[1:])

    if len(s["dims"]) > 1:
        add("Tamano de imagen", ERROR, "Los cortes tienen tamanos distintos: " + ", ".join(f"{r}x{c}" for r, c in sorted(s["dims"])))
    else:
        r, c = next(iter(s["dims"]))
        add("Tamano de imagen", OK if r >= 256 else AVISO, f"{r} x {c} pixeles" + ("" if r >= 256 else " - resolucion baja"))

    t = s["grosor"]
    if t <= 0:
        add("Grosor de corte", AVISO, "No indicado en el DICOM")
    elif t <= 1.5:
        add("Grosor de corte", OK, f"{t:g} mm - precision alta")
    elif t <= 3.0:
        add("Grosor de corte", OK, f"{t:g} mm - precision buena")
    elif t <= 5.0:
        add("Grosor de corte", AVISO, f"{t:g} mm - cortes gruesos: estructuras pequenas (calculos, vasos finos) pueden no verse")
    else:
        add("Grosor de corte", ERROR, f"{t:g} mm - demasiado grueso para una reconstruccion 3D util (maximo 5 mm)")

    sp = s["spacing"]
    if len(sp) != 2:
        add("Tamano de pixel", ERROR, "Falta PixelSpacing: no se puede medir")
    elif not (0.2 <= sp[0] <= 2.0):
        add("Tamano de pixel", AVISO, f"{sp[0]:.2f} mm - valor inusual")

    pos = np.unique(np.round(np.array(s["pos"]), 3))
    if len(pos) < 30:
        add("Posicion de los cortes", ERROR, "Faltan posiciones de los cortes (ImagePositionPatient)")
    else:
        gaps = np.diff(pos)
        med = float(np.median(gaps))
        holes = int((gaps > 1.6 * med).sum())
        dup = s["n"] - len(pos)
        length = float(pos[-1] - pos[0])
        d = f"cada {med:.2f} mm, cubre {length / 10:.0f} cm"
        if holes:
            add("Continuidad de los cortes", AVISO, f"{holes} saltos en la serie ({d}): la reconstruccion tendra huecos")
        else:
            add("Continuidad de los cortes", OK, d + (f"; {dup} cortes repetidos se ignoran" if dup else ""))
        if length < 120:
            add("Longitud explorada", AVISO, f"Solo {length / 10:.0f} cm: muchas estructuras quedaran cortadas")
    return res


def _existing(p):
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    return p or os.path.expanduser("~")


def find_duplicate(library, sid, uid):
    if os.path.isdir(os.path.join(library, sid)):
        return sid
    if not uid:
        return None
    for d in os.listdir(library) if os.path.isdir(library) else []:
        try:
            if json.load(open(os.path.join(library, d, "estudio.json"), encoding="utf-8")).get("uid_estudio") == uid:
                return d
        except Exception:
            pass
    return None


def series_report(result, uid):
    """Revalida otra serie elegida a mano."""
    s = result["_series"][uid]
    return check_series(s), voxels(s)
