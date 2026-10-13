#!/usr/bin/env python3
"""Re-extrai frames do 03_nobg.png — segmentação por connected component.

Dentro de cada cell do grid, isola o blob cujo centroide está mais próximo
do centro da cell (descarta pixels de chars vizinhos que invadem a cell).
Content-crop -> bottom-anchor 48x48 -> sheet office 7x3.

Rows rpgmaker: 0=down 1=left 2=right 3=up. Office rows: 0=down 1=up 2=right.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "ai-src" / "rpgmaker"
OUT = ROOT.parent / "assets" / "chars"
FW = FH = 48
GROUND_H = 44
ALPHA_MIN = 96

GRIDS = {   # grid real observado em cada 03_nobg
    "mago": (6, 4), "druida": (4, 4), "arqueiro": (4, 4), "paladino": (4, 4),
    "feiticeiro": (4, 4), "guerreiro": (4, 4), "bruxo": (4, 4),
    "artificie": (4, 4), "xama": (4, 4),
}
PICK4 = {6: [0, 2, 3, 5], 4: [0, 1, 2, 3]}


def blob_cell(img, alpha_mask, gx, gy, gw, gh):
    """Extrai só o blob mais centrado dentro da cell (gx,gy)."""
    x0, y0 = int(gx * gw), int(gy * gh)
    x1, y1 = int((gx + 1) * gw), int((gy + 1) * gh)
    cell_a = alpha_mask[y0:y1, x0:x1]
    # apaga pixeis que tocam a margem da cell (linhas de grid residuais)
    m = 4
    border = np.zeros_like(cell_a)
    border[:m, :] = border[-m:, :] = border[:, :m] = border[:, -m:] = True
    cell_a = cell_a & ~border
    lbl, n = ndimage.label(cell_a)
    if n == 0:
        return Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    cell_area = cell_a.shape[0] * cell_a.shape[1]
    ch, cw = cell_a.shape
    parts = []
    for i in range(1, n + 1):
        ys, xs = np.where(lbl == i)
        sz = len(xs)
        if sz < 200 or sz > cell_area * 0.6:        # specks / residual gigante
            continue
        bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max(), ys.max()
        bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
        elongated = max(bw / max(bh, 1), bh / max(bw, 1)) > 4
        touches = bx0 <= 8 or by0 <= 8 or bx1 >= cw - 9 or by1 >= ch - 9
        if elongated and touches:                  # linha de grid residual
            continue
        parts.append((i, sz, bx0, by0, bx1, by1))
    if not parts:
        best = None
    else:
        parts.sort(key=lambda p: -p[1])
        main = parts[0]
        best_ids = {main[0]}                        # blob principal (corpo)
        # junta blobs próximos do principal (halo, asas, props soltos)
        mx = (main[4] - main[2]) * 0.15 + 8
        my = (main[5] - main[3]) * 0.15 + 8
        for i, sz, x0b, y0b, x1b, y1b in parts[1:]:
            if not (x1b < main[2] - mx or x0b > main[4] + mx or
                    y1b < main[3] - my or y0b > main[5] + my):
                best_ids.add(i)
        best = np.isin(lbl, list(best_ids))
    if best is None:                                # fallback: content-crop simples
        sub = np.array(img)[y0:y1, x0:x1].copy()
        sub[~cell_a] = (0, 0, 0, 0)
        cell = Image.fromarray(sub)
        bbox = cell.getbbox()
        if bbox:
            cell = cell.crop(bbox)
        s = min(GROUND_H / cell.height, FW / cell.width)
        w, h = max(1, round(cell.width * s)), max(1, round(cell.height * s))
        cell = cell.resize((w, h), Image.Resampling.LANCZOS)
        out = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
        out.paste(cell, ((FW - w) // 2, FH - h), cell)
        a = out.getchannel("A").point(lambda v: 255 if v >= ALPHA_MIN else 0)
        out.putalpha(a)
        return out
    mask = best
    ys, xs = np.where(mask)
    sub = np.array(img)[y0:y1, x0:x1].copy()
    sub[~mask] = (0, 0, 0, 0)
    cell = Image.fromarray(sub).crop(
        (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    s = min(GROUND_H / cell.height, FW / cell.width)
    w, h = max(1, round(cell.width * s)), max(1, round(cell.height * s))
    cell = cell.resize((w, h), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    out.paste(cell, ((FW - w) // 2, FH - h), cell)
    a = out.getchannel("A").point(lambda v: 255 if v >= ALPHA_MIN else 0)
    out.putalpha(a)
    return out


def fix(name):
    cols, rows = GRIDS[name]
    img = Image.open(SRC / name / "03_nobg.png").convert("RGBA")
    alpha = np.array(img)[:, :, 3] > 110          # threshold alto: corta residual fraco
    gw, gh = img.width / cols, img.height / rows
    pick = PICK4[cols]
    cell = lambda r, i: blob_cell(img, alpha, pick[i], r, gw, gh)

    sheet = Image.new("RGBA", (FW * 7, FH * 3), (0, 0, 0, 0))
    for r, sr in enumerate([0, 3, 2]):
        for c in range(4):
            sheet.paste(cell(sr, c), (c * FW, r * FH))
        # typing: mesmo frame, bob vertical de 2px — sem flicker de silhueta
        t = cell(sr, 0)
        sheet.paste(t, (4 * FW, r * FH))
        bob = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
        bob.paste(t, (0, 2), t)
        sheet.paste(bob, (5 * FW, r * FH))
        sheet.paste(t, (6 * FW, r * FH))
    sheet.save(OUT / f"ai_{name}.png")
    print(f"[fix] {name} {cols}x{rows} -> ai_{name}.png", flush=True)


if __name__ == "__main__":
    import sys
    for n in (sys.argv[1:] or GRIDS):
        fix(n)
    print("DONE")
