from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.samsungtv_smart.api import samsungws


@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize(
    "age, usable", [(0, True), (29, True), (31, False), (-1, False)]
)
def test_only_recent_native_app_readings_can_replace_a_rest_check(
    monkeypatch, running, age, usable
):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(samsungws, "datetime", SimpleNamespace(now=lambda _: now))
    device = object.__new__(samsungws.SamsungTVWS)
    device._running_app = "com.example.player" if running else None
    device._running_apps = {"com.example.player": now - timedelta(seconds=age)}
    device._last_running_scan = now

    assert device.is_app_running("com.example.player", max_age=30) is (
        running if usable else None
    )


def test_a_missing_native_reading_keeps_the_rest_fallback():
    device = object.__new__(samsungws.SamsungTVWS)
    device._running_apps = {}
    device._running_app = "com.example.player"

    assert device.is_app_running("com.example.player", max_age=30) is None
    assert device.is_app_running("com.example.player") is True
