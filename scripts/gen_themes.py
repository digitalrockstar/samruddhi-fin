"""Generates static/css/themes.css and samruddhi/themes_data.py from palettes (incl. Themes.md).
Every theme is run through the same normaliser so text, accent and status colours meet WCAG contrast
(body text 7:1, secondary 4.5:1, tertiary 3:1, accent/status 4.5:1) while keeping the palette's hues."""
import colorsys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rgb = lambda h: tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
hexs = lambda c: "#%02X%02X%02X" % tuple(max(0, min(255, round(v))) for v in c)
def lum(c):
    f = lambda v: (v / 255 / 12.92) if v / 255 <= .03928 else ((v / 255 + .055) / 1.055) ** 2.4
    r, g, b = map(f, c); return .2126 * r + .7152 * g + .0722 * b
def cr(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return (la + .05) / (lb + .05)
def mix(a, b, t): return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
WHITE, BLACK, INK = (255, 255, 255), (0, 0, 0), (11, 11, 15)
def chroma(c): h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c)); return s * v

def tinted(base, src, target):
    """Mix `base` toward `src` until relative luminance hits `target` (or src itself if it cannot)."""
    if (lum(src) - target) * (lum(base) - target) > 0 and abs(lum(src) - target) > abs(lum(base) - target):
        return src
    lo, hi = 0.0, 1.0
    for _ in range(30):
        m = (lo + hi) / 2
        if (lum(mix(base, src, m)) - target) * (lum(base) - target) > 0: lo = m
        else: hi = m
    return mix(base, src, (lo + hi) / 2)

def fit(c, bgs, need, direction):
    t = 0.0
    while min(cr(c, b) for b in bgs) < need + .08 and t < 1:
        t += .02; c = mix(c, WHITE if direction == "lighten" else BLACK, .06)
    return c

DARK_CHART = ["#32D768", "#FF9F0A", "#BF5AF2", "#FF375F", "#64D2FF", "#FFD60A", "#8E8E9A"]
LIGHT_CHART = ["#1E9E4A", "#C77700", "#8E44D6", "#D92D45", "#0A7EA4", "#9A7400", "#6E6E78"]

def finish(key, name, mode, group, bg, s1, s2, text, text2, text3, accent, a2):
    dark = mode == "dark"; d = "lighten" if dark else "darken"
    if dark:                                    # keep layers ordered: bg < surface < surface-2
        if lum(s1) < lum(bg) + .008: s1 = mix(s1, WHITE, .05)
        if lum(s2) < lum(s1) + .012: s2 = mix(s2, WHITE, .06)
    bgs = (bg, s1, s2)
    text = fit(text, bgs, 7, "lighten" if dark else "darken")
    text2 = fit(text2, bgs, 4.5, d); text3 = fit(text3, bgs, 3, d)
    accent = fit(accent, bgs, 4.5, d); a2 = fit(a2, bgs, 3, d)
    ink = WHITE if cr(accent, WHITE) >= cr(accent, INK) else INK
    sem = lambda h: fit(rgb(h), bgs, 4.5, d)
    pos, neg, warn = (sem("#4ADE80"), sem("#FF6B7F"), sem("#FFB340")) if dark else (sem("#1E9E4A"), sem("#D92D45"), sem("#B86E00"))
    ch = [accent, a2] + [fit(rgb(h), (s1,), 3, d) for h in (DARK_CHART if dark else LIGHT_CHART)]
    r = lambda c, a: "rgba(%d,%d,%d,%s)" % (*[round(v) for v in c], a)
    css = dict(bg=bg, surface=s1, **{"surface-2": s2}, text=text, **{"text-2": text2, "text-3": text3}, accent=accent, **{"accent-ink": ink})
    t = {k: hexs(v) for k, v in css.items()}
    t.update(pos=hexs(pos), neg=hexs(neg), warn=hexs(warn), border=r(text, .16 if dark else .14), glass=r(s1, .74),
             shadow=("0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.45)" if dark else "0 1px 2px rgba(0,0,0,.04),0 8px 24px rgba(0,0,0,.07)"))
    for i, c in enumerate(ch, 1): t[f"c{i}"] = hexs(c)
    return dict(key=key, name=name, mode=mode, group=group, tokens=t, swatch=[t["bg"], t["surface"], t["accent"], hexs(a2)])

