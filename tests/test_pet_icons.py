"""Phase 4: every Jarvis icon is made from Orbi (gui/pet_icons.py + tools/make_icons.py)."""

import os
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_APP = None


def _app():
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _ico_sizes(path: Path) -> list[int]:
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and kind == 1
    sizes = []
    for i in range(count):
        w, h, _, _, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + size <= len(data)
        sizes.append(w or 256)
    return sizes


def test_app_icon_file_is_a_real_multi_size_ico(tmp_path):
    _app()
    from gui import pet_icons as I
    assert _ico_sizes(ROOT / "assets" / "Jarvis_Lite_Logo.ico") == list(I.ICO_SIZES)
    fresh = I.write_ico(tmp_path / "x.ico", sizes=(16, 256))
    assert _ico_sizes(fresh) == [16, 256]


def test_icons_have_no_cut_off_glow_and_a_solid_pet():
    _app()
    from gui import pet_icons as I
    for s in (16, 24, 32, 40, 48, 64, 128, 256):
        img = I.render(s)
        corners = [img.pixelColor(x, y).alpha() for x, y in ((0, 0), (s - 1, 0), (0, s - 1), (s - 1, s - 1))]
        assert max(corners) == 0, s                                   # transparent corners: no "box"
        assert img.pixelColor(s // 2, int(s * 0.62)).alpha() == 255, s  # the body is solid


def test_tray_badges_differ_by_state():
    _app()
    from gui import pet_icons as I
    imgs = {st: I.tray_image(32, st) for st in I.TRAY_STATES}
    for a in I.TRAY_STATES:
        for b in I.TRAY_STATES:
            if a < b:
                assert imgs[a] != imgs[b], (a, b)
    assert I.tray_state("MUTED") == "muted" and I.tray_state("processing") == "busy"
    assert I.tray_state("error") == "alert" and I.tray_state("listening") == "idle"
    assert not I.tray_icon("muted").isNull() and not I.app_icon().isNull()


def test_generated_files_exist_at_the_right_sizes():
    _app()
    from PyQt6.QtGui import QImage
    from tools.make_icons import ANDROID_RES, DENSITIES
    checks = {ROOT / "assets" / "Jarvis_Lite_Logo.png": 512, ROOT / "assets" / "jarvis_pet_256.png": 256,
              ROOT / "dashboard" / "static" / "favicon.png": 64, ROOT / "dashboard" / "static" / "pet.png": 160,
              ROOT / "dashboard" / "static" / "apple-touch-icon.png": 180, ROOT / "docs" / "images" / "jarvis-pet.png": 256}
    for d, px in DENSITIES.items():
        for n in ("ic_launcher.png", "ic_launcher_round.png", "ic_jarvis_launcher.png"):
            checks[ANDROID_RES / f"mipmap-{d}" / n] = px
    for path, px in checks.items():
        img = QImage(str(path))
        assert not img.isNull() and img.width() == px and img.height() == px, path
    social = QImage(str(ROOT / "docs" / "images" / "social-preview.png"))
    assert (social.width(), social.height()) == (1280, 640)


def test_dashboard_serves_only_the_pet_images():
    from fastapi.testclient import TestClient
    from dashboard.server import DashboardServer
    client = TestClient(DashboardServer()._build_app())
    for name in ("favicon.png", "pet.png", "apple-touch-icon.png"):
        r = client.get(f"/static/{name}")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG", name
    assert client.get("/favicon.ico").status_code == 200
    for name in ("app.html", "login.html", "..%2F..%2Fmain.py", "secret.json"):
        assert client.get(f"/static/{name}").status_code == 404, name
    login = client.get("/login").text
    assert 'rel="icon"' in login and "/static/pet.png" in login


def test_tray_icon_follows_jarvis_state_in_both_ui_classes():
    ui = (ROOT / "ui.py").read_text(encoding="utf-8-sig")
    assert ui.count("self._tray.setIcon(tray_icon(ts))") == 2
