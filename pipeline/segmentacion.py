import os
import subprocess
import sys
import numpy as np
import nibabel as nib
from scipy import ndimage as ndi
from totalsegmentator.map_to_binary import class_map

NAMES = class_map["total"]
INV = {v: k for k, v in NAMES.items()}

# modelos adicionales de TotalSegmentator sin licencia: estructuras dentro de los organos
EXTRA_TASKS = ("abdominal_muscles", "lung_vessels", "liver_vessels", "vertebrae_body", "liver_segments",
               "lung_nodules", "liver_lesions", "pleural_pericard_effusion")
# organo que debe verse en el estudio para que el modelo tenga sentido (recortan la imagen a ese organo)
TASK_ORGAN = {"lung_vessels": "lung_", "lung_nodules": "lung_", "pleural_pericard_effusion": "lung_",
              "liver_vessels": "liver", "liver_segments": "liver", "liver_lesions": "liver"}
BONE_PREFIXES = ("vertebrae", "rib", "hip", "femur", "sacrum", "sternum", "humerus", "scapula", "clavicula", "skull")


def ids(*prefixes):
    return [i for k, i in INV.items() if k.startswith(prefixes)]


def task_applies(work, task, min_cm3=30):
    """False si el organo que necesita el modelo no aparece (o casi) en el estudio."""
    organ = TASK_ORGAN.get(task)
    p = os.path.join(work, "seg_total.nii.gz")
    if not organ or not os.path.exists(p):
        return True
    img = nib.load(p)
    tot = np.asarray(img.dataobj)
    return bool(np.isin(tot, ids(organ)).sum() * float(np.prod(nib.affines.voxel_sizes(img.affine))) / 1000 >= min_cm3)


def device():
    """'gpu' o 'cpu'. TAC3D_DEVICE (auto/gpu/cpu) lo fija la app de escritorio."""
    want = os.environ.get("TAC3D_DEVICE", "auto").lower()
    if want in ("gpu", "cpu"):
        return want
    try:
        import torch
        return "gpu" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def run_totalseg(ct_path, out_path, task):
    exe = os.path.join(os.path.dirname(sys.executable), "TotalSegmentator.exe")
    if not os.path.exists(exe):
        exe = os.path.join(os.path.dirname(sys.executable), "TotalSegmentator")
    cmd = [exe, "-i", ct_path, "-o", out_path, "--ml", "-ta", task]
    cpu = device() == "cpu"
    if cpu:
        cmd += ["--device", "cpu"]
    if task not in EXTRA_TASKS:
        r = subprocess.run(cmd)
    else:
        # alta resolucion: en 3 trozos para no agotar una GPU de 4 GB; sin dividir si la zona es corta; CPU al final
        tries = ((["--force_split"], None), ([], "reintento sin dividir el volumen"))
        if not cpu:
            tries += ((["--device", "cpu"], "reintento en CPU (mas lento)"),)
        for extra_args, msg in tries:
            if msg:
                print("  " + msg)
            r = subprocess.run(cmd + extra_args)
            if os.path.exists(out_path):
                break
    if r.returncode or not os.path.exists(out_path):
        raise SystemExit(f"TotalSegmentator fallo en la tarea '{task}'")


def load(work):
    img = nib.load(os.path.join(work, "ct.nii.gz"))
    ct = np.asarray(img.dataobj).astype(np.int16)
    tot = np.asarray(nib.load(os.path.join(work, "seg_total.nii.gz")).dataobj).astype(np.uint8)
    body = np.asarray(nib.load(os.path.join(work, "seg_body.nii.gz")).dataobj).astype(np.uint8)
    return img, ct, tot, body


def load_extra(work):
    out = {}
    for t in EXTRA_TASKS:
        p = os.path.join(work, f"seg_{t}.nii.gz")
        if os.path.exists(p):
            out[t] = np.asarray(nib.load(p).dataobj).astype(np.uint8)
    return out


def extra_mask(extra, task, names):
    if task not in extra:
        return None
    inv = {v: k for k, v in class_map[task].items()}
    return np.isin(extra[task], [inv[n] for n in names if n in inv])


