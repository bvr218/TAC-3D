import os
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi


def win(a, c=50, w=400):
    return (np.clip((a - (c - w / 2)) / w, 0, 1) * 255).astype(np.uint8)


def _grid(tiles, cols):
    w, h = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    M = Image.new("RGB", (w * cols, h * rows))
    for k, t in enumerate(tiles):
        M.paste(t, ((k % cols) * w, (k // cols) * h))
    return M


def overview(out_dir, ct, tot, body_m, wall, sp):
    """Cortes axiales y coronales con todas las etiquetas superpuestas."""
    rng = np.random.default_rng(3)
    pal = rng.integers(60, 255, (256, 3)).astype(np.uint8)
    pal[250] = (255, 60, 60)
    edge = body_m & ~ndi.binary_erosion(body_m, structure=np.ones((3, 3, 1)))

    def overlay(c2, l2, e2):
        g = win(c2)
        rgb = np.stack([g] * 3, -1).astype(np.float32)
        m = l2 > 0
        rgb[m] = rgb[m] * 0.55 + pal[l2[m]] * 0.45
        rgb[e2] = (255, 255, 0)
        return rgb.astype(np.uint8)

    def lab_at(sl):
        l = tot[sl].copy()
        l[wall[sl] & (l == 0)] = 250
        return l

    zs = np.linspace(4, ct.shape[2] - 5, 12).astype(int)
    tiles = []
    for z in zs:
        sl = (slice(None), slice(None), z)
        im = Image.fromarray(overlay(ct[sl].T, lab_at(sl).T, edge[sl].T)).resize((400, 400))
        ImageDraw.Draw(im).text((5, 5), f"corte {z}", fill=(255, 255, 255))
        tiles.append(im)
    _grid(tiles, 4).save(os.path.join(out_dir, "control_axial.png"))

    ys = np.where(body_m.any((0, 2)))[0]
    ys = np.linspace(ys.min() + 0.3 * np.ptp(ys), ys.max() - 0.2 * np.ptp(ys), 4).astype(int)
    h = int(round(512 * ct.shape[2] * sp[2] / (ct.shape[0] * sp[0])))
    tiles = []
    for y in ys:
        sl = (slice(None), y, slice(None))
        o = overlay(ct[sl].T[::-1], lab_at(sl).T[::-1], edge[sl].T[::-1])
        im = Image.fromarray(o).resize((512, h))
        ImageDraw.Draw(im).text((5, 5), f"coronal {y}", fill=(255, 255, 255))
        tiles.append(im)
    _grid(tiles, 4).save(os.path.join(out_dir, "control_coronal.png"))


def zoom_tiles(out_path, ct, items, sp, half_mm=40, window=(60, 500)):
    """Recortes axiales centrados en cada foco. items: [(mascara, texto)]."""
    if not items:
        return
    tiles = []
    for mask, text in items[:24]:
        idx = np.argwhere(mask)
        c = idx.mean(0).round().astype(int)
        zz, cnt = np.unique(idx[:, 2], return_counts=True)
        z = int(zz[cnt.argmax()])
        hx, hy = int(half_mm / sp[0]), int(half_mm / sp[1])
        x0, y0 = max(c[0] - hx, 0), max(c[1] - hy, 0)
        x1, y1 = min(c[0] + hx, ct.shape[0]), min(c[1] + hy, ct.shape[1])
        g = win(ct[x0:x1, y0:y1, z].T, *window)
        rgb = np.stack([g] * 3, -1)
        m = mask[x0:x1, y0:y1, z].T
        ring = ndi.binary_dilation(m, iterations=2) & ~ndi.binary_dilation(m, iterations=1)
        rgb[ring] = (255, 40, 40)
        im = Image.fromarray(rgb).resize((300, 300), Image.NEAREST)
        d = ImageDraw.Draw(im)
        for i, line in enumerate(text.split("\n")):
            d.text((5, 5 + 12 * i), line, fill=(255, 255, 0))
        d.text((5, 285), "der <-> izq  (vista desde los pies)", fill=(170, 170, 170))
        tiles.append(im)
    _grid(tiles, 4).save(out_path)
