import json
import os
import numpy as np
import nibabel as nib
import trimesh
import fast_simplification
from scipy import ndimage as ndi
from skimage.measure import marching_cubes
from segmentacion import INV, extra_mask

SKIN = (0.88, 0.68, 0.56)
MUSCLE = (0.62, 0.16, 0.14)
BONE = (0.93, 0.89, 0.78)
ART = (0.80, 0.06, 0.06)
VEIN = (0.16, 0.26, 0.66)
KIDNEY = (0.58, 0.22, 0.16)
STONE = (1.0, 0.85, 0.15)
DENSITY_COLORS = {"lesion": (0.86, 0.30, 0.86), "liquido": (0.35, 0.75, 0.95), "aire": (0.80, 0.92, 1.0)}

LADO = {"left": "izquierdo", "right": "derecho"}
WALL_MUSCLES = {"pectoralis_major": "Pectoral mayor", "rectus_abdominis": "Recto abdominal", "serratus_anterior": "Serrato anterior",
                "latissimus_dorsi": "Dorsal ancho", "trapezius": "Trapecio", "external_oblique": "Oblicuo externo",
                "internal_oblique": "Oblicuo interno", "quadratus_lumborum": "Cuadrado lumbar"}
SEGMENT_COLORS = [(0.55, 0.20, 0.15), (0.70, 0.30, 0.20), (0.45, 0.15, 0.25), (0.62, 0.38, 0.18),
                  (0.50, 0.25, 0.10), (0.68, 0.22, 0.30), (0.40, 0.20, 0.12), (0.75, 0.40, 0.30)]
VERTS = [f"C{i}" for i in range(1, 8)] + [f"T{i}" for i in range(1, 13)] + [f"L{i}" for i in range(1, 6)] + ["S1"]


def load_mask(path):
    a = np.load(path)
    return (a[a.files[0]] if hasattr(a, "files") else a).astype(bool)