def internal_masks(ct, tot, sp):
    """Contenido interno de los organos a partir de la densidad (HU) dentro de cada mascara de la IA."""
    out = {}
    clean = lambda m: ndi.binary_opening(m, structure=np.ones((2, 2, 1)))
    gi = np.isin(tot, [INV[k] for k in ("small_bowel", "colon", "duodenum", "stomach")])
    if gi.any():
        depth = edt_per_slice(gi, sp)
        colon = tot == INV["colon"]
        lumen = gi & (depth > 2.5) & (ct >= -400) & (ct < 150)
        out["pared_digestiva"] = gi & (depth <= 2.5) & (ct >= -30) & (ct < 150)
        out["gas_digestivo"] = clean(gi & (ct < -400))
        out["contraste_oral"] = clean(gi & (ct >= 150))
        out["contenido_colon"] = clean(lumen & colon)
        out["contenido_intestinal"] = clean(lumen & ~colon)
    bones = np.isin(tot, ids(*BONE_PREFIXES))
    if bones.any():
        inner = ndi.binary_erosion(bones, structure=np.ones((3, 3, 1)))
        out["medula_osea"] = clean(inner & (ct > -100) & (ct < 300))
    gb = tot == INV["gallbladder"]
    if gb.any():
        # calculos: calcificados (>=130 HU) o claramente distintos de la bilis; los de colesterol pueden ser
        # isodensos y no verse en la TAC. Se mira lejos de la pared para evitar el efecto de volumen parcial.
        inner = ndi.binary_erosion(gb, structure=np.ones((3, 3, 1)), iterations=2)
        bile = float(np.median(ct[inner] if inner.any() else ct[gb]))
        near_fat = ndi.binary_dilation(~gb & (ct < -30), structure=np.ones((9, 9, 1)))
        odd = inner & ((ct >= max(bile + 45, 60)) | ((ct <= bile - 45) & ~near_fat))
        odd = ndi.binary_opening(odd, structure=np.ones((2, 2, 1)))
        stones = odd | (gb & (ct >= 130))
        out["calculos_vesiculares"] = stones
        lumen = ndi.binary_erosion(gb, structure=np.ones((3, 3, 1)), iterations=max(1, round(1.5 / sp[0])))
        out["bilis"] = lumen & ~ndi.binary_dilation(stones, iterations=1)
    from skimage.morphology import convex_hull_image
    for side, key in (("der", "kidney_right"), ("izq", "kidney_left")):
        k = tot == INV[key]
        if k.sum() < 500:
            continue
        hull = np.zeros_like(k)
        for z in np.where(k.any((0, 1)))[0]:
            hull[:, :, z] = convex_hull_image(k[:, :, z])
        out[f"seno_renal_{side}"] = clean(hull & (tot == 0) & (ct > -150) & (ct < -30))
    for side, (fl, _, _) in renal_fluid(ct, tot, sp).items():
        out[f"orina_renal_{side}"] = fl
    return out


def keep_large(mask, vc, min_cm3):
    lab, n = ndi.label(mask, structure=np.ones((3, 3, 3)))
    if not n:
        return mask
    s = ndi.sum(mask, lab, range(1, n + 1)) * vc
    return np.isin(lab, 1 + np.where(s >= min_cm3)[0])


def largest_components(mask, n):
    lab, k = ndi.label(mask, structure=np.ones((3, 3, 3)))
    if not k:
        return []
    s = ndi.sum(mask, lab, range(1, k + 1))
    return [lab == i + 1 for i in np.argsort(-s)[:n]]


def fill_per_slice(mask):
    return np.stack([ndi.binary_fill_holes(mask[:, :, z]) for z in range(mask.shape[2])], 2)


def describe_zones(mask, ct, sp, organ, ref):
    vc = float(np.prod(sp)) / 1000
    lab, n = ndi.label(mask, structure=np.ones((3, 3, 3)))
    lo = hi = 0
    big = (0, 0.0, 0)
    for i, sl in enumerate(ndi.find_objects(lab), 1):
        m = lab[sl] == i
        hu = float(np.median(ct[sl][m]))
        lo, hi = (lo + 1, hi) if hu < ref else (lo, hi + 1)
        if m.sum() > big[0]:
            big = (int(m.sum()), max((sl[0].stop - sl[0].start) * sp[0], (sl[1].stop - sl[1].start) * sp[1]), hu)
    kinds = " y ".join(t for t in (f"{lo} menos densa(s)" if lo else "", f"{hi} mas densa(s)" if hi else "") if t)
    return (f"{n} zona(s) con densidad distinta a la del {organ} ({kinds}), {mask.sum() * vc:.1f} cm3 en total. "
            f"La mayor mide ~{big[1]:.0f} mm en el plano axial y tiene {big[2]:.0f} HU de mediana "
            f"(el {organ} alrededor: {ref:.0f} HU). Deteccion automatica por densidad: no es un diagnostico.")


