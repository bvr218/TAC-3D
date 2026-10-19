import os
import re
import zipfile
import numpy as np
import nibabel as nib
import pydicom

PHASE_PREF = ("PORTAL", "VENOS", "NEFRO", "SIMPLE", "SIN CONTRASTE", "ARTER", "TARDIA", "EXCRET")


def extract(path, dest):
    if os.path.isdir(path):
        return path
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(dest)
    return dest


def _normal(d):
    iop = np.array(d.ImageOrientationPatient, float)
    return np.cross(iop[:3], iop[3:])


def scan(root):
    """Agrupa todos los archivos DICOM por serie. Devuelve series de imagen y documentos (informes PDF)."""
    series, docs = {}, []
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                d = pydicom.dcmread(p, stop_before_pixels=True)
            except Exception:
                continue
            if "EncapsulatedDocument" in d or getattr(d, "Modality", "") == "DOC":
                docs.append(p)
                continue
            uid = getattr(d, "SeriesInstanceUID", None)
            if uid is None:
                continue
            series.setdefault(uid, {"uid": uid, "files": [], "hdr": d})["files"].append(p)

    out = []
    for s in series.values():
        d = s["hdr"]
        s["modalidad"] = str(getattr(d, "Modality", ""))
        s["descripcion"] = str(getattr(d, "SeriesDescription", "")).strip()
        s["numero"] = int(getattr(d, "SeriesNumber", 0) or 0)
        s["n"] = len(s["files"])
        s["grosor"] = float(getattr(d, "SliceThickness", 0) or 0)
        s["tipo"] = [str(x).upper() for x in getattr(d, "ImageType", [])]
        s["carpeta"] = os.path.basename(os.path.dirname(s["files"][0]))
        axial = "ImageOrientationPatient" in d and abs(_normal(d)[2]) > 0.8
        s["util"] = (s["modalidad"] == "CT" and axial and s["n"] >= 30 and "LOCALIZER" not in s["tipo"])
        out.append(s)
    out.sort(key=lambda s: s["numero"])
    return out, docs


def choose(series, wanted=None):
    if wanted:
        for s in series:
            if wanted in (s["uid"], str(s["numero"]), s["carpeta"], s["descripcion"]):
                return s
        raise SystemExit(f"No encontre la serie '{wanted}'")
    cand = [s for s in series if s["util"]]
    if not cand:
        raise SystemExit("El estudio no tiene ninguna serie axial de TAC utilizable")

    def phase(s):
        txt = (s["descripcion"] + " " + s["carpeta"]).upper()
        return next((i for i, k in enumerate(PHASE_PREF) if k in txt), len(PHASE_PREF))

    # original antes que reconstruida, cortes finos (hasta 1 mm), fase preferida, mas cortes
    return min(cand, key=lambda s: ("ORIGINAL" not in s["tipo"], max(s["grosor"], 1.0), phase(s), -s["n"]))


def to_nifti(s, out):
    slices = [pydicom.dcmread(f) for f in s["files"]]
    n0 = _normal(slices[0])
    slices = [d for d in slices if np.allclose(_normal(d), n0, atol=1e-3) and hasattr(d, "ImagePositionPatient")]
    slices.sort(key=lambda d: float(np.dot(d.ImagePositionPatient, n0)))
    pos = np.array([np.dot(d.ImagePositionPatient, n0) for d in slices])
    keep = np.r_[True, np.diff(pos) > 1e-3]
    slices = [d for d, k in zip(slices, keep) if k]
    gaps = np.diff(pos[keep])
    if gaps.std() > 0.05 * gaps.mean():
        print(f"  aviso: separacion entre cortes irregular ({gaps.min():.2f}-{gaps.max():.2f} mm)")

    vol = np.stack([d.pixel_array.astype(np.float32) * float(getattr(d, "RescaleSlope", 1))
                    + float(getattr(d, "RescaleIntercept", 0)) for d in slices], axis=-1)
    vol = np.transpose(vol, (1, 0, 2))  # (cols=x, rows=y, z)

    d0 = slices[0]
    row_cos = np.array(d0.ImageOrientationPatient[:3], float)
    col_cos = np.array(d0.ImageOrientationPatient[3:], float)
    ps = [float(v) for v in d0.PixelSpacing]
    p0 = np.array(d0.ImagePositionPatient, float)
    p1 = np.array(slices[-1].ImagePositionPatient, float)
    affine = np.eye(4)
    affine[:3, 0] = row_cos * ps[1]
    affine[:3, 1] = col_cos * ps[0]
    affine[:3, 2] = (p1 - p0) / (len(slices) - 1)
    affine[:3, 3] = p0
    affine = np.diag([-1, -1, 1, 1]) @ affine  # LPS -> RAS
    nib.save(nib.Nifti1Image(np.clip(vol, -1024, 32767).astype(np.int16), affine), out)
    return vol.shape, np.round(nib.affines.voxel_sizes(affine), 3)


def metadata(s):
    d = s["hdr"]
    g = lambda k: str(getattr(d, k, "") or "").strip()
    name = g("PatientName").replace("^", " ").strip()
    date = g("StudyDate")
    age = g("PatientAge")
    return {
        "paciente": re.sub(r"\s+", " ", name),
        "sexo": g("PatientSex"),
        "edad": str(int(age[:3])) if age[:3].isdigit() else age,
        "fecha": f"{date[6:8]}/{date[4:6]}/{date[:4]}" if len(date) == 8 else date,
        "estudio": g("StudyDescription"),
        "serie": s["descripcion"] or s["carpeta"],
        "institucion": g("InstitutionName"),
        "equipo": g("Manufacturer"),
        "cortes": s["n"],
        "grosor_mm": s["grosor"],
        "uid_estudio": g("StudyInstanceUID"),
        "uid_serie": s["uid"],
    }


def save_reports(docs, dest):
    """Guarda los informes PDF incrustados en el estudio y extrae su texto."""
    saved = []
    for i, p in enumerate(docs):
        d = pydicom.dcmread(p)
        if "EncapsulatedDocument" not in d:
            continue
        name = "informe.pdf" if i == 0 else f"informe_{i + 1}.pdf"
        pdf = os.path.join(dest, name)
        data = bytes(d.EncapsulatedDocument)
        open(pdf, "wb").write(data[:data.rfind(b"%%EOF") + 5] if b"%%EOF" in data else data)
        try:
            from pypdf import PdfReader
            txt = "\n".join(pg.extract_text() or "" for pg in PdfReader(pdf).pages)
            open(pdf[:-4] + ".txt", "w", encoding="utf-8").write(txt)
        except Exception as e:
            print("  no pude leer el texto del informe:", e)
        saved.append(name)
    return saved