def L_(t, base=WHITE): return tinted(base, t, 0)  # placeholder to keep signature obvious
def duo(key, name, tint_l, tint_d, acc_l, acc_d, a2):
    out = []
    t = rgb(tint_l); a = rgb(acc_l)
    bg, s1, s2 = tinted(WHITE, t, .905), tinted(WHITE, t, .98), tinted(WHITE, t, .945)
    tx = tinted(INK, t, .012); out.append(finish(f"{key}-light", name, "light", "Duo", bg, s1, s2, tx, mix(tx, WHITE, .35), mix(tx, WHITE, .5), a, rgb(a2)))
    t = rgb(tint_d); a = rgb(acc_d)
    bg, s1, s2 = tinted(BLACK, t, .012), tinted(BLACK, t, .03), tinted(BLACK, t, .055)
    tx = mix(WHITE, t, .06); out.append(finish(f"{key}-dark", name, "dark", "Duo", bg, s1, s2, tx, mix(tx, BLACK, .32), mix(tx, BLACK, .5), a, rgb(a2)))
    return out

def palette_dark(key, name, p, group, acc_pool=None):
    p = [rgb(x) for x in p]; n = len(p)
    pool = sorted(p[2:] if acc_pool is None else [p[i] for i in acc_pool], key=chroma, reverse=True)
    bg, s1, s2 = tinted(BLACK, p[0], .012), tinted(BLACK, p[1], .03), tinted(BLACK, p[2], .055)
    top = p[-1]; tx = mix(top, WHITE, .65 if lum(top) < .6 else .2)
    return finish(key, name, "dark", group, bg, s1, s2, tx, p[-1] if lum(top) > .35 else mix(p[-1], WHITE, .4), p[-2] if n > 2 else p[-1], pool[0], pool[1] if len(pool) > 1 else pool[0])

def palette_light(key, name, p, group, bg_i, acc_pool, text_i=0):
    p = [rgb(x) for x in p]
    pool = sorted([p[i] for i in acc_pool], key=chroma, reverse=True)
    src = p[bg_i]; bg, s1, s2 = tinted(WHITE, src, .905), tinted(WHITE, src, .98), tinted(WHITE, src, .945)
    tx = tinted(INK, p[text_i], .014)
    return finish(key, name, "light", group, bg, s1, s2, tx, mix(tx, WHITE, .33), mix(tx, WHITE, .5), pool[0], pool[1] if len(pool) > 1 else pool[0])