def density_findings(ct, tot, sp, extra):
    """Zonas con densidad distinta a la del organo que las contiene: masas, nodulos, quistes, liquido...

    La IA de organos suele incluir un tumor dentro de la mascara del organo, asi que sin esto quedaria oculto en
    su malla. Combina los modelos de lesiones de la IA (si estan) con un umbral de densidad; no clasifica nada.
    """
    vc = float(np.prod(sp)) / 1000
    slab = np.ones((3, 3, 1))
    out = []

    lungs = {}
    for side, suf in (("der", "_right"), ("izq", "_left")):
        lobes = np.isin(tot, [i for k, i in INV.items() if k.startswith("lung_") and k.endswith(suf)])
        if lobes.sum() * vc >= 50:
            lungs[side] = lobes
    if lungs:
        lung = np.any(list(lungs.values()), axis=0)
        ref = float(np.median(ct[lung]))
        # tejido blando (> -100 HU) dentro del contorno del pulmon que no sea vaso ni bronquio
        dense = fill_per_slice(lung) & (ct > -100) & ((tot == 0) | lung)
        airways_vessels = extra_mask(extra, "lung_vessels", ["lung_arteries", "lung_veins", "lung_airways", "lung_airways_wall"])
        if airways_vessels is not None:
            dense &= ~ndi.binary_dilation(airways_vessels, structure=slab)
        dense = keep_large(ndi.binary_opening(dense, structure=slab), vc, 0.3)
        nodules = extra_mask(extra, "lung_nodules", ["lung_nodules"])
        if nodules is not None:
            dense |= keep_large(nodules, vc, 0.05)
        # una masa con el centro necrotico o con aire tiene menos densidad dentro: sigue siendo parte de la masa
        dense = fill_per_slice(dense)
        lab, n = ndi.label(dense, structure=np.ones((3, 3, 3)))
        near = {s: ndi.binary_dilation(l, structure=slab, iterations=10) for s, l in lungs.items()}
        per = {s: np.zeros(ct.shape, bool) for s in lungs}
        for i, sl in enumerate(ndi.find_objects(lab), 1):
            m = lab[sl] == i
            per[max(lungs, key=lambda s: int(near[s][sl][m].sum()))][sl] |= m
        for side, m in per.items():
            if m.any():
                lado = "derecho" if side == "der" else "izquierdo"
                out.append(dict(key=f"zona_densa_pulmon_{side}", name=f"Zona densa en el pulmon {lado}", kind="lesion",
                                parent=f"Pulmon {lado}", mask=m, text=describe_zones(m, ct, sp, f"pulmon {lado}", ref)))
        low = keep_large(ndi.binary_opening(lung & (ct < -950), structure=slab), vc, 0.5)
        if low.any():
            out.append(dict(key="pulmon_baja_densidad", name="Zonas de muy baja densidad del pulmon (< -950 HU)",
                            kind="aire", parent=None, mask=low, hidden=True,
                            text=f"Aire sin tejido pulmonar visible: {low.sum() * vc:.1f} cm3 "
                                 f"({100 * low.sum() / lung.sum():.1f}% del pulmon). Deteccion automatica por densidad."))

    liver_model = extra_mask(extra, "liver_lesions", ["liver_lesions"])
    for organ, key, name in (("liver", "higado", "higado"), ("spleen", "bazo", "bazo"), ("pancreas", "pancreas", "pancreas")):
        o = tot == INV[organ]
        if o.sum() * vc < 20:
            continue
        # los huecos que la IA dejo dentro del organo cuentan: ahi suele estar lo que no se parece al organo
        inner = ndi.binary_erosion(fill_per_slice(o), structure=np.ones((5, 5, 1))) & ((tot == 0) | o)
        if organ == "liver" and "liver_vessels" in extra:
            inner &= ~ndi.binary_dilation(extra["liver_vessels"] > 0, structure=slab)
        if inner.sum() < 1000:
            continue
        vals = ct[inner & o]
        ref = float(np.median(vals))
        mad = 1.4826 * float(np.median(np.abs(vals - ref)))
        hypo = keep_large(ndi.binary_opening(inner & (ct < ref - max(30, 3 * mad)), structure=slab), vc, 0.5)
        hyper = keep_large(ndi.binary_opening(inner & (ct > ref + max(60, 4 * mad)), structure=np.ones((2, 2, 1))), vc, 0.1)
        # se descarta: lo que toca otra estructura (hilio, vasos, vesicula), la grasa de las cisuras y los bordes
        # de vasos, donde solo unos voxeles pasan el umbral pero la zona entera se parece al organo
        m = hypo | hyper
        lab, n = ndi.label(m, structure=np.ones((3, 3, 3)))
        touching = set(np.unique(lab[ndi.binary_dilation((tot > 0) & ~o, structure=slab, iterations=2) & m]))
        keep = []
        for i, sl in enumerate(ndi.find_objects(lab), 1):
            hu = float(np.median(ct[sl][lab[sl] == i]))
            if i not in touching and (-10 < hu < ref - 30 or hu > ref + 60):
                keep.append(i)
        m = np.isin(lab, keep)
        if organ == "liver" and liver_model is not None:
            m |= keep_large(liver_model & ndi.binary_dilation(o, iterations=2), vc, 0.1)
        if m.any():
            out.append(dict(key=f"zona_densidad_{key}", name=f"Zona de otra densidad en el {name}", kind="lesion",
                            parent=name.capitalize(), mask=m, text=describe_zones(m, ct, sp, name, ref)))

    for label, key, name, parent in (("pleural_effusion", "liquido_pleural", "Liquido pleural", None),
                                     ("pericardial_effusion", "liquido_pericardico", "Liquido pericardico", "Corazon")):
        m = extra_mask(extra, "pleural_pericard_effusion", [label])
        if m is None:
            continue
        m = keep_large(m, vc, 5)
        if m.any():
            out.append(dict(key=key, name=name, kind="liquido", parent=parent, mask=m,
                            text=f"{name}: {m.sum() * vc:.0f} cm3, densidad mediana {np.median(ct[m]):.0f} HU. "
                                 "Deteccion automatica (IA): no es un diagnostico."))
    return out


