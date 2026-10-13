#!/usr/bin/env python3
"""Gera as 9 raças via frame_ronin handle_generate_rpgmaker (Gemini web).

Cada raça -> sheet 4x4 de 48px em ai-src/rpgmaker/<race>/ (RPG Maker:
row0=down, row1=left, row2=right, row3=up). Depois compose_race() monta
o sheet do office: 7 cols x 3 rows (down/up/right) -> assets/chars/ai_<race>.png

Run: "<venv frame-ronin>/python.exe" gen_rpgmaker.py [race ...]
"""
import json
import sys
from pathlib import Path

from frame_ronin_mcp.tools.generate import handle_generate_rpgmaker
from PIL import Image

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "ai-src" / "rpgmaker"
OUT = ROOT.parent / "assets" / "chars"
FW = FH = 48

RACES = {
    "mago":       "wise old wizard character, huge pointed purple hat, long purple robe, holding a wooden staff, full body",
    "druida":     "green-skinned orc druid character, brown hooded robe, white tusks, holding a wooden staff with leaves, full body",
    "arqueiro":   "blonde elf archer character, large pointed ears, green hood and tunic, holding a bow, full body",
    "paladino":   "angelic paladin character, large white feathered wings, golden halo, shining silver armor, full body",
    "feiticeiro": "small green goblin sorcerer character, huge ears, purple robe, holding an open spellbook, full body",
    "guerreiro":  "short stout dwarf warrior character, huge orange beard, horned metal helmet, holding an axe, full body",
    "bruxo":      "red-skinned demon warlock character, big curved horns, dark robe, small devil tail, full body",
    "artificie":  "pale vampire artificer character, slick black hair, high dark collar, dark cape with red lining, holding a wrench, full body",
    "xama":       "brown-furred werewolf shaman character standing upright, wolf ears and snout, tribal necklace, holding a totem staff, full body",
}

# office sheet: cols 0-3 walk, 4-5 typing, 6 idle/read
# rpgmaker rows: 0=down 1=left 2=right 3=up ; office rows: 0=down 1=up 2=right
def compose_race(name):
    frames = SRC / name / "frames"
    sheet = Image.open(SRC / name / "04_resized.png").convert("RGBA")
    cell = lambda r, c: sheet.crop((c * FW, r * FH, (c + 1) * FW, (r + 1) * FH))
    out = Image.new("RGBA", (FW * 7, FH * 3), (0, 0, 0, 0))
    # walk cols 0-3 direto; typing = frames intermédios do front; read = idle
    picks = {0: [(0, c) for c in range(4)], 1: [(3, c) for c in range(4)],
             2: [(2, c) for c in range(4)]}
    for r in range(3):
        src_row = 3 if r == 1 else r                    # up row <- rpgmaker back row
        for c in range(4):
            out.paste(cell(*picks[r][c]), (c * FW, r * FH))
        # typing = contact frames (0 e 2 sao quase iguais -> bob subtil, sem flicker)
        out.paste(cell(src_row, 0), (4 * FW, r * FH))
        out.paste(cell(src_row, 2), (5 * FW, r * FH))
        out.paste(cell(src_row, 0), (6 * FW, r * FH))   # read/idle
    dest = OUT / f"ai_{name}.png"
    out.save(dest)
    print(f"[compose] {dest.name} {out.size}", flush=True)


def gen(name, desc):
    od = SRC / name
    if (od / "04_resized.png").exists():
        print(f"[gen] {name}: cached", flush=True)
        return
    r = handle_generate_rpgmaker({
        "prompt": desc, "output_dir": str(od), "backend": "gemini",
        "width": FW, "height": FH, "rows": 4, "columns": 4, "headless": True,
    })
    print(f"[gen] {name}: {json.dumps(r.get('steps'))}", flush=True)


if __name__ == "__main__":
    names = sys.argv[1:] or list(RACES)
    for n in names:
        gen(n, RACES[n])
        compose_race(n)
    print("DONE", flush=True)