def catalog():
    """Estructuras con nombre en espanol. Las etiquetas de TotalSegmentator que no esten aqui se agregan solas."""
    S = []

    def add(key, name, layer, color, labels, **kw):
        S.append(dict(key=key, name=name, layer=layer, color=color, labels=labels, **kw))

    add("piel", "Piel", "Piel", SKIN, "body", opacity=0.35)
    for side, lado in LADO.items():
        add(f"iliopsoas_{side}", f"Psoas iliaco {lado}", "Musculo", MUSCLE, [f"iliopsoas_{side}"])
        add(f"gluteus_maximus_{side}", f"Gluteo mayor {lado}", "Musculo", MUSCLE, [f"gluteus_maximus_{side}"])
        add(f"gluteus_medius_{side}", f"Gluteo medio {lado}", "Musculo", MUSCLE, [f"gluteus_medius_{side}"])
        add(f"gluteus_minimus_{side}", f"Gluteo menor {lado}", "Musculo", MUSCLE, [f"gluteus_minimus_{side}"])
        add(f"autochthon_{side}", f"Musculos paravertebrales {lado}s", "Musculo", MUSCLE, [f"autochthon_{side}"])
    for side, lado in LADO.items():
        for k, nm in WALL_MUSCLES.items():
            add(f"{k}_{side}", f"{nm} {lado}", "Musculo", MUSCLE, ("extra", "abdominal_muscles", [f"{k}_{side}"]))
    add("pared", "Otros musculos de la pared", "Musculo", (0.66, 0.2, 0.17), "wall")

    for v in VERTS:
        add(f"vert_{v}", f"Vertebra {v}", "Hueso", BONE, [f"vertebrae_{v}"])
    add("sacro", "Sacro", "Hueso", BONE, ["sacrum"])
    add("hip_left", "Hueso iliaco izquierdo", "Hueso", BONE, ["hip_left"])
    add("hip_right", "Hueso iliaco derecho", "Hueso", BONE, ["hip_right"])
    add("femur_left", "Femur izquierdo", "Hueso", BONE, ["femur_left"])
    add("femur_right", "Femur derecho", "Hueso", BONE, ["femur_right"])
    add("costillas_izq", "Costillas izquierdas", "Hueso", BONE, [f"rib_left_{i}" for i in range(1, 13)])
    add("costillas_der", "Costillas derechas", "Hueso", BONE, [f"rib_right_{i}" for i in range(1, 13)])
    add("esternon", "Esternon", "Hueso", BONE, ["sternum"])
    add("cartilagos", "Cartilagos costales", "Hueso", (0.82, 0.86, 0.88), ["costal_cartilages"])
    add("medula", "Medula espinal", "Hueso", (0.96, 0.86, 0.45), ["spinal_cord"])
    add("discos", "Discos intervertebrales", "Hueso", (0.55, 0.75, 0.85), ("extra", "vertebrae_body", ["intervertebral_discs"]))

    add("higado", "Higado", "Organos", (0.48, 0.13, 0.10), ["liver"])
    add("bazo", "Bazo", "Organos", (0.42, 0.10, 0.20), ["spleen"])
    add("vesicula", "Vesicula biliar", "Organos", (0.30, 0.52, 0.22), ["gallbladder"])
    add("pancreas", "Pancreas", "Organos", (0.92, 0.76, 0.50), ["pancreas"])
    add("suprarrenal_der", "Glandula suprarrenal derecha", "Organos", (0.86, 0.66, 0.26), ["adrenal_gland_right"])
    add("suprarrenal_izq", "Glandula suprarrenal izquierda", "Organos", (0.86, 0.66, 0.26), ["adrenal_gland_left"])
    add("pulmon_izq", "Pulmon izquierdo", "Organos", (0.95, 0.66, 0.66), ["lung_upper_lobe_left", "lung_lower_lobe_left"], opacity=0.6)
    add("pulmon_der", "Pulmon derecho", "Organos", (0.95, 0.66, 0.66),
        ["lung_upper_lobe_right", "lung_middle_lobe_right", "lung_lower_lobe_right"], opacity=0.6)
    add("corazon", "Corazon", "Organos", (0.62, 0.10, 0.10), ["heart"])

    add("rinon_der", "Rinon derecho", "Urinario", KIDNEY, ["kidney_right"])
    add("rinon_izq", "Rinon izquierdo", "Urinario", KIDNEY, ["kidney_left"])
    add("quiste_renal_der", "Quiste renal derecho", "Urinario", (0.92, 0.9, 0.55), ["kidney_cyst_right"])
    add("quiste_renal_izq", "Quiste renal izquierdo", "Urinario", (0.92, 0.9, 0.55), ["kidney_cyst_left"])
    add("vejiga", "Vejiga", "Urinario", (0.92, 0.82, 0.56), ["urinary_bladder"])
    add("prostata", "Prostata", "Urinario", (0.80, 0.55, 0.60), ["prostate"])

    add("esofago", "Esofago", "Digestivo", (0.86, 0.55, 0.50), ["esophagus"])
    add("estomago", "Estomago", "Digestivo", (0.86, 0.56, 0.50), ["stomach"])
    add("duodeno", "Duodeno", "Digestivo", (0.90, 0.62, 0.54), ["duodenum"])
    add("intestino", "Intestino delgado", "Digestivo", (0.92, 0.64, 0.56), ["small_bowel"])
    add("colon", "Colon", "Digestivo", (0.76, 0.50, 0.40), ["colon"])

    add("aorta", "Aorta", "Vasos", ART, ["aorta"])
    add("iliaca_art_izq", "Arteria iliaca izquierda", "Vasos", ART, ["iliac_artery_left"])
    add("iliaca_art_der", "Arteria iliaca derecha", "Vasos", ART, ["iliac_artery_right"])
    add("cava", "Vena cava inferior", "Vasos", VEIN, ["inferior_vena_cava"])
    add("iliaca_ven_izq", "Vena iliaca izquierda", "Vasos", VEIN, ["iliac_vena_left"])
    add("iliaca_ven_der", "Vena iliaca derecha", "Vasos", VEIN, ["iliac_vena_right"])
    add("porta", "Vena porta y esplenica", "Vasos", (0.36, 0.22, 0.62), ["portal_vein_and_splenic_vein"])
    add("vena_pulmonar", "Venas pulmonares", "Vasos", VEIN, ["pulmonary_vein"])

    # contenido interno: modelos especificos de la IA y densidad dentro de cada organo
    lv = "lung_vessels"
    add("bronquios", "Bronquios (via aerea)", "Interior", (0.88, 0.92, 0.96), ("extra", lv, ["lung_airways"]), parent="Pulmones")
    add("arterias_pulmon", "Arterias dentro del pulmon", "Interior", ART, ("extra", lv, ["lung_arteries"]), parent="Pulmones")
    add("venas_pulmon", "Venas dentro del pulmon", "Interior", VEIN, ("extra", lv, ["lung_veins"]), parent="Pulmones")
    add("vasos_higado", "Vasos dentro del higado", "Interior", (0.38, 0.25, 0.78), ("extra", "liver_vessels", ["liver_vessels"]),
        parent="Higado")
    for i, rom in enumerate(("I", "II", "III", "IV", "V", "VI", "VII", "VIII"), 1):
        add(f"segmento_hepatico_{i}", f"Segmento hepatico {rom}", "Interior", SEGMENT_COLORS[i - 1],
            ("extra", "liver_segments", [f"liver_segment_{i}"]), parent="Higado", hidden=True)
    add("pared_digestiva", "Pared del tubo digestivo", "Interior", (0.86, 0.46, 0.42), ("interior", "pared_digestiva"),
        parent="Digestivo", opacity=0.5)
    add("gas_digestivo", "Gas dentro del intestino", "Interior", (0.72, 0.84, 0.96), ("interior", "gas_digestivo"), parent="Digestivo")
    add("contenido_colon", "Contenido del colon (heces)", "Interior", (0.52, 0.38, 0.20), ("interior", "contenido_colon"),
        parent="Colon")
    add("contenido_intestinal", "Contenido del estomago e intestino delgado", "Interior", (0.78, 0.70, 0.38),
        ("interior", "contenido_intestinal"), parent="Digestivo")
    add("contraste_oral", "Contraste oral", "Interior", (0.98, 0.98, 0.86), ("interior", "contraste_oral"), parent="Digestivo")
    add("medula_osea", "Hueso esponjoso y medula osea", "Interior", (0.78, 0.36, 0.30), ("interior", "medula_osea"), parent="Huesos")
    for side, lado in (("der", "derecho"), ("izq", "izquierdo")):
        add(f"seno_renal_{side}", f"Grasa del seno renal {lado}", "Interior", (0.95, 0.85, 0.45), ("interior", f"seno_renal_{side}"),
            parent=f"Rinon {lado}")
        add(f"orina_renal_{side}", f"Orina en pelvis y calices ({lado})", "Interior", (0.35, 0.75, 0.95),
            ("interior", f"orina_renal_{side}"), parent=f"Rinon {lado}")
    add("bilis", "Bilis (contenido de la vesicula)", "Interior", (0.86, 0.74, 0.18), ("interior", "bilis"),
        parent="Vesicula biliar")
    add("calculos_vesiculares", "Calculos en la vesicula", "Interior", STONE, ("interior", "calculos_vesiculares"),
        parent="Vesicula biliar", small=True, marker=True, min_voxels=3)

    used = {l for s in S if isinstance(s["labels"], list) for l in s["labels"]}
    for name in INV:
        if name in used:
            continue
        n = name.lower()
        if any(k in n for k in ("artery", "aorta", "trunk")):
            layer, color = "Vasos", ART
        elif any(k in n for k in ("vein", "vena", "atrial_appendage")):
            layer, color = "Vasos", VEIN
        elif any(k in n for k in ("rib", "vertebra", "humerus", "scapula", "clavicula", "skull", "sternum", "femur")):
            layer, color = "Hueso", BONE
        elif any(k in n for k in ("gluteus", "iliopsoas", "autochthon", "muscle")):
            layer, color = "Musculo", MUSCLE
        else:
            layer, color = "Organos", (0.70, 0.40, 0.38)
        add(name, name.replace("_", " ").capitalize(), layer, color, [name])
    return S