def edt_per_slice(mask, spacing):
    dist = np.zeros(mask.shape, np.float32)
    for z in range(mask.shape[2]):
        dist[:, :, z] = ndi.distance_transform_edt(mask[:, :, z], sampling=spacing[:2])
    return dist


def derived_masks(ct, tot, body, spacing, exclude=None):
    """Cuerpo (para la piel) y el musculo de la pared corporal que ningun modelo de la IA cubre."""
    body_m = ndi.binary_fill_holes(body > 0, structure=np.ones((3, 3, 1)))
    body_m = ndi.binary_opening(body_m, iterations=2)
    dist = edt_per_slice(body_m, spacing)
    labeled = ndi.binary_dilation((tot > 0) if exclude is None else (tot > 0) | exclude, iterations=2)
    soft = (ct >= -29) & (ct <= 150)
    wall = body_m & soft & ~labeled & (dist > 3) & (dist < 45)
    wall = ndi.binary_opening(wall, structure=np.ones((3, 3, 1)), iterations=1)
    lab, n = ndi.label(wall)
    if n:
        sizes = ndi.sum(wall, lab, range(1, n + 1))
        wall = np.isin(lab, 1 + np.where(sizes > 3000)[0])
    return body_m, wall, dist


def refine_bowel(ct, tot, sp, depth_mm=4.0):
    """Quita de las asas la grasa del mesenterio que la IA incluyo entre ellas, para que las asas se separen.

    Solo se elimina grasa conectada con la grasa exterior y a menos de depth_mm del borde. El colon no se
    toca: su pared es tan fina que la grasa exterior se cuela en el contenido fecal moteado.
    """
    tot = tot.copy()
    bowel_ids = [INV[k] for k in ("small_bowel", "duodenum", "stomach")]
    bowel = np.isin(tot, bowel_ids)
    if not bowel.any():
        return tot, 0
    fat = (ct > -250) & (ct < -30)
    shallow = bowel & (edt_per_slice(bowel, sp) <= depth_mm)
    outside_fat = fat & ~bowel & (tot == 0)
    reach = ndi.binary_propagation(outside_fat, structure=np.ones((3, 3, 1)), mask=outside_fat | (fat & shallow))
    remove = reach & bowel
    tot[remove] = 0
    # puentes finos que quedan entre asas
    for i in bowel_ids:
        m = tot == i
        if not m.any():
            continue
        opened = ndi.binary_opening(m, structure=np.ones((3, 3, 1)))
        tot[m & ~opened] = 0
    return tot, int(remove.sum())


def has_contrast(ct, tot):
    aorta = tot == INV["aorta"]
    # sangre sin contraste ~30-45 HU; una fase portal tardia o floja puede quedar en ~90 HU
    return bool(aorta.sum() > 500 and np.median(ct[aorta]) > 65)


