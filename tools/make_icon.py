from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "app.png"
TARGET = ROOT / "assets" / "app.generated.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as image:
        image.convert("RGBA").save(TARGET, sizes=SIZES)
    print(TARGET)


if __name__ == "__main__":
    main()
