#!/usr/bin/env python3
"""Gera sprites de raças fantasia via Pollinations.ai + rembg.

Pipeline (igual ao PetDeskSaas gen_pet_pollinations.py):
  uma imagem por pose (seed fixo) -> crop watermark -> rembg matte ->
  content-crop -> bottom-anchored 32x48 cell -> hard alpha -> quantize 16
  -> sheet 7 col x 3 row (down/up/right, mesmas frames nas 3 linhas).

Run:  python gen_races_ai.py   (precisa de Pillow + rembg instalados)
Cache: ai-src/<race>/<pose>_raw.png — re-runs só refazem o que falta.
"""
import io
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image
from rembg import remove as rembg_remove

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "ai-src"
OUT = ROOT.parent / "assets" / "chars"
FW, FH = 32, 48
GROUND_H = 42          # altura do conteúdo dentro da cell
SEED = 42
WM_CROP = 55
ALPHA_MIN = 96

STYLE = (
    ", one character only, centered, full body visible, "
    "16-bit retro pixel art game sprite, hard pixel edges, "
    "no anti-aliasing, flat colors, thick dark outline, "
    "pure white background"
)

RACES = {
    # prompt visual de cada raça — features grandes e óbvias
    "mago":       "wise old wizard wearing a huge pointed purple hat and long purple robe, holding a wooden staff",
    "druida":     "green-skinned orc druid wearing a brown hooded robe, white tusks, wooden staff with leaves",
    "arqueiro":   "blonde elf ranger with large pointed ears, green hood and tunic, holding a bow",
    "paladino":   "angelic paladin with white feathered wings, golden halo, shining silver armor",
    "feiticeiro": "small green goblin sorcerer with huge ears, purple robe, holding an open spellbook",
    "guerreiro":  "short stout dwarf warrior with a huge orange beard, horned metal helmet, holding an axe",
    "bruxo":      "red-skinned demon warlock with big curved horns, dark robe, small devil tail",
    "artificie":  "pale vampire artificer, slick black hair, high dark collar, dark cape with red lining, holding a wrench",
    "xama":       "brown-furred werewolf shaman standing upright, wolf ears and snout, tribal necklace, holding a totem staff",
}

POSES = [  # (nome, pose) — 5 poses por raça, reutilizadas nos 7 slots
    ("idle",  "standing facing forward, arms at sides"),
    ("walk1", "walking pose, left leg forward, right arm forward"),
    ("walk2", "walking pose, right leg forward, left arm forward"),
    ("type1", "leaning forward, arms extended typing on an invisible keyboard"),
    ("type2", "leaning forward, hands together working, slightly different posture"),
]

# layout da sheet: col 0-3 = walk cycle, 4-5 = typing, 6 = idle/read
ROW_LAYOUT = ["idle", "walk1", "idle", "walk2", "type1", "type2", "idle"]


def fetch(prompt, dest, tries=8):
    url = ("https://image.pollinations.ai/prompt/"
           + urllib.parse.quote(prompt)
           + f"?width=512&height=512&model=turbo&nologo=true&seed={SEED}&enhance=false")
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DevinOffice/0.1"})
            with urllib.request.urlopen(req, timeout=240) as r:
                dest.write_bytes(r.read())
            return
        except Exception as e:
            print(f"  [gen] try {t+1}: {e}", flush=True)
            time.sleep(min(10 * (t + 1), 60))
    raise RuntimeError(f"pollinations falhou: {prompt[:50]}")


def cell_of(nobg_png: Path) -> Image.Image:
    img = Image.open(nobg_png).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    s = min(GROUND_H / img.height, FW / img.width)
    w = max(1, round(img.width * s)); h = max(1, round(img.height * s))
    img = img.resize((w, h), Image.Resampling.LANCZOS)
    cell = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    cell.paste(img, ((FW - w) // 2, FH - h))          # bottom-center
    alpha = cell.getchannel("A").point(lambda v: 255 if v >= ALPHA_MIN else 0)
    cell.putalpha(alpha)
    return cell


def process_pose(race_dir: Path, stem: str) -> Image.Image:
    raw = race_dir / f"{stem}_raw.png"
    nobg = race_dir / f"{stem}_nobg.png"
    if not nobg.exists():
        img = Image.open(raw).convert("RGBA")
        img = img.crop((0, 0, img.width, img.height - WM_CROP))
        buf = io.BytesIO(); img.save(buf, "PNG")
        out = rembg_remove(buf.getvalue())
        nobg.write_bytes(out)
    return cell_of(nobg)


def unify_palette(sheet: Image.Image, colors=16) -> Image.Image:
    alpha = sheet.getchannel("A")
    q = sheet.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGBA")
    q.putalpha(alpha)
    return q


def prefetch_all(workers=5):
    """Fetch paralelo de todos os raws em falta (cache torna re-runs seguros)."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = []
    for name, desc in RACES.items():
        rd = SRC / name
        rd.mkdir(parents=True, exist_ok=True)
        for pose_name, pose in POSES:
            raw = rd / f"{pose_name}_raw.png"
            if not raw.exists():
                jobs.append((name, pose_name, desc + ", " + pose + STYLE, raw))
    print(f"[gen] {len(jobs)} imagens por gerar", flush=True)
    def job(j):
        n, pn, prompt, raw = j
        fetch(prompt, raw)
        print(f"[{n}] {pn} raw ok", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(job, jobs))


def build_race(name: str, desc: str) -> None:
    race_dir = SRC / name
    cells = {}
    for pose_name, pose in POSES:
        cells[pose_name] = process_pose(race_dir, pose_name)
        print(f"[{name}] {pose_name} ok", flush=True)

    cols = len(ROW_LAYOUT)
    sheet = Image.new("RGBA", (FW * cols, FH * 3), (0, 0, 0, 0))
    for row in range(3):                               # down/up/right = mesmas frames
        for c, pose_name in enumerate(ROW_LAYOUT):
            sheet.paste(cells[pose_name], (c * FW, row * FH))
    sheet = unify_palette(sheet)
    sheet.save(OUT / f"ai_{name}.png")
    print(f"[{name}] SHEET -> ai_{name}.png ({sheet.width}x{sheet.height})", flush=True)


if __name__ == "__main__":
    import sys
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:]) or set(RACES)
    prefetch_all()
    for name, desc in RACES.items():
        if name in only:
            build_race(name, desc)
    print("[gen] DONE", flush=True)