def renal_fluid(ct, tot, sp):
    """Orina en el seno renal (pelvis y calices) de cada rinon. Un lado con mucho mas liquido sugiere dilatacion."""
    from skimage.morphology import convex_hull_image
    vc = float(np.prod(sp)) / 1000
    out = {}
    for side, key in (("der", "kidney_right"), ("izq", "kidney_left")):
        k = tot == INV[key]
        if k.sum() < 500:
            continue
        hull = np.zeros_like(k)
        for z in np.where(k.any((0, 1)))[0]:
            hull[:, :, z] = convex_hull_image(k[:, :, z])
        zone = ndi.binary_dilation(hull, structure=np.ones((9, 9, 1)))
        # en los polos no hay seno renal: la zona dilatada solo recogeria tejido vecino
        area = hull.sum((0, 1))
        zone[:, :, area < 0.35 * area.max()] = False
        fl = ndi.binary_opening(zone & (ct >= -10) & (ct <= 25) & (tot == 0), structure=np.ones((2, 2, 1)))
        lab, n = ndi.label(fl)
        if n:
            s = ndi.sum(fl, lab, range(1, n + 1)) * vc
            fl = np.isin(lab, 1 + np.where(s > 0.3)[0])
        out[side] = (fl, round(float(fl.sum() * vc), 1), round(float(k.sum() * vc), 1))
    return out


def find_stones(ct, tot, body_dist, aff):
    """Focos de densidad calcica fuera de hueso y vasos: posibles calculos urinarios (y flebolitos).

    Solo es fiable en estudios sin contraste: con contraste el sistema colector y los vasos brillan igual.
    """
    sp = nib.affines.voxel_sizes(aff)
    vox_mm3 = float(np.prod(sp))
    kid = np.isin(tot, ids("kidney"))
    bladder = tot == INV["urinary_bladder"]
    allowed = (tot == 0) | kid | bladder
    near_bone = ndi.binary_dilation(np.isin(tot, ids("vertebrae", "rib", "hip", "femur", "sacrum", "sternum",
                                                     "costal", "scapula", "clavicula", "humerus", "skull")),
                                    structure=np.ones((5, 5, 3)))
    near_artery = ndi.binary_dilation(np.isin(tot, ids("aorta", "iliac_artery", "common_carotid", "subclavian",
                                                       "brachiocephalic_trunk")), structure=np.ones((3, 3, 1)))
    seed = (ct >= 130) & allowed & ~near_bone & ~near_artery & (body_dist > 15)
    lab, n = ndi.label(seed, structure=np.ones((3, 3, 3)))
    out = np.zeros(ct.shape, np.uint16)
    found = []
    if not n:
        return out, found
    objs = ndi.find_objects(lab)
    # hueso que la IA no etiqueto (puntas de costillas, bordes del campo): focos densos de mas de 15 mm
    dense, nd = ndi.label((ct >= 200) & (body_dist > 0))
    big_ids = [j for j, s in enumerate(ndi.find_objects(dense), 1)
               if s and max((s[0].stop - s[0].start) * sp[0], (s[1].stop - s[1].start) * sp[1]) > 15]
    bone_like = np.isin(dense, big_ids)
    kid_zone = ndi.binary_dilation(kid, structure=np.ones((7, 7, 3)))
    kid_r = tot == INV["kidney_right"]
    kid_bottom = np.where(kid.any((0, 1)))[0].min() if kid.any() else 0
    for i, sl in enumerate(objs, 1):
        m = lab[sl] == i
        nv = int(m.sum())
        vol = nv * vox_mm3
        peak = int(ct[sl][m].max())
        if vol < 2 or vol > 20000 or peak < 200:
            continue
        idx = np.argwhere(m) + [s.start for s in sl]
        c = idx.mean(0)
        ras = nib.affines.apply_affine(aff, c)
        side = "der" if ras[0] > 0 else "izq"  # RAS: x positivo = derecha del paciente
        ci = tuple(np.round(c).astype(int))
        if kid_zone[sl][m].any():
            zone = "renal"
            side = "der" if (kid_r[sl][m].any() or ras[0] > 0) else "izq"
        elif bladder[sl][m].mean() > 0.5:
            zone = "vejiga"
        elif ci[2] < kid_bottom:
            zone = "pelvis"
        else:
            zone = "abdomen"
        ext = (idx.max(0) - idx.min(0) + 1) * sp
        if zone != "renal":
            if ext[:2].max() > 15 or idx[:, 2].min() == 0 or idx[:, 2].max() == ct.shape[2] - 1:
                continue
            pad = tuple(slice(max(s.start - 3, 0), s.stop + 3) for s in sl)
            own = lab[pad] == i
            if (ndi.binary_dilation(own, iterations=3) & bone_like[pad] & ~own).any():
                continue
        k = len(found) + 1
        out[sl][m] = k
        found.append(dict(id=k, zona=zone, lado=side, volumen_mm3=round(vol, 1), hu_max=peak,
                          diametro_mm=round(float(ext[:2].max()), 1), corte=int(ci[2]),
                          ras_mm=[round(float(v), 1) for v in ras]))
    return out, found
