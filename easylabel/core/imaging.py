"""Master normalisation and display derivatives. Every displayed file is produced here, so the
spec (sRGB ICC, JPEG q95 4:4:4, no EXIF, hash name, no upsampling) is guaranteed, not checked."""
import hashlib
import io
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

SCALES = ('1', '1.5', '2')
ACCEPTED_FORMATS = ('JPEG', 'PNG', 'TIFF')
JPEG_OPTIONS = dict(quality=95, subsampling=0, optimize=False, progressive=False)


def _fixed_srgb_icc():
    """LittleCMS stamps the creation time into the ICC header (bytes 24–35); pin it so encoded files
    are byte-identical across runs and machines — regeneration scripts verify output hashes."""
    icc = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
    icc[24:36] = struct.pack('>6H', 2026, 1, 1, 0, 0, 0)
    assert icc[84:100] == bytes(16)  # no profile ID to invalidate
    return bytes(icc)


SRGB_ICC = _fixed_srgb_icc()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_master(path):
    """Return (8-bit sRGB PIL image, description of the source profile)."""
    with Image.open(path) as im:
        im.load()
        if im.format not in ACCEPTED_FORMATS:
            raise ValueError(f'不支持的格式 {im.format}；主图须为 JPEG / PNG / TIFF')
        if im.mode != 'RGB':
            raise ValueError(f'主图须为 8-bit RGB，实际为 {im.mode}')
        icc = im.info.get('icc_profile')
        if not icc:
            return im.copy(), 'none (declared sRGB)'
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        description = ImageCms.getProfileDescription(source).strip()
        if 'srgb' in description.lower().replace(' ', ''):
            return im.copy(), description
        # Explicit, recorded conversion — never silent.
        converted = ImageCms.profileToProfile(im, source, ImageCms.createProfile('sRGB'),
                                              renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                                              outputMode='RGB')
        return converted, f'converted from {description}'


def css_box(width, height, css_long_edge):
    """CSS size of the displayed image. The short edge is rounded to an even number so the 1.5×
    file has integer device pixels; aspect error is at most one CSS pixel."""
    if css_long_edge % 2:
        raise ValueError('CSS 长边必须为偶数，以保证 1.5× 图为整数像素')
    long, short = max(width, height), min(width, height)
    css_short = max(2, 2 * round(short * css_long_edge / long / 2))
    return (css_long_edge, css_short) if width >= height else (css_short, css_long_edge)


_LINEAR_LUT = np.where(np.arange(256) / 255 <= .04045, np.arange(256) / 255 / 12.92,
                       ((np.arange(256) / 255 + .055) / 1.055) ** 2.4).astype(np.float32)


def to_linear(im):
    """8-bit sRGB image → float32 linear-light array (exact, via lookup table)."""
    return _LINEAR_LUT[np.asarray(im)]


def _to_srgb8(linear):
    linear = np.clip(linear, 0, 1)
    out = np.where(linear <= .0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4, dtype=np.float32) - .055)
    return np.round(out * 255).astype(np.uint8)


def resize_linear(im, size, linear=None):
    """Lanczos in linear light; downsampling only. `linear` may be passed to reuse to_linear(im)."""
    if size == im.size:
        return im.copy()
    if size[0] > im.width or size[1] > im.height:
        raise ValueError(f'主图 {im.width}×{im.height} 小于所需 {size[0]}×{size[1]}；不做放大')
    linear = to_linear(im) if linear is None else linear
    channels = [np.asarray(Image.fromarray(np.ascontiguousarray(linear[..., c]), 'F')
                           .resize(size, Image.Resampling.LANCZOS)) for c in range(3)]
    return Image.fromarray(_to_srgb8(np.stack(channels, axis=-1)), 'RGB')


def encode_jpeg(im):
    buffer = io.BytesIO()
    im.save(buffer, 'JPEG', icc_profile=SRGB_ICC, **JPEG_OPTIONS)
    return buffer.getvalue()


def render_derivatives(im, css_long_edges):
    """Yield (css_long_edge, scale, jpeg bytes, width, height) for every requested size."""
    linear = to_linear(im)
    for css_long_edge in css_long_edges:
        css_w, css_h = css_box(im.width, im.height, css_long_edge)
        for scale in SCALES:
            factor = float(scale)
            size = (round(css_w * factor), round(css_h * factor))
            data = encode_jpeg(resize_linear(im, size, linear))
            yield css_long_edge, scale, data, size[0], size[1]
