"""Static image conversion; lossless is the default, independent of video budgets."""
from pathlib import Path
import tempfile

from PIL import Image, ImageOps, ImageCms

from converter import Converter, ConversionError, Result, publish

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".tif", ".tiff", ".gif", ".webp"}


class ImageConverter(Converter):
    def convert_image(self, source, output_dir=None, mode="lossless"):
        source = Path(source).resolve()
        if mode not in {"lossless", "quality95"}:
            raise ConversionError("未知图片画质模式。")
        self.check_cancel()
        self.progress("正在读取图片与方向信息", None)
        directory = Path(output_dir).resolve() if output_dir else source.parent
        directory.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as original:
            if getattr(original, "n_frames", 1) != 1:
                raise ConversionError("这是动图或多页图片，不能作为单张静态图转换。请使用 GIF/WebP 动图或导出单张图片。")
            image = ImageOps.exif_transpose(original)
            if image.mode in {"I", "F"} or image.mode.startswith("I;16"):
                raise ConversionError("这张图片是高位深 / 浮点图片，请先导出为 8 位 RGB/RGBA 图片以避免色彩损失。")
            if max(image.size) > 16383:
                raise ConversionError("WebP 单边最多 16383 像素，请先拆分长图。")
            icc = original.info.get("icc_profile")
            if image.mode == "CMYK" and icc:
                import io
                target_profile = ImageCms.createProfile("sRGB")
                image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                                 target_profile, outputMode="RGB")
                icc = ImageCms.ImageCmsProfile(target_profile).tobytes()
            alpha = "A" in image.getbands() or "transparency" in image.info
            image = image.convert("RGBA" if alpha else "RGB")
            image.load()
        self.check_cancel()
        lossless = mode == "lossless"
        self.progress("正在无损压缩 · 不新增有损压缩噪点" if lossless else "正在高画质压缩 · 画质 95", None)
        with tempfile.TemporaryDirectory(prefix=".webp-image-", dir=directory) as temp:
            candidate = Path(temp) / "image.webp"
            options = dict(format="WEBP", lossless=lossless, quality=100 if lossless else 95,
                           method=6, exact=True)
            if icc:
                options["icc_profile"] = icc
            image.save(candidate, **options)
            self.check_cancel()
            self.progress("正在校验尺寸、透明通道与图片完整性", None)
            with Image.open(candidate) as checked:
                checked.load()
                if checked.size != image.size or getattr(checked, "n_frames", 1) != 1:
                    raise ConversionError("输出图片校验失败，未保存。")
                if lossless and checked.convert(image.mode).tobytes() != image.tobytes():
                    raise ConversionError("无损像素校验失败，未保存。")
            self.check_cancel()
            output = publish(candidate, directory, source.stem, label="图片")
        result = Result(str(source), str(output), output.stat().st_size, *image.size,
                        1, 0, 100 if lossless else 95, 0, False,
                        encoding="无损保真" if lossless else "高画质 95")
        self.progress("图片已校验并保存", 100)
        return result


def convert_media(converter, source, max_mb=10, output_dir=None, keep_fps=False, image_mode="lossless"):
    source = Path(source)
    if source.suffix.lower() in IMAGE_EXTENSIONS:
        with Image.open(source) as image:
            static = getattr(image, "n_frames", 1) == 1
        if static or source.suffix.lower() not in {".gif", ".webp"}:
            return converter.convert_image(source, output_dir, image_mode)
    return converter.convert(source, max_mb, output_dir, keep_fps)
