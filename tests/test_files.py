"""The photo bucket: a stored photo reads back with the same bytes and content type."""

from common.files import Files


def test_get_image_returns_the_stored_bytes_and_content_type(env, ddb):
    files = Files("files")
    key = files.put_image("acc1", "s1", b"jpeg-bytes", "image/jpeg")
    assert files.get_image(key) == (b"jpeg-bytes", "image/jpeg")
