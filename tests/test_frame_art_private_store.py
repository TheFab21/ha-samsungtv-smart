"""Frame Art files are kept out of www and served with a token (#472).

They were written under <config>/www/frame_art/<entry_id>/, which Home
Assistant serves at /local/ without login: Spook 5.12 listed 227 such files on
one install, the user's own uploaded photos among them (personal/MY_F….jpg),
downloadable by anyone who reached Home Assistant and knew the entry id.
"""

import asyncio
import os
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from custom_components.samsungtv_smart import art_files
from custom_components.samsungtv_smart.art_files import (
    art_dir,
    art_file_url,
    art_token,
    art_url,
    legacy_art_dir,
    migrate_legacy_art,
)
from custom_components.samsungtv_smart.http_art import SamsungTVArtView
from custom_components.samsungtv_smart.http_thumbnail import SamsungTVThumbnailView
from homeassistant.components.http import KEY_AUTHENTICATED

ENTRY = "01KPRFBFBF8Y4XQS49WG85JX29J"


@pytest.fixture
def fh(tmp_path):
    return SimpleNamespace(
        config=SimpleNamespace(path=lambda *p: os.path.join(str(tmp_path), *p)),
        data={},
    )


def _write(path, data=b"jpeg"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# --- where the files live -----------------------------------------------------


def test_the_private_folder_is_not_under_www(fh):
    www = fh.config.path("www")
    assert not art_dir(fh, ENTRY).startswith(www)
    assert ".storage" in art_dir(fh, ENTRY)


def test_the_gallery_resize_cache_is_not_under_www_either(fh):
    view = SamsungTVThumbnailView(fh)
    assert not view._cache_dir.startswith(fh.config.path("www"))


# --- migration ------------------------------------------------------------------


def test_existing_files_are_moved_out_of_www(fh):
    _write(legacy_art_dir(fh, ENTRY, "current.jpg"))
    _write(legacy_art_dir(fh, ENTRY, "personal", "MY_F0045.jpg"))
    _write(legacy_art_dir(fh, ".thumb_cache", "abc.jpg"))
    assert migrate_legacy_art(fh, ENTRY) == 2
    assert os.path.isfile(art_dir(fh, ENTRY, "current.jpg"))
    assert os.path.isfile(art_dir(fh, ENTRY, "personal", "MY_F0045.jpg"))
    # Nothing left for /local/ to serve.
    assert not os.path.exists(legacy_art_dir(fh))


def test_a_newer_private_copy_is_kept(fh):
    _write(art_dir(fh, ENTRY, "current.jpg"), b"new")
    _write(legacy_art_dir(fh, ENTRY, "current.jpg"), b"old")
    migrate_legacy_art(fh, ENTRY)
    with open(art_dir(fh, ENTRY, "current.jpg"), "rb") as f:
        assert f.read() == b"new"
    assert not os.path.exists(legacy_art_dir(fh, ENTRY))


def test_another_tv_not_migrated_yet_keeps_the_parent(fh):
    _write(legacy_art_dir(fh, ENTRY, "current.jpg"))
    _write(legacy_art_dir(fh, "OTHERENTRY01", "current.jpg"))
    migrate_legacy_art(fh, ENTRY)
    assert os.path.isfile(legacy_art_dir(fh, "OTHERENTRY01", "current.jpg"))


def test_nothing_to_migrate_is_a_no_op(fh):
    assert migrate_legacy_art(fh, ENTRY) == 0


# --- URLs -------------------------------------------------------------------------


def test_the_token_is_per_tv_and_stable_for_the_run(fh):
    assert art_token(fh, ENTRY) == art_token(fh, ENTRY)
    assert art_token(fh, ENTRY) != art_token(fh, "OTHERENTRY01")
    assert len(art_token(fh, ENTRY)) >= 40


def test_a_new_run_gets_a_new_token(fh, tmp_path):
    first = art_token(fh, ENTRY)
    restarted = SimpleNamespace(config=fh.config, data={})
    assert art_token(restarted, ENTRY) != first


def test_urls_carry_the_token_and_the_version(fh):
    url = art_url(fh, ENTRY, "personal/MY_F0045.jpg", version=123)
    assert url.startswith(f"/api/samsungtv_smart/art/{ENTRY}/personal/MY_F0045.jpg?")
    assert f"token={art_token(fh, ENTRY)}" in url
    assert url.endswith("&v=123")


def test_a_file_url_follows_its_mtime(fh):
    path = art_dir(fh, ENTRY, "current.jpg")
    _write(path)
    os.utime(path, (1000, 1000))
    assert art_file_url(fh, ENTRY, path).endswith("&v=1000")
    os.utime(path, (2000, 2000))
    assert art_file_url(fh, ENTRY, path).endswith("&v=2000")


def test_no_url_for_a_missing_file_or_one_outside_the_folder(fh):
    assert art_file_url(fh, ENTRY, art_dir(fh, ENTRY, "current.jpg")) is None
    outside = fh.config.path("www", "x.jpg")
    _write(outside)
    assert art_file_url(fh, ENTRY, outside) is None


# --- the view -----------------------------------------------------------------------


def _get(fh, entry_id, path, *, token=None, authenticated=False):
    query = f"?token={token}" if token is not None else ""
    request = make_mocked_request(
        "GET", f"/api/samsungtv_smart/art/{entry_id}/{path}{query}"
    )
    request[KEY_AUTHENTICATED] = authenticated
    return asyncio.run(SamsungTVArtView(fh).get(request, entry_id, path))


def _hass_async(fh):
    async def executor(func, *args):
        return func(*args)

    fh.async_add_executor_job = executor
    return fh


def test_the_right_token_gets_the_file(fh):
    _hass_async(fh)
    _write(art_dir(fh, ENTRY, "personal", "MY_F0045.jpg"))
    response = _get(fh, ENTRY, "personal/MY_F0045.jpg", token=art_token(fh, ENTRY))
    assert isinstance(response, web.FileResponse)
    assert response.headers["Cache-Control"].startswith("private")


@pytest.mark.parametrize("token", [None, "", "guess"])
def test_no_token_or_a_wrong_one_gets_nothing(fh, token):
    _hass_async(fh)
    _write(art_dir(fh, ENTRY, "current.jpg"))
    response = _get(fh, ENTRY, "current.jpg", token=token)
    assert response.status == 404


def test_another_tv_s_token_does_not_open_this_one(fh):
    _hass_async(fh)
    _write(art_dir(fh, ENTRY, "current.jpg"))
    response = _get(fh, ENTRY, "current.jpg", token=art_token(fh, "OTHERENTRY01"))
    assert response.status == 404


def test_a_logged_in_request_needs_no_token(fh):
    _hass_async(fh)
    _write(art_dir(fh, ENTRY, "current.jpg"))
    response = _get(fh, ENTRY, "current.jpg", authenticated=True)
    assert isinstance(response, web.FileResponse)


@pytest.mark.parametrize(
    "path", ["../../secrets.yaml", "..%2F..%2Fsecrets.yaml", "personal/../../x"]
)
def test_nothing_outside_the_tv_s_folder(fh, path):
    _hass_async(fh)
    _write(fh.config.path("secrets.yaml"))
    _write(fh.config.path(".storage", "samsungtv_smart", "frame_art", "x"))
    response = _get(fh, ENTRY, path, authenticated=True)
    assert response.status == 404


def test_a_malformed_entry_id_gets_nothing(fh):
    _hass_async(fh)
    response = _get(fh, "../etc", "passwd", authenticated=True)
    assert response.status == 404


def test_the_module_does_not_keep_tokens_across_hass_instances(fh):
    # Stored in fh.data, so they go with the run (restart = new tokens).
    art_token(fh, ENTRY)
    assert art_files._TOKENS in fh.data


# --- the folder sensor --------------------------------------------------------------


def test_the_folder_sensor_gives_a_url_per_file_in_order(fh):
    from custom_components.samsungtv_smart.sensor import FrameArtFolderSensor

    _hass_async(fh)
    for name, mtime in (("MY_F0002.jpg", 20), ("MY_F0001.jpg", 10), ("note.txt", 5)):
        path = art_dir(fh, ENTRY, "personal", name)
        _write(path)
        os.utime(path, (mtime, mtime))
    entry = SimpleNamespace(entry_id=ENTRY)
    sensor = FrameArtFolderSensor(fh, entry, "personal", "The Frame", "uid")
    asyncio.run(sensor.async_update())
    attrs = sensor.extra_state_attributes
    assert [os.path.basename(p) for p in attrs["file_list"]] == [
        "MY_F0001.jpg",
        "MY_F0002.jpg",
    ]
    token = art_token(fh, ENTRY)
    assert attrs["file_urls"] == [
        f"/api/samsungtv_smart/art/{ENTRY}/personal/MY_F0001.jpg?token={token}&v=10",
        f"/api/samsungtv_smart/art/{ENTRY}/personal/MY_F0002.jpg?token={token}&v=20",
    ]
    assert "www" not in attrs["path"]
    # Tokened URLs stay out of the recorder's history.
    assert "file_urls" in FrameArtFolderSensor._unrecorded_attributes
