import io

import numpy as np
import pytest
from PIL import Image, ImageCms
from PIL.JpegImagePlugin import get_sampling

from easylabel.core.imaging import SRGB_ICC, css_box, load_master, render_derivatives, resize_linear, to_linear


def test_css_box_even_short_edge_and_orientation():
    assert css_box(3000, 2000, 1024) == (1024, 682)
    assert css_box(2000, 3000, 1024) == (682, 1024)
    w, h = css_box(4000, 2997, 900)
    assert w == 900 and h % 2 == 0
    with pytest.raises(ValueError):
        css_box(3000, 2000, 1023)


def test_derivatives_meet_display_spec():
    im = Image.fromarray((np.random.default_rng(1).random((400, 600, 3)) * 255).astype(np.uint8))
    out = list(render_derivatives(im, [200]))
    assert [(s, w, h) for _, s, _, w, h in out] == [('1', 200, 134), ('1.5', 300, 201), ('2', 400, 268)]
    for _, _, data, w, h in out:
        with Image.open(io.BytesIO(data)) as j:
            assert j.format == 'JPEG' and j.size == (w, h)
            assert 'srgb' in ImageCms.getProfileDescription(
                ImageCms.ImageCmsProfile(io.BytesIO(j.info['icc_profile']))).lower().replace(' ', '')
            assert not j.info.get('exif') and not j.getexif()
            assert get_sampling(j) == 0  # 4:4:4
    # Deterministic: re-rendering gives identical bytes (needed for regeneration scripts).
    assert [o[2] for o in out] == [o[2] for o in render_derivatives(im, [200])]


def test_no_upsampling():
    im = Image.new('RGB', (100, 80))
    with pytest.raises(ValueError, match='不做放大'):
        list(render_derivatives(im, [80]))  # 2× needs 160 px


def test_linear_light_resize_preserves_mean_luminance():
    # A fine black/white checkerboard averages to 50% linear light (≈188 in sRGB), not 128.
    board = (np.indices((200, 200)).sum(axis=0) % 2 * 255).astype(np.uint8)
    im = Image.fromarray(np.stack([board] * 3, axis=-1))
    small = resize_linear(im, (50, 50))
    assert abs(float(to_linear(small).mean()) - 0.5) < 0.02


def test_non_srgb_profile_is_converted_and_recorded(tmp_path):
    path = tmp_path / 'lab.jpg'
    Image.new('RGB', (10, 10), (200, 100, 50)).save(path, icc_profile=SRGB_ICC)
    _, profile = load_master(path)
    assert 'srgb' in profile.lower().replace(' ', '')
    bare = tmp_path / 'bare.png'
    Image.new('RGB', (10, 10)).save(bare)
    assert load_master(bare)[1] == 'none (declared sRGB)'
    gray = tmp_path / 'gray.png'
    Image.new('L', (10, 10)).save(gray)
    with pytest.raises(ValueError, match='RGB'):
        load_master(gray)


def test_srgb_profile_bytes_are_stable():
    import importlib
    import time
    import easylabel.core.imaging as m
    first = m.SRGB_ICC
    time.sleep(1.1)  # the unpinned header would differ in its seconds field
    assert importlib.reload(m).SRGB_ICC == first
