"""Themes (contrast, completeness), PNG icon set, and layout regressions."""
import re
import struct
from pathlib import Path

import pytest

from samruddhi.models import User, db
from samruddhi.themes_data import THEME_KEYS, THEMES

from .test_web import client, setup_admin  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static/css/themes.css").read_text()
APP_CSS = (ROOT / "static/css/app.css").read_text()


def parse_themes():
    out = {}
    for key, body in re.findall(r'\[data-theme="([^"]+)"\]\{([^}]*)\}', CSS):
        out[key] = dict(re.findall(r"--([\w-]+):([^;]+)", body))
    return out


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4 for v in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def cr(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def test_every_requested_palette_is_present():
    names = {t[1] for t in THEMES}
    for n in ["Coral × Teal", "Cobalt × Lime", "Plum × Peach", "Sage × Cream", "Rose × Olive", "Lavender × Beige",
              "Coral × Navy", "Purple × Lime", "Tangerine × Indigo", "Evening Mix", "Romantic Blend", "Sunset View 01",
              "Peacock Feather", "Sunset View 02", "Purple Blend", "Warm Neutrals", "Deep Forest Green", "Midnight Blue",
              "Muted Purple", "Earthy Brown", "Navy & Slate", "Daylight", "Linen", "Midnight", "Graphite", "Forest", "Dusk"]:
        assert n in names, n
    assert len(THEME_KEYS) == len(set(THEME_KEYS)) == len(parse_themes()) == 42
    modes = {t[2] for t in THEMES}
    assert modes == {"light", "dark"} and sum(t[2] == "light" for t in THEMES) >= 10 and sum(t[2] == "dark" for t in THEMES) >= 10


@pytest.mark.parametrize("key,t", list(parse_themes().items()))
def test_theme_contrast_is_readable(key, t):
    for bg in ("bg", "surface", "surface-2"):
        assert cr(t["text"], t[bg]) >= 7, (key, "text", bg)
        assert cr(t["text-2"], t[bg]) >= 4.5, (key, "text-2", bg)
        assert cr(t["text-3"], t[bg]) >= 3, (key, "text-3", bg)
        for tone in ("accent", "pos", "neg", "warn"):
            assert cr(t[tone], t[bg]) >= 4.5, (key, tone, bg)
    assert cr(t["accent-ink"], t["accent"]) >= 4.5, (key, "button text")
    assert {f"c{i}" for i in range(1, 9)} <= set(t)


def test_dark_themes_are_dark_and_light_are_light():
    tk = parse_themes()
    for key, name, mode, *_ in THEMES:
        assert (lum(tk[key]["bg"]) < .1) == (mode == "dark"), key


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def test_png_icon_set_exists_with_right_sizes():
    sizes = {"favicon-16.png": 16, "favicon-32.png": 32, "favicon-48.png": 48, "apple-touch-icon.png": 180,
             "icon-192.png": 192, "icon-512.png": 512, "icon-maskable-512.png": 512}
    for f, px in sizes.items():
        assert png_size(ROOT / "static/icons" / f) == (px, px), f
    res = ROOT / "android/app/src/main/res"
    for d, px in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
        for n in ("ic_launcher.png", "ic_launcher_round.png"):
            assert png_size(res / f"mipmap-{d}" / n) == (px, px), (d, n)
    assert not list((ROOT / "static/icons").glob("*.svg")) and not list((ROOT / "android").rglob("*.svg"))
    assert 'android:roundIcon="@mipmap/ic_launcher_round"' in (ROOT / "android/app/src/main/AndroidManifest.xml").read_text()


def test_head_and_manifest_use_png_only(client):
    setup_admin(client)
    html = client.get("/").get_data(as_text=True)
    assert "favicon-32.png" in html and "apple-touch-icon.png" in html and ".svg" not in html.split("</head>")[0]
    m = client.get("/manifest.webmanifest").get_json()
    assert all(i["type"] == "image/png" for i in m["icons"]) and {i["purpose"] for i in m["icons"]} == {"any", "maskable"}
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data[:4] == b"\x89PNG"


def test_settings_lists_every_theme_and_saves_new_ones(client):
    setup_admin(client)
    html = client.get("/settings").get_data(as_text=True)
    assert html.count('name="theme"') == len(THEMES) == 42
    assert "Two-colour pairs" in html and 'value="light"' in html and 'value="dark"' in html
    client.post("/settings/theme", data=dict(theme="coral-navy-dark"))
    assert db.session.get(User, 1).theme == "coral-navy-dark"
    assert 'data-theme="coral-navy-dark"' in client.get("/").get_data(as_text=True)


def test_layout_regressions_desktop_width_and_menu_spacing():
    main = re.search(r"\.main \{([^}]*)\}", APP_CSS).group(1)
    assert "width: 100%" in main and "max-width: 72rem" in main            # content fills the desktop column
    assert re.search(r"\.li \.t, \.li \.s \{ display: block; \}", APP_CSS)  # title and description on separate lines
    assert "@media (min-width: 56rem)" in APP_CSS and ".fgrid" in APP_CSS and ".groups" in APP_CSS
