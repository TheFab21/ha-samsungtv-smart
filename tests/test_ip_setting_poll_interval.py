"""IP Control expert settings read the TV at most every 30 s.

Home Assistant polls a platform at its own SCAN_INTERVAL, and this
integration's switch platform uses 5 s (for the Art Mode switch). A 9.0.0 log
showed the Auto HDR Remastering switch reading the TV 57 times in 4 min 40 s,
every read refused with -32002 on an HDMI input, while the gamma number on the
30 s number platform read it 7 times.
"""

import asyncio

from custom_components.samsungtv_smart import ip_settings as ips
from custom_components.samsungtv_smart.api.ipcontrol import SamsungIPControlError


class _Client:
    def __init__(self, answer="On"):
        self.reads = 0
        self.answer = answer

    async def async_get_setting(self, method, param):
        self.reads += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def async_set_setting(self, method, param, value):
        return value


class _Entity(ips.IPSettingEntity):
    def _apply(self, value):
        self.value = value

    def async_write_ha_state(self):
        pass


def _entity(monkeypatch, client, viewing=lambda: True):
    entity = object.__new__(_Entity)
    entity.hass = None
    entity._entry_id = "entry"
    entity._host = "192.0.2.1"
    entity._device_name = "TV"
    entity._setting = ips.IP_SWITCHES[0]
    entity._live = False
    entity._last_read = None
    entity.value = None
    entity._client = lambda: client
    monkeypatch.setattr(ips, "tv_normal_viewing", lambda hass, entry_id: viewing())
    monkeypatch.setattr(ips, "clear_token_problem", lambda *a: None)
    return entity


def _clock(monkeypatch, start=1000.0):
    now = [start]
    monkeypatch.setattr(ips.time, "monotonic", lambda: now[0])
    return now


def test_reads_at_most_every_30_seconds(monkeypatch):
    now = _clock(monkeypatch)
    client = _Client()
    entity = _entity(monkeypatch, client)
    for _ in range(6):  # six 5 s polls
        asyncio.run(entity.async_update())
        now[0] += 5
    assert client.reads == 1
    assert entity.value == "On" and entity._live is True
    asyncio.run(entity.async_update())  # 30 s after the first read
    assert client.reads == 2


def test_a_refused_read_also_waits(monkeypatch):
    now = _clock(monkeypatch)
    client = _Client(SamsungIPControlError("returned error -32002"))
    entity = _entity(monkeypatch, client)
    for _ in range(6):
        asyncio.run(entity.async_update())
        now[0] += 5
    assert client.reads == 1
    assert entity._live is False


def test_back_in_normal_viewing_reads_at_once(monkeypatch):
    now = _clock(monkeypatch)
    client = _Client()
    viewing = [True]
    entity = _entity(monkeypatch, client, lambda: viewing[0])
    asyncio.run(entity.async_update())
    viewing[0] = False
    now[0] += 5
    asyncio.run(entity.async_update())  # Art Mode: no read, unavailable
    assert client.reads == 1 and entity._live is False
    viewing[0] = True
    now[0] += 5
    asyncio.run(entity.async_update())
    assert client.reads == 2 and entity._live is True


def test_a_write_counts_as_a_fresh_read(monkeypatch):
    now = _clock(monkeypatch)
    client = _Client()
    entity = _entity(monkeypatch, client)
    asyncio.run(entity._async_write("Off"))
    assert entity.value == "Off"
    now[0] += 5
    asyncio.run(entity.async_update())
    assert client.reads == 0