themes = []
# ---- classic (hand tuned, still normalised)
H = lambda x: rgb(x)
themes += [
 finish("daylight", "Daylight", "light", "Classic", H("#F2F2F7"), H("#FFFFFF"), H("#F7F7FA"), H("#111114"), H("#5C5C66"), H("#8E8E98"), H("#007AFF"), H("#34C759")),
 finish("linen", "Linen", "light", "Classic", H("#F4EFE4"), H("#FFFDF8"), H("#F8F3E8"), H("#2A2118"), H("#6B5E4E"), H("#9A8D7C"), H("#B5651D"), H("#6B9A5B")),
 finish("midnight", "Midnight", "dark", "Classic", H("#07070B"), H("#14141B"), H("#1C1C26"), H("#F5F5F8"), H("#A5A5B3"), H("#6F6F7E"), H("#0A84FF"), H("#32D768")),
 finish("graphite", "Graphite", "dark", "Classic", H("#121214"), H("#1E1E21"), H("#28282C"), H("#F2F2F3"), H("#A8A8AE"), H("#74747B"), H("#30D158"), H("#64D2FF")),
 finish("forest", "Forest", "dark", "Classic", H("#08130E"), H("#112119"), H("#18301F"), H("#EAF6EF"), H("#9DB8A9"), H("#6A8878"), H("#5BD6A0"), H("#9BDC5A")),
 finish("dusk", "Dusk", "dark", "Classic", H("#110C18"), H("#1C1427"), H("#271C37"), H("#F6F0FB"), H("#B3A5C4"), H("#7C6E8F"), H("#BF5AF2"), H("#5FDB8A")),
]
# ---- 2-colour combinations (each in light + dark): key, name, tint_light, tint_dark, accent_light, accent_dark, secondary
for row in [
 ("coral-teal", "Coral × Teal", "#14B8A6", "#14B8A6", "#FF6B5A", "#FF6B5A", "#14B8A6"),
 ("cobalt-lime", "Cobalt × Lime", "#2457FF", "#2457FF", "#2457FF", "#C8FF3D", "#C8FF3D"),
 ("plum-peach", "Plum × Peach", "#FFB39A", "#542A52", "#542A52", "#FFB39A", "#FFB39A"),
 ("sage-cream", "Sage × Cream", "#F2E8CF", "#A3B18A", "#A3B18A", "#A3B18A", "#F2E8CF"),
 ("rose-olive", "Rose × Olive", "#C9A0A0", "#8A8F68", "#8A8F68", "#C9A0A0", "#C9A0A0"),
 ("lavender-beige", "Lavender × Beige", "#D8C3A8", "#A89BBE", "#A89BBE", "#A89BBE", "#D8C3A8"),
 ("coral-navy", "Coral × Navy", "#FF6B5B", "#172A46", "#172A46", "#FF6B5B", "#FF6B5B"),
 ("purple-lime", "Purple × Lime", "#6C3FA0", "#6C3FA0", "#6C3FA0", "#B7F34A", "#B7F34A"),
 ("tangerine-indigo", "Tangerine × Indigo", "#FF8A3D", "#4338CA", "#4338CA", "#FF8A3D", "#FF8A3D"),
]: themes += duo(*row)
# ---- 5-colour palettes
themes += [
 palette_dark("evening-mix", "Evening Mix", ["#0E0D15", "#182346", "#3D5387", "#7C83AD", "#BFA9BA"], "Palettes"),
 palette_light("romantic-blend", "Romantic Blend", ["#826E8B", "#EB6E9B", "#E69CBA", "#F4B3C7", "#F5CDD0"], "Palettes", 4, [1, 0, 2]),
 palette_light("sunset-view-01", "Sunset View 01", ["#C78997", "#F5B297", "#F5D6A2", "#F5E4C4", "#8A99B1"], "Palettes", 3, [0, 4, 1]),
 palette_light("peacock-feather", "Peacock Feather", ["#4D52B4", "#4E9CE8", "#70D6C5", "#CAE5BC", "#E1EDD4"], "Palettes", 4, [0, 1, 2]),
 palette_dark("sunset-view-02", "Sunset View 02", ["#812D35", "#934372", "#B9689F", "#C388A9", "#536CA5"], "Palettes", acc_pool=[2, 3, 4]),
 palette_dark("purple-blend", "Purple Blend", ["#2B103C", "#3D2A5D", "#572866", "#8F529B", "#AC91C0"], "Palettes"),
]
# ---- 6-colour palettes (dark -> light, offered in both modes)
for key, name, p in [
 ("warm-neutrals", "Warm Neutrals", ["#3E3239", "#6B5560", "#9E8189", "#C7A9A3", "#E8D2C4", "#F7EFE7"]),
 ("deep-forest", "Deep Forest Green", ["#051F20", "#0B2B26", "#163832", "#235347", "#8EB69B", "#DAF1DE"]),
 ("midnight-blue", "Midnight Blue", ["#06141B", "#11212D", "#253745", "#4A5C6A", "#9BA8AB", "#CCD0CF"]),
 ("muted-purple", "Muted Purple", ["#17151C", "#292531", "#45404F", "#716879", "#AAA2B3", "#E2DDE7"]),
 ("earthy-brown", "Earthy Brown", ["#211B1A", "#382A28", "#60433D", "#916B60", "#C1A094", "#E8D8CE"]),
 ("navy-slate", "Navy & Slate", ["#111522", "#1E263B", "#303D59", "#536684", "#98A9C4", "#DCE3EF"]),
]:
    themes.append(palette_dark(f"{key}-dark", name, p, "Palettes"))
    themes.append(palette_light(f"{key}-light", name, p, "Palettes", 5, [1, 2, 3], text_i=0))

css = ["/* GENERATED by scripts/gen_themes.py - do not edit by hand. */"]
for t in themes:
    body = ";".join(f"--{k}:{v}" for k, v in t["tokens"].items())
    css.append(f'[data-theme="{t["key"]}"]{{color-scheme:{t["mode"]};{body}}}')
(ROOT / "static/css/themes.css").write_text("\n".join(css) + "\n")
rows = ",\n".join(f'    ({t["key"]!r}, {t["name"]!r}, {t["mode"]!r}, {t["group"]!r}, {t["swatch"]!r})' for t in themes)
(ROOT / "samruddhi/themes_data.py").write_text(
    '"""GENERATED by scripts/gen_themes.py. (key, name, mode, group, [bg, surface, accent, secondary])"""\n'
    f"THEMES = [\n{rows},\n]\nTHEME_KEYS = [t[0] for t in THEMES]\nTHEME_GROUPS = ['Classic', 'Duo', 'Palettes']\n")
print(len(themes), "themes")