# RAS (mm) -> three.js: X = -R (derecha del paciente a la izquierda de la pantalla visto de frente), Y = superior, Z = anterior
R2T = np.array([[-1, 0, 0], [0, 0, 1], [0, 1, 0]], float)


def mesh_from_mask(mask, aff, zf, max_faces, sigma):
    sp = nib.affines.voxel_sizes(aff)
    idx = np.argwhere(mask)
    lo = np.maximum(idx.min(0) - 3, 0)
    hi = np.minimum(idx.max(0) + 4, mask.shape)
    sub = np.pad(mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]], 2)
    # distancia con signo (mm): interpola la forma entre cortes gruesos sin adelgazar asas pequenas
    sdf = (ndi.distance_transform_edt(~sub, sampling=sp) - ndi.distance_transform_edt(sub, sampling=sp)).astype(np.float32)
    sdf = ndi.gaussian_filter(sdf, sigma)
    nz = sdf.shape[2]
    # mode="nearest": con el modo por defecto el ultimo corte sale a 0 y crea una lamina falsa en el borde
    sdf = ndi.zoom(sdf, (1, 1, zf), order=1, mode="nearest")
    if sdf.min() >= 0:
        return None
    v, f, _, _ = marching_cubes(sdf, 0.0)
    v[:, 2] *= (nz - 1) / (sdf.shape[2] - 1)
    v += lo - 2
    vt = nib.affines.apply_affine(aff, v) @ R2T.T
    # simplificar cerca del origen: las cuadricas en float32 son inestables con coordenadas de ~1 m
    origin = vt.mean(0)
    m = trimesh.Trimesh(vt - origin, f, process=True)
    if len(m.faces) > max_faces:
        vv, ff = fast_simplification.simplify(m.vertices.astype(np.float32), m.faces.astype(np.int32),
                                              target_reduction=1 - max_faces / len(m.faces))
        m = trimesh.Trimesh(vv, ff, process=True)
    trimesh.smoothing.filter_taubin(m, lamb=0.5, nu=-0.53, iterations=10)
    m.apply_translation(origin)
    m.fix_normals()
    return m


