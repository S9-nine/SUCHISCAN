import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))

import extractor


def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


requires_tesseract = pytest.mark.skipif(
    not _tesseract_available(), reason="Tesseract OCR isn't installed on this machine"
)


def draw_screenshot(path: Path, lines: list[str], dark: bool = False, width: int = 1080, height: int = 1600) -> Path:
    """Render a synthetic (non-personal) payment screenshot for OCR tests.

    Deliberately not using real screenshots from archive/ here: those contain a
    real person's name, UPI ID, and transaction ID, and archive/ is gitignored
    specifically to keep that data out of version control.
    """
    bg, fg = (20, "white") if dark else ("white", (20, 20, 20))
    img = Image.new("RGB", (width, height), color=bg)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default(size=48)
    y = 100
    for line in lines:
        draw.text((80, y), line, fill=fg, font=font)
        y += 90
    img.save(path)
    return path


@pytest.fixture
def tiny_image(tmp_path) -> Path:
    """A minimal valid image file, for tests that mock out the OCR call itself
    and only need `Image.open()` to succeed."""
    path = tmp_path / "tiny.png"
    Image.new("RGB", (20, 20), color="white").save(path)
    return path
