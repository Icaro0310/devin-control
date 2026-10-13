#!/usr/bin/env python3
"""Gera sprites de raças fantasia para o devin-office — v2, features bold.

Base: assets/chars/char_*.png (pixel-agents, MIT) — 112x96 =
3 linhas (down,up,right) x 7 frames de 16x32. Cabeça ~y2-16, corpo 17-29.
As features são GRANDES de propósito — legibilidade à distância > subtileza.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
CHARS = ROOT.parent / "assets" / "chars"
CHARS.mkdir(parents=True, exist_ok=True)
FW, FH, COLS, ROWS = 16, 32, 7, 3
SKIN = (228, 178, 138)


def is_skin(p):
    r, g, b, a = p
    return a > 100 and r > 165 and g > 105 and b > 75 and r >= b and g > b - 30


def tint_to(dst, src):
    r, g, b, a = src
    return (min(255, r * dst[0] // SKIN[0]), min(255, g * dst[1] // SKIN[1]),
            min(255, b * dst[2] // SKIN[2]), a)


def recolor(img, skin=None, cloth=None, fur=None, hair=None):
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            p = px[x, y]
            if p[3] < 60:
                continue
            r, g, b, a = p
            if fur:
                lum = min(255, (r + g + b) // 3) / 160.0
                px[x, y] = (min(255, int(fur[0] * lum)),
                            min(255, int(fur[1] * lum)),
                            min(255, int(fur[2] * lum)), a)
            elif is_skin(p):
                if skin:
                    px[x, y] = tint_to(skin, p)
            elif hair and y < 12 and (r + g + b) < 330:   # cabelo escuro -> cor
                px[x, y] = tint_to(hair, (200, 200, 200, a))
            elif cloth:
                px[x, y] = (min(255, r * cloth[0] // 255),
                            min(255, g * cloth[1] // 255),
                            min(255, b * cloth[2] // 255), a)


def feats(fn):
    """decorator: fn recebe setter p(x,y,color) frame-local"""
    return fn


def draw(frame, fns):
    px = frame.load(); w, h = frame.size
    def p(x, y, c):
        if 0 <= x < w and 0 <= y < h:
            px[x, y] = c
    for f in fns:
        f(p)


# ── features bold ─────────────────────────────────────────────
def wizard_hat(p):
    v, e, b = (74, 52, 140, 255), (46, 32, 92, 255), (232, 180, 74, 255)
    for x in range(7, 9):   p(x, 0, v)
    for x in range(6, 10):  p(x, 1, v)
    for x in range(6, 10):  p(x, 2, e)          # dobra
    for x in range(5, 11):  p(x, 3, v)
    for x in range(4, 12):  p(x, 4, v)
    for x in range(3, 13):  p(x, 5, e)
    for x in range(2, 14):  p(x, 6, v)
    for x in range(1, 15):  p(x, 7, b)          # aba
    for x in range(1, 15):  p(x, 8, e)


def horns(p):
    c, t = (138, 31, 31, 255), (200, 60, 50, 255)
    for (x, y) in [(2, 0), (3, 0), (2, 1), (3, 1), (1, 1), (2, 2), (1, 2)]:
        p(x, y, c)
    for (x, y) in [(13, 0), (12, 0), (13, 1), (12, 1), (14, 1), (13, 2), (14, 2)]:
        p(x, y, c)
    p(1, 0, t); p(14, 0, t)


def elf_ears(p):
    sk, dk = (238, 198, 158, 255), (190, 140, 105, 255)
    for y in (11, 12, 13):
        p(1, y, sk); p(0, y, sk); p(14, y, sk); p(15, y, sk)
    p(0, 10, dk); p(15, 10, dk)


def halo(p):
    c = (255, 226, 122, 255)
    for x in range(4, 12): p(x, 0, c)
    p(4, 1, c); p(11, 1, c)
    for x in range(5, 11): p(x, 1, (255, 240, 180, 255))


def wings(p):
    w, s = (244, 244, 250, 255), (190, 196, 216, 255)
    for y in range(12, 26):
        d = max(0, 4 - (y - 12) // 3)              # asas afunilam para baixo
        for i in range(d):
            p(i, y, w if i < d - 1 else s)
            p(15 - i, y, w if i < d - 1 else s)
    for x in range(4):
        p(x, 12, w); p(15 - x, 12, w)


def beard(p, c=(196, 110, 34, 255)):
    for x in range(3, 13):
        for y in range(14, 20):
            p(x, y, c)
    p(4, 20, c); p(11, 20, c)
    p(6, 14, (90, 50, 20, 255)); p(9, 14, (90, 50, 20, 255))   # boca/nariz sombra


def tusks(p):
    w = (255, 255, 250, 255)
    for y in (13, 14, 15, 16, 17):
        p(3, y, w); p(4, y, w); p(11, y, w); p(12, y, w)
    p(3, 12, w); p(12, 12, w)                   # pontas sobem


def wolf(p):  # orelhas + focinho
    e, m, n = (88, 56, 28, 255), (190, 140, 95, 255), (30, 25, 20, 255)
    for (x, y) in [(3, 0), (4, 0), (3, 1), (4, 1), (5, 1), (4, 2), (3, 2)]:
        p(x, y, e)
    for (x, y) in [(12, 0), (11, 0), (12, 1), (11, 1), (10, 1), (11, 2), (12, 2)]:
        p(x, y, e)
    for x in range(6, 10):
        p(x, 14, m); p(x, 15, m)
    p(6, 16, m); p(9, 16, m); p(7, 15, n); p(8, 15, n)


def spellbook(p):
    c, pg = (122, 74, 34, 255), (232, 220, 192, 255)
    for x in range(11, 16):
        for y in range(17, 22): p(x, y, c)
    p(12, 18, pg); p(13, 18, pg); p(12, 19, pg); p(13, 19, pg)
    p(14, 20, (60, 30, 10, 255))


def vamp_collar(p):
    d, r = (24, 18, 40, 255), (150, 30, 50, 255)
    for x in range(2, 14):                      # gola alta atrás da cabeça
        p(x, 13, d); p(x, 14, d)
    for x in range(1, 15):
        p(x, 15, d); p(x, 16, d)
    for x in range(5, 11):
        p(x, 16, r)
    for x in (0, 1, 14, 15):                    # capa escura nas laterais
        for y in range(17, 27): p(x, y, d)
    p(6, 15, r); p(9, 15, r)                    # forro vermelho visível


def horn_helm(p):
    m, h = (140, 145, 156, 255), (220, 220, 230, 255)
    for x in range(3, 13): p(x, 3, m); p(x, 4, m)
    for x in range(4, 12): p(x, 5, m)
    p(1, 2, h); p(2, 2, h); p(2, 3, h); p(13, 2, h); p(14, 2, h); p(13, 3, h)
    p(7, 4, (255, 215, 90, 255)); p(8, 4, (255, 215, 90, 255))   # gema


def hood(p, c=(90, 70, 40, 255)):
    for x in range(3, 13): p(x, 2, c); p(x, 3, c)
    for x in range(2, 14): p(x, 4, c); p(x, 5, c)
    p(2, 6, c); p(13, 6, c)


def tail(p):
    c = (138, 31, 31, 255); tip = (60, 16, 16, 255)
    p(0, 24, c); p(0, 25, c); p(0, 26, c); p(1, 27, c); p(0, 27, tip); p(1, 28, tip)


def leaf_crown(p):
    c = (60, 130, 50, 255)
    p(4, 2, c); p(6, 2, c); p(8, 2, c); p(10, 2, c); p(12, 2, c)


def bow(p):
    c, s = (110, 70, 30, 255), (235, 235, 235, 255)
    # arco curvo grosso no lado esquerdo
    for (x, y) in [(0,15),(1,15),(0,16),(1,16),(0,17),(1,17),
                   (0,18),(0,19),(0,20),(0,21),
                   (0,22),(1,22),(0,23),(1,23)]:
        p(x, y, c)
    for y in range(16, 23): p(2, y, s)          # corda
    p(1, 19, c); p(1, 20, c)                    # grip


def staff(p):
    w, g = (100, 64, 30, 255), (90, 160, 60, 255)
    for y in range(14, 28): p(15, y, w)         # haste direita
    p(15, 13, g); p(14, 14, g)                  # folha no topo


def goblin_ears(p):
    sk = (138, 168, 58, 255); dk = (100, 128, 40, 255)
    for y in (10, 11, 12, 13):
        p(0, y, sk); p(1, y, sk); p(14, y, sk); p(15, y, sk)
    p(0, 9, dk); p(15, 9, dk); p(1, 9, sk); p(14, 9, sk)


RACES = {
    "mago":       ("char_0", dict(cloth=(80, 80, 200)),          [wizard_hat]),
    "druida":     ("char_2", dict(skin=(96, 148, 64), cloth=(110, 130, 80)), [hood, tusks, leaf_crown, staff]),
    "arqueiro":   ("char_1", dict(skin=(238, 198, 158), hair=(232, 190, 80), cloth=(90, 140, 90)), [elf_ears, bow]),
    "paladino":   ("char_3", dict(skin=(240, 216, 200), hair=(240, 230, 190), cloth=(235, 225, 190)), [halo, wings]),
    "feiticeiro": ("char_5", dict(skin=(138, 168, 58), cloth=(120, 90, 160)), [goblin_ears, spellbook], 0.75),
    "guerreiro":  ("char_4", dict(skin=(205, 145, 105), cloth=(150, 130, 110)), [horn_helm, beard], 0.72),
    "bruxo":      ("char_5", dict(skin=(192, 74, 58), cloth=(60, 50, 70)),   [horns, tail]),
    "artificie":  ("char_1", dict(skin=(224, 224, 236), hair=(20, 20, 30), cloth=(80, 70, 110)), [vamp_collar]),
    "xama":       ("char_3", dict(fur=(150, 100, 60)),           [wolf]),
}
RACES = {k: (*v, False) if len(v) == 3 else v for k, v in RACES.items()}


def gen(name, base, rc, fns, squash):
    src = Image.open(CHARS / f"{base}.png").convert("RGBA")
    out = Image.new("RGBA", src.size, (0, 0, 0, 0))
    for r in range(ROWS):
        for f in range(COLS):
            fr = src.crop((f * FW, r * FH, (f + 1) * FW, (r + 1) * FH))
            recolor(fr, **rc)
            if squash:
                fr = fr.resize((FW, int(FH * squash)), Image.NEAREST)
                tmp = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
                tmp.paste(fr, (0, FH - fr.height)); fr = tmp
            draw(fr, fns)
            out.paste(fr, (f * FW, r * FH))
    dest = CHARS / f"race_{name}.png"
    out.save(dest)
    print(f"{dest.name} ok")


if __name__ == "__main__":
    for name, (base, rc, fns, sq) in RACES.items():
        gen(name, base, rc, fns, sq)