def build(study_dir, img, ct, tot, masks, stones, stone_list, findings, info):
    aff = img.affine
    sp = nib.affines.voxel_sizes(aff)
    voxel_cm3 = float(np.prod(sp)) / 1000.0
    zf = max(1, int(round(sp[2] / sp[0])))
    S = catalog()

    # calculos renales detectados automaticamente (solo estudios sin contraste)
    if stone_list is not None:
        for side, lado in (("der", "derecho"), ("izq", "izquierdo")):
            ks = [c["id"] for c in stone_list if c["zona"] == "renal" and c["lado"] == side]
            if ks:
                S.append(dict(key=f"calculos_renales_{side}", name=f"Calculos renales {lado}s (deteccion automatica)",
                              layer="Urinario", color=STONE, labels=("calculo", ks), marker=True, small=True,
                              finding=f"{len(ks)} foco(s) de densidad calcica en el rinon {lado}, detectados por densidad "
                                      f"(>200 HU). Diametro maximo "
                                      f"{max(c['diametro_mm'] for c in stone_list if c['id'] in ks)} mm."))

    density = {d["key"]: d for d in masks.get("densidad", [])}
    for d in density.values():
        S.append(dict(key=d["key"], name=d["name"], layer="Interior", color=DENSITY_COLORS[d["kind"]],
                      labels=("densidad", d["key"]), parent=d.get("parent"), finding=d.get("text"),
                      marker=d["kind"] == "lesion", small=True, min_voxels=3, hidden=d.get("hidden", False)))

    by_key = {s["key"]: s for s in S}
    for h in findings:
        if "mascara" in h:
            src = h["mascara"]
            labels = ("calculo", [int(x) for x in str(src).split(":")[1].split(",")]) if str(src).startswith("calculo:") \
                else ("archivo", src)
            s = dict(key=h["clave"], name=h["nombre"], layer=h.get("capa", "Organos"),
                     color=tuple(h.get("color", STONE)), labels=labels, marker=h.get("marcador", False), small=True)
            by_key.pop(s["key"], None)
            S = [x for x in S if x["key"] != s["key"]] + [s]
            by_key[s["key"]] = s
        target = by_key.get(h.get("clave") or h.get("estructura"))
        if target is None:
            print("  aviso: el hallazgo apunta a una estructura que no existe:", h)
            continue
        if "nombre" in h:
            target["name"] = h["nombre"]
        target["finding"] = h["texto"]

    web = os.path.join(study_dir, "web")
    stl = os.path.join(study_dir, "stl")
    os.makedirs(web, exist_ok=True)
    os.makedirs(stl, exist_ok=True)
    for f in os.listdir(web):
        if f.endswith(".glb"):
            os.remove(os.path.join(web, f))

    occ = ndi.gaussian_filter(((tot > 0) | masks["wall"]).astype(np.float32), (1.5, 1.5, 0.5))
    aff_inv = np.linalg.inv(aff)
    body = masks["body"]
    labels = np.where(body, 255, 0).astype(np.uint8)

    manifest, bounds, coords = [], [], {}
    for s in S:
        lab = s["labels"]
        if lab in ("body", "wall"):
            mask = masks[lab]
        elif isinstance(lab, tuple) and lab[0] == "calculo":
            mask = np.isin(stones, lab[1])
        elif isinstance(lab, tuple) and lab[0] == "archivo":
            mask = load_mask(os.path.join(study_dir, "trabajo", lab[1]))
        elif isinstance(lab, tuple) and lab[0] == "extra":
            mask = extra_mask(masks["extra"], lab[1], lab[2])
        elif isinstance(lab, tuple) and lab[0] == "interior":
            mask = masks["interior"].get(lab[1])
        elif isinstance(lab, tuple) and lab[0] == "densidad":
            mask = density[lab[1]]["mask"]
        else:
            mask = np.isin(tot, [INV[l] for l in lab if l in INV])
        if mask is None:
            continue
        n = int(mask.sum())
        if n < s.get("min_voxels", 2 if s.get("small") else 200):
            continue
        big = n > 200000
        detailed = s["layer"] in ("Digestivo", "Interior")
        faces = (120000 if s["key"] == "piel" else 250000 if detailed and big else 80000 if detailed
                 else 60000 if big else 25000)
        sigma = ((1.0, 1.0, 0.5) if s["key"] in ("piel", "pared") else (0.3, 0.3, 0.15) if s.get("small")
                 else (0.4, 0.4, 0.3) if detailed else (0.6, 0.6, 0.3))
        m = mesh_from_mask(mask, aff, max(1, zf // 2) if s["key"] == "piel" else zf, faces, sigma)
        if m is None or len(m.faces) < 8:
            continue
        m.export(os.path.join(stl, s["key"] + ".stl"))
        if s["key"] != "piel" and s["layer"] != "Interior":
            ao = bake_ao(m, occ, aff_inv)
            m.visual.vertex_colors = np.c_[np.repeat((ao * 255).astype(np.uint8)[:, None], 3, 1),
                                           np.full(len(ao), 255, np.uint8)]
        m.export(os.path.join(web, s["key"] + ".glb"))
        if s["key"] != "piel" and len(manifest) < 250:
            labels[mask] = len(manifest) + 1
        if s["key"] not in ("piel", "pared"):
            coords[s["key"]] = np.argwhere(mask).astype(np.int16)
        bounds.append(m.bounds)
        e = {k: s[k] for k in ("key", "name", "layer")}
        e["color"] = list(s["color"])
        e["opacity"] = s.get("opacity", 1.0)
        e["finding"] = s.get("finding")
        e["parent"] = s.get("parent")
        e["hidden"] = bool(s.get("hidden"))
        if s.get("marker"):
            lab_m, nm = ndi.label(mask, structure=np.ones((3, 3, 3)))
            cs = ndi.center_of_mass(mask, lab_m, range(1, nm + 1))
            e["markers"] = [np.round(nib.affines.apply_affine(aff, c) @ R2T.T, 1).tolist() for c in cs]
        e["volume_cm3"] = round(n * voxel_cm3, 3 if s.get("small") else 1)
        e["faces"] = len(m.faces)
        manifest.append(e)
        print(f"  {s['key']:<28} {n:>9} voxeles {len(m.faces):>7} caras")

    inside = containment(coords, tot.shape)
    layer = {e["key"]: e["layer"] for e in manifest}
    for e in manifest:
        e["contenido"] = [k for k in inside.get(e["key"], [])
                          if e["layer"] != "Interior" or layer[k] == "Interior"]

    b = np.array(bounds)
    lo, hi = b[:, 0].min(0), b[:, 1].max(0)
    volume = export_volume(web, ct, labels, body, aff)
    json.dump({"estudio": info, "center": ((lo + hi) / 2).tolist(), "size": (hi - lo).tolist(), "volume": volume,
               "structures": manifest},
              open(os.path.join(web, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(manifest)


def containment(coords, shape, min_frac=0.6):
    """Para cada estructura, las estructuras mas pequenas que quedan (casi) enteras dentro de ella."""
    keys = sorted(coords, key=lambda k: -len(coords[k]))
    box = {k: (c.min(0).astype(int), c.max(0).astype(int) + 1) for k, c in coords.items()}
    out = {}
    for i, a in enumerate(keys):
        lo = np.maximum(box[a][0] - 2, 0)
        hi = np.minimum(box[a][1] + 2, shape)
        sub = np.zeros(hi - lo, bool)
        sub[tuple((coords[a] - lo).T)] = True
        sub = ndi.binary_dilation(sub, iterations=2)
        for b in keys[i + 1:]:
            if (box[b][0] >= hi).any() or (box[b][1] <= lo).any():
                continue
            cb = coords[b].astype(int) - lo
            ok = ((cb >= 0) & (cb < sub.shape)).all(1)
            if sub[tuple(cb[ok].T)].sum() >= min_frac * len(cb):
                out.setdefault(a, []).append(b)
    return out


def bake_ao(m, occ, aff_inv, dists=(1.5, 3, 5, 8, 12)):
    """Oclusion ambiental por vertice: oscurece los surcos donde la superficie toca otras estructuras."""
    acc = np.zeros(len(m.vertices), np.float32)
    for d in dists:
        ras = (m.vertices + m.vertex_normals * d) @ R2T
        ijk = nib.affines.apply_affine(aff_inv, ras)
        acc += ndi.map_coordinates(occ, ijk.T, order=1, mode="constant", cval=0)
    return np.clip(1 - 0.9 * acc / len(dists), 0.22, 1)


def export_volume(web, ct, labels, body, aff):
    """TAC real (8 bits) y mapa de estructuras, recortados al cuerpo, para pintar las caras de corte en el visor."""
    idx = np.argwhere(body)
    lo, hi = idx.min(0), idx.max(0) + 1
    sl = tuple(slice(a, b) for a, b in zip(lo, hi))
    gray = np.interp(ct[sl], [-1000, -200, 300, 1500], [0, 40, 230, 255]).astype(np.uint8)
    # orden de Data3DTexture: x mas rapido, luego y, luego z
    gray.transpose(2, 1, 0).tofile(os.path.join(web, "volumen.bin"))
    labels[sl].transpose(2, 1, 0).tofile(os.path.join(web, "etiquetas.bin"))
    T = np.eye(4)
    T[:3, 3] = lo
    R = np.eye(4)
    R[:3, :3] = R2T
    return {"dims": [int(v) for v in hi - lo], "ijk_to_three": (R @ aff @ T).flatten().tolist()}
