"""Make every Jarvis icon from Orbi, the pet (gui/pet_icons.py), so they all match.

    .venv\\Scripts\\python.exe tools\\make_icons.py

Writes:
  assets/Jarvis_Lite_Logo.ico   app / taskbar / Start menu / installer / uninstaller (16 to 256 px)
  assets/Jarvis_Lite_Logo.png   512 px: setup wizard, window logo, notifications (same names: nothing else changes)
  assets/jarvis_pet_256.png     256 px pet
  dashboard/static/favicon.png, apple-touch-icon.png, pet.png   the web dashboard
  jarvis-connect-android/.../mipmap-*/ic_launcher*.png          the phone app, every screen density
  docs/images/jarvis-pet.png, social-preview.png                README and GitHub
The first run keeps a copy of the old icons in build/old_icons.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ANDROID_RES = ROOT / "jarvis-connect-android" / "app" / "src" / "main" / "res"
DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def _backup(paths: list[Path]) -> None:
    dest = ROOT / "build" / "old_icons"
    if dest.exists():
        return
    for p in paths:
        if p.is_file():
            target = dest / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def main() -> list[Path]:
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841  (needed for fonts/painting)
    from gui import pet_icons as I

    out: list[Path] = []
    assets = ROOT / "assets"
    targets = [assets / "Jarvis_Lite_Logo.ico", assets / "Jarvis_Lite_Logo.png"]
    targets += [ANDROID_RES / f"mipmap-{d}" / n for d in DENSITIES
                for n in ("ic_launcher.png", "ic_launcher_round.png", "ic_jarvis_launcher.png")]
    targets += [ANDROID_RES / "drawable" / "ic_jarvis_launcher.png"]
    _backup(targets)

    def save(img, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not img.save(str(path), "PNG"):
            raise SystemExit(f"couldn't write {path}")
        out.append(path)

    out.append(I.write_ico(assets / "Jarvis_Lite_Logo.ico"))
    save(I.render(512), assets / "Jarvis_Lite_Logo.png")
    save(I.render(256), assets / "jarvis_pet_256.png")

    static = ROOT / "dashboard" / "static"
    save(I.render(64), static / "favicon.png")
    save(I.tile(180), static / "apple-touch-icon.png")
    save(I.render(160), static / "pet.png")

    if ANDROID_RES.is_dir():
        for d, px in DENSITIES.items():
            folder = ANDROID_RES / f"mipmap-{d}"
            save(I.tile(px), folder / "ic_launcher.png")
            save(I.tile(px, round_shape=True), folder / "ic_launcher_round.png")
            save(I.tile(px), folder / "ic_jarvis_launcher.png")
        save(I.tile(192), ANDROID_RES / "drawable" / "ic_jarvis_launcher.png")

    images = ROOT / "docs" / "images"
    save(I.render(256), images / "jarvis-pet.png")
    save(I.social_image(), images / "social-preview.png")
    save(_moods_strip(), images / "pet-moods.png")
    return out


def _moods_strip():
    """README picture: Orbi in a few moods, drawn from code (no real screen in it)."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter
    from gui.pet import draw_pet
    moods = [("idle", "Ready"), ("listening", "Listening"), ("thinking", "Thinking"), ("working", "Working"),
             ("speaking", "Speaking"), ("done", "Done!"), ("focus", "Focus mode"), ("sleep", "Quiet hours")]
    cell, w = 150, 150 * len(moods)
    img = QImage(w, 210, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#0b0f17"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    f = QFont("Segoe UI", 1)
    f.setPixelSize(15)
    for i, (mood, label) in enumerate(moods):
        draw_pet(p, i * cell + cell / 2, 108, 78, mood=mood, t=1.1 + i * 0.37, look=(0.3, 0.1), accent="#ffaa00")
        p.setFont(f)
        p.setPen(QColor("#a7b0c0"))
        p.drawText(QRectF(i * cell, 172, cell, 24), Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    return img


if __name__ == "__main__":
    for p in main():
        print(p.relative_to(ROOT))
