import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import time

import pytest
from PIL import Image, ImageDraw, ImageOps, ImageCms
from PySide6.QtWidgets import QApplication

from converter import ConversionError, Cancelled, Converter
from image_converter import ImageConverter, convert_media
from app import MainWindow


def detailed_image():
    image = Image.new("RGBA", (480, 320))
    image.putdata([(x % 256, y % 256, (x + y) % 256, (x * 3 + y) % 256)
                   for y in range(320) for x in range(480)])
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "Product details ABC 123", fill="black", stroke_width=1)
    return image


def test_lossless_keeps_every_rgba_pixel_and_icc_never_overwrites(tmp_path):
    source = tmp_path / "透明 文字.png"
    image = detailed_image()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image.save(source, icc_profile=profile)
    existing = tmp_path / "透明 文字_图片.webp"
    existing.write_bytes(b"keep")
    before = source.read_bytes()
    # Image quality must not be sacrificed to the video's very small budget.
    result = convert_media(ImageConverter(), source, max_mb=0.0001)
    with Image.open(result.output) as out:
        assert out.convert("RGBA").tobytes() == image.tobytes()
        assert out.info["icc_profile"] == profile
    assert result.encoding == "无损保真" and result.size > 100
    assert source.read_bytes() == before and existing.read_bytes() == b"keep"
    assert Path(result.output).name.endswith("_图片_2.webp")


def test_jpeg_orientation_is_correct_without_further_pixel_loss(tmp_path):
    source = tmp_path / "orientation.jpg"
    im = detailed_image().convert("RGB")
    exif = Image.Exif()
    exif[274] = 6
    im.save(source, quality=96, exif=exif)
    with Image.open(source) as original:
        expected = ImageOps.exif_transpose(original).convert("RGB")
    result = convert_media(ImageConverter(), source)
    with Image.open(result.output) as out:
        assert out.size == (320, 480)
        assert out.convert("RGB").tobytes() == expected.tobytes()


@pytest.mark.parametrize("extension", ["bmp", "tiff", "gif", "webp"])
def test_common_static_formats_are_lossless(tmp_path, extension):
    source = tmp_path / f"image.{extension}"
    detailed_image().convert("RGB").save(source)
    with Image.open(source) as original:
        expected = original.convert("RGB").tobytes()
    result = convert_media(ImageConverter(), source)
    with Image.open(result.output) as out:
        assert out.convert("RGB").tobytes() == expected


def test_animated_gif_is_routed_to_animation_converter(tmp_path, monkeypatch):
    source = tmp_path / "animated.gif"
    a, b = Image.new("RGB", (64, 48), "red"), Image.new("RGB", (64, 48), "blue")
    a.save(source, save_all=True, append_images=[b], duration=100, loop=0)
    marker = object()
    monkeypatch.setattr(Converter, "convert", lambda *_: marker)
    assert convert_media(ImageConverter(), source) is marker


def test_cancel_does_not_publish_and_high_quality_is_available(tmp_path):
    source = tmp_path / "a.png"
    detailed_image().save(source)
    converter = ImageConverter()
    converter.cancel()
    with pytest.raises(Cancelled):
        converter.convert_image(source)
    assert not list(tmp_path.glob("*.webp"))
    result = ImageConverter().convert_image(source, mode="quality95")
    assert result.encoding == "高画质 95" and result.quality == 95
    with Image.open(result.output) as out:
        assert out.size == (480, 320)
        assert out.convert("RGBA").getchannel("A").tobytes() == detailed_image().getchannel("A").tobytes()


def test_image_queue_is_async_and_shows_lossless_result(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "queue.png"
    detailed_image().save(source)
    window = MainWindow()
    window.image_mode.setCurrentIndex(0)
    try:
        window.add_files([str(source)])
        deadline = time.monotonic() + 15
        while window.active and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not window.active
        assert window.row_progress[0].value() == 100
        assert "无损保真" in window.table.item(0, 3).text()
    finally:
        window.close()
