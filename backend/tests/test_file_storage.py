import io

import pytest
from PIL import Image

from app.modules.files.images import InvalidImage, process_image
from app.modules.files.storage import LocalStorage


@pytest.fixture(autouse=True)
def clean_database():
    # These pure file tests do not need the database fixture in conftest.
    yield


def image_bytes(format="PNG", size=(80, 120)):
    stream = io.BytesIO()
    Image.new("RGBA" if format == "PNG" else "RGB", size, "white").save(stream, format=format)
    return stream.getvalue()


def test_private_storage_roundtrip_and_atomic_replacement(tmp_path):
    store = LocalStorage(tmp_path / "private")
    key = "owner/file/original.png"
    store.put(key, b"original")
    assert store.head(key).size == 8
    assert store.read(key) == b"original"
    store.put(key, b"replacement")
    assert store.read(key) == b"replacement"
    assert len(list((tmp_path / "private/owner/file").iterdir())) == 1
    store.delete(key)
    store.delete(key)
    with pytest.raises(FileNotFoundError):
        store.read(key)


@pytest.mark.parametrize("key", ["../outside", "owner/../../outside", "/absolute/file",
                                  "owner\\file", "owner/./file", "owner//file", "owner/file?x"])
def test_storage_rejects_path_escape(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put(key, b"x")


def test_process_original_kept_and_variants_decodable():
    original = image_bytes(size=(2600, 1000))
    result = process_image(original, "image/png", 4_000_000)
    assert (result.width, result.height) == (2600, 1000)
    assert len(result.sha256) == 64
    for variant, max_edge in ((result.reading, 2400), (result.thumbnail, 400)):
        with Image.open(io.BytesIO(variant)) as image:
            assert image.format == "JPEG"
            assert max(image.size) <= max_edge
            image.load()
    assert Image.open(io.BytesIO(original)).format == "PNG"


def test_invalid_mime_and_pixel_limit():
    with pytest.raises(InvalidImage, match="image_type_mismatch"):
        process_image(image_bytes(), "image/jpeg", 40_000_000)
    with pytest.raises(InvalidImage, match="image_pixels_exceeded"):
        process_image(image_bytes(), "image/png", 100)
    with pytest.raises(InvalidImage, match="image_invalid"):
        process_image(b"not an image", "image/png", 40_000_000)
