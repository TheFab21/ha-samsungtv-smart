"""Batch throttle: ~2s between uploads; unchanged second run uploads 0 files."""
import asyncio
import os


async def test_batch_sleeps_two_seconds_between_uploads(art_client, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(secs):
        sleeps.append(secs)

    uploaded: list[str] = []

    async def fake_upload(file, *a, **k):
        uploaded.append(file)
        return f"CID-{file}"

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(art_client, "upload", fake_upload)

    files = [f"img{i}.jpg" for i in range(30)]  # >25, no sidecar -> all upload
    result = await art_client.upload_batch(files)

    assert len(uploaded) == 30
    assert result == [f"CID-img{i}.jpg" for i in range(30)]
    # 30 uploads -> 29 inter-upload throttle sleeps of 2.0s each.
    assert sleeps == [2.0] * 29


async def test_batch_no_sleep_for_single_item(art_client, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(secs):
        sleeps.append(secs)

    async def fake_upload(file, *a, **k):
        return f"CID-{file}"

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(art_client, "upload", fake_upload)

    await art_client.upload_batch(["only.jpg"])
    assert sleeps == []


async def test_second_run_of_unchanged_folder_uploads_zero(art_client, tmp_path, monkeypatch):
    async def no_sleep(_secs):
        return None

    calls: list[str] = []

    async def fake_upload(file, *a, **k):
        calls.append(file)
        return "CID-" + os.path.basename(file)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(art_client, "upload", fake_upload)

    f = tmp_path / "cat.jpg"
    f.write_bytes(b"not-a-real-jpeg")
    sidecar = str(tmp_path / "sidecar.json")

    first = await art_client.upload_batch([str(f)], sidecar_path=sidecar)
    assert first == ["CID-cat.jpg"]
    assert calls == [str(f)]  # uploaded once

    calls.clear()
    second = await art_client.upload_batch([str(f)], sidecar_path=sidecar)
    assert second == []       # unchanged -> skipped
    assert calls == []        # 0 uploads on run 2 (criterion 12)
