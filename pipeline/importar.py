"""Importa un estudio de TAC (zip o carpeta DICOM) y genera su reconstruccion 3D para el visor.

Uso:
  python importar.py Study-120443-PACIENTE.zip        importa un estudio nuevo
  python importar.py Study-...zip --serie "8 PORTAL"  fuerza una serie concreta
  python importar.py --rehacer 120443-PACIENTE        regenera mallas tras editar hallazgos.json
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

import numpy as np
import nibabel as nib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dicom_io
import segmentacion as seg
import mallas
import controles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDIES = os.environ.get("TAC3D_ESTUDIOS") or os.path.join(ROOT, "estudios")
TMP = os.environ.get("TAC3D_TMP") or os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", "/tmp")), "tac3d_tmp")

TASK_LABELS = {"total": "IA: organos, huesos y vasos", "body": "IA: contorno del cuerpo",
               "abdominal_muscles": "IA: musculos de la pared", "lung_vessels": "IA: vasos pulmonares",
               "liver_vessels": "IA: vasos del higado", "vertebrae_body": "IA: vertebras y discos",
               "liver_segments": "IA: segmentos del higado", "lung_nodules": "IA: nodulos y masas del pulmon",
               "liver_lesions": "IA: lesiones del higado", "pleural_pericard_effusion": "IA: liquido pleural y pericardico"}

HELP = ("Hallazgos del informe que se muestran en el visor. Cada entrada puede: (a) anotar una estructura existente "
        "con {'estructura': 'rinon_der', 'texto': '...'}; o (b) crear una estructura nueva desde un calculo detectado "
        "(ver trabajo/calculos.json y control/candidatos_calculos.png) con {'clave': 'calculo_ureter_izq', 'nombre': '...', "
        "'capa': 'Urinario', 'mascara': 'calculo:3', 'marcador': true, 'texto': '...'}, o desde una mascara .npy guardada "
        "en trabajo/ con 'mascara': 'liquido_renal_izq.npz'. Despues ejecuta: importar_estudio.bat --rehacer <id>")


def step(msg, key=None):
    """Mensaje de etapa. Las lineas @@ las lee la app de escritorio para mostrar el progreso."""
    if key:
        print(f"@@ETAPA {key}", flush=True)
    print(f"\n[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def study_id(path):
    stem = os.path.splitext(os.path.basename(os.path.normpath(path)))[0]
    stem = re.sub(r"^(study|estudio)[-_ ]*", "", stem, flags=re.I)
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "estudio"


def import_dicom(src, sdir, work, wanted):
    tmp = os.path.join(TMP, os.path.basename(sdir))
    try:
        step("Descomprimiendo y leyendo las series DICOM", "extraer")
        root = dicom_io.extract(src, tmp)
        series, docs = dicom_io.scan(root)
        print(f"  {'serie':<26}{'imagenes':>9}{'grosor':>8}  util")
        for s in series:
            print(f"  {s['carpeta'][:25]:<26}{s['n']:>9}{s['grosor']:>7.1f}   {'si' if s['util'] else '-'}")
        s = dicom_io.choose(series, wanted)
        print(f"  -> uso la serie '{s['carpeta']}' ({s['n']} cortes de {s['grosor']} mm)")
        shape, vs = dicom_io.to_nifti(s, os.path.join(work, "ct.nii.gz"))
        print(f"  volumen {shape}, voxel {vs} mm")
        info = dicom_io.metadata(s)
        info["informes"] = dicom_io.save_reports(docs, sdir)
        if info["informes"]:
            print("  informe del radiologo guardado:", ", ".join(info["informes"]))
        json.dump(info, open(os.path.join(sdir, "estudio.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    finally:
        if os.path.isdir(tmp) and not os.path.isdir(src):
            shutil.rmtree(tmp, ignore_errors=True)


def viewer_header(info, n=None):
    sexo = {"M": "M", "F": "F"}.get(info.get("sexo"), "")
    parts = [f"Paciente {sexo}, {info['edad']} anos" if info.get("edad") else "", info.get("fecha", ""),
             f"serie {info['serie']}" if info.get("serie") else "", f"{n} estructuras" if n else ""]
    return {"titulo": info.get("estudio") or "TAC", "subtitulo": " - ".join(p for p in parts if p),
            "paciente": info.get("paciente", ""), "sexo": info.get("sexo", "")}


def update_index():
    items = []
    for f in sorted(glob.glob(os.path.join(STUDIES, "*", "web", "manifest.json"))):
        m = json.load(open(f, encoding="utf-8"))
        sid = os.path.basename(os.path.dirname(os.path.dirname(f)))
        e = m.get("estudio", {})
        items.append({"id": sid, "titulo": e.get("titulo", sid), "subtitulo": e.get("subtitulo", ""),
                      "paciente": e.get("paciente", "")})
    json.dump(items, open(os.path.join(STUDIES, "estudios.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    try:
        run()
    except SystemExit as e:
        if e.code not in (None, 0):
            print(f"@@ERROR {e.code}", flush=True)
        raise
    except Exception as e:
        print(f"@@ERROR {type(e).__name__}: {e}", flush=True)
        raise
    print("@@FIN", flush=True)


def run():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", nargs="?", help="zip del estudio o carpeta con los DICOM")
    ap.add_argument("--rehacer", metavar="ID", help="regenerar mallas e imagenes de un estudio ya importado")
    ap.add_argument("--serie", help="numero, carpeta o descripcion de la serie a usar")
    ap.add_argument("--id", help="nombre de la carpeta del estudio")
    ap.add_argument("--forzar", action="store_true", help="repetir todos los pasos aunque ya existan")
    a = ap.parse_args()
    if not a.entrada and not a.rehacer:
        ap.error("indica el zip del estudio o --rehacer ID")

    sid = a.rehacer or a.id or study_id(a.entrada)
    sdir = os.path.join(STUDIES, sid)
    work = os.path.join(sdir, "trabajo")
    ctrl = os.path.join(sdir, "control")
    for d in (work, ctrl):
        os.makedirs(d, exist_ok=True)
    print(f"Estudio: {sid}\nCarpeta: {sdir}")

    ct_path = os.path.join(work, "ct.nii.gz")
    do_import = bool(a.entrada) and (a.forzar or a.serie or not os.path.exists(ct_path))
    if do_import:
        a.forzar = True
    elif not os.path.exists(ct_path):
        raise SystemExit("Este estudio no se ha importado todavia: pasa el zip como entrada")
    tasks = [t for t in ("total", "body") + seg.EXTRA_TASKS
             if a.forzar or not os.path.exists(os.path.join(work, f"seg_{t}.nii.gz"))]
    plan = (["extraer"] if do_import else []) + [f"seg_{t}" for t in tasks] + ["calculos", "mallas", "control"]
    print("@@PLAN " + ",".join(plan), flush=True)
    print("@@DISPOSITIVO " + seg.device(), flush=True)

    if do_import:
        import_dicom(os.path.abspath(a.entrada), sdir, work, a.serie)
    print("@@VOXELES %d" % int(np.prod(nib.load(ct_path).shape)), flush=True)

    for task in tasks:
        out = os.path.join(work, f"seg_{task}.nii.gz")
        if not seg.task_applies(work, task):
            print(f"  se omite '{task}': el organo que analiza no aparece en este estudio")
            if os.path.exists(out):
                os.remove(out)
            plan.remove(f"seg_{task}")
            print("@@PLAN " + ",".join(plan), flush=True)
            continue
        step(f"{TASK_LABELS.get(task, task)} (TotalSegmentator, tarea '{task}')", f"seg_{task}")
        if task in ("total", "body"):
            seg.run_totalseg(ct_path, out, task)
        else:
            try:
                seg.run_totalseg(ct_path, out, task)
            except SystemExit as e:
                print("  aviso: se omite este modelo:", e)

    step("Calculando piel, pared muscular, contenido interno y focos de densidad calcica", "calculos")
    img, ct, tot, body = seg.load(work)
    extra = seg.load_extra(work)
    sp = nib.affines.voxel_sizes(img.affine)
    tot, removed = seg.refine_bowel(ct, tot, sp)
    print(f"  grasa del mesenterio retirada de entre las asas: {removed * np.prod(sp) / 1000:.1f} cm3")
    body_m, wall, body_dist = seg.derived_masks(ct, tot, body, sp, extra.get("abdominal_muscles", 0) > 0)
    interior = seg.internal_masks(ct, tot, sp)
    for k, m in interior.items():
        print(f"  interior {k:<24} {m.sum() * np.prod(sp) / 1000:8.1f} cm3")
    contrast = seg.has_contrast(ct, tot)
    print("  estudio", "CON contraste" if contrast else "SIN contraste")
    if not contrast and extra.pop("liver_vessels", None) is not None:
        print("  se omiten los vasos hepaticos: ese modelo necesita contraste y sin el da falsas lesiones")
    density = seg.density_findings(ct, tot, sp, extra)
    print(f"  {len(density)} estructuras de otra densidad dentro de los organos:")
    for d in density:
        print(f"   {d['key']:<28} {d['mask'].sum() * np.prod(sp) / 1000:8.1f} cm3")
    stones, stone_list = np.zeros(ct.shape, np.uint16), None
    if not contrast:
        stones, stone_list = seg.find_stones(ct, tot, body_dist, img.affine)
        np.savez_compressed(os.path.join(work, "calculos.npz"), etiquetas=stones)
        json.dump(stone_list, open(os.path.join(work, "calculos.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  {len(stone_list)} focos candidatos a calculo:")
        for c in stone_list:
            print(f"   #{c['id']:<3} {c['zona']:<8} {c['lado']}  {c['diametro_mm']:>5} mm  {c['hu_max']:>5} HU  corte {c['corte']}")
        for side, (fl, v, kv) in seg.renal_fluid(ct, tot, sp).items():
            np.savez_compressed(os.path.join(work, f"liquido_renal_{side}.npz"), mascara=fl)
            print(f"  rinon {side}: {kv} cm3, liquido en pelvis y calices {v} cm3 (trabajo/liquido_renal_{side}.npz)")

    hpath = os.path.join(sdir, "hallazgos.json")
    if not os.path.exists(hpath):
        json.dump({"_ayuda": HELP, "hallazgos": []}, open(hpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    findings = json.load(open(hpath, encoding="utf-8")).get("hallazgos", [])

    step("Generando modelos 3D", "mallas")
    epath = os.path.join(sdir, "estudio.json")
    info = json.load(open(epath, encoding="utf-8")) if os.path.exists(epath) else {}
    n = mallas.build(sdir, img, ct, tot, {"body": body_m, "wall": wall, "extra": extra, "interior": interior,
                                          "densidad": density}, stones, stone_list, findings, {})
    mf = os.path.join(sdir, "web", "manifest.json")
    m = json.load(open(mf, encoding="utf-8"))
    m["estudio"] = viewer_header(info, n)
    m["generado"] = int(time.time())
    json.dump(m, open(mf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    step("Imagenes de control", "control")
    controles.overview(ctrl, ct, tot, body_m, wall, sp)
    if stone_list:
        controles.zoom_tiles(os.path.join(ctrl, "candidatos_calculos.png"), ct,
                             [(stones == c["id"], f"#{c['id']} {c['zona']} {c['lado']}\n{c['diametro_mm']} mm  {c['hu_max']} HU"
                               f"\ncorte {c['corte']}") for c in stone_list], sp)
    for f in glob.glob(os.path.join(ctrl, "zonas_densidad*.png")):
        os.remove(f)
    for name, window, sel in (("zonas_densidad_pulmon.png", (-500, 1500), lambda d: "pulmon" in d["key"]),
                              ("zonas_densidad.png", (60, 400), lambda d: "pulmon" not in d["key"])):
        tiles = [(m, f"{d['name'][:40]}\n{m.sum() * np.prod(sp) / 1000:.1f} cm3  {np.median(ct[m]):.0f} HU")
                 for d in density if sel(d) and d["kind"] != "aire" for m in seg.largest_components(d["mask"], 4)]
        controles.zoom_tiles(os.path.join(ctrl, name), ct, tiles, sp, window=window)
    items = []
    for h in findings:
        src = h.get("mascara", "")
        if src.startswith("calculo:"):
            items.append((np.isin(stones, [int(x) for x in src[8:].split(",")]), h["nombre"][:40]))
        elif src.endswith((".npy", ".npz")):
            items.append((mallas.load_mask(os.path.join(work, src)), h["nombre"][:40]))
    controles.zoom_tiles(os.path.join(ctrl, "hallazgos.png"), ct, [i for i in items if i[0].any()], sp)

    update_index()
    print(f"\nListo: {n} estructuras. Abre el visor y elige el estudio '{sid}'.")


if __name__ == "__main__":
    main()
