"""Frame Art on a TV that says it is not a Frame (CONF_ART_ON_NON_FRAME).

supported() gates every art feature on the REST device flag FrameTVSupport.
The 2026 Micro RGB MRN75R95HAFXZA reports it "false" yet runs the art app
(api_version 6.0.0.0 on the channel; art mode, Art Store slideshows and
mattes all work). With the option on, such a TV is asked for api_version and
counts as supported when the channel answers. Without it, nothing changes:
the art channel is never opened for the check.
"""

import asyncio
import logging

import pytest


class _Resp:
    """A REST /api/v2/ answer."""

    def __init__(self, status=200, frame="false", fail=False):
        self.status = status
        self._frame = frame
        self._fail = fail

    async def __aenter__(self):
        if self._fail:
            raise OSError("TV asleep")
        return self

    async def __aexit__(self, *_exc):
        return False

    async def json(self):
        return {"device": {"FrameTVSupport": self._frame}}


class _Session:
    closed = False

    def __init__(self, **resp):
        self._resp = resp
        self.gets = 0

    def get(self, _url):
        self.gets += 1
        return _Resp(**self._resp)


def _client(monkeypatch, *, option, rest, channel):
    """An art client with a faked REST answer and a faked art channel.

    ``channel`` is what _send_art_request returns for api_version: a dict with
    a version (the app answered), None (silent / cooldown) or an exception.
    """
    from samsungtv_smart_test_api import art  # noqa: PLC0415

    client = art.SamsungTVAsyncArt(
        host="192.0.2.10",
        port=8002,
        token="tok",
        name="pytest",
        art_on_non_frame=option,
    )
    session = _Session(**rest)
    probes = []

    async def _get_session():
        return session

    async def _send(request, timeout=5.0, **_kw):
        probes.append(request["request"])
        await asyncio.sleep(0)  # a real request yields; lets callers overlap
        if isinstance(channel, Exception):
            raise channel
        return channel

    monkeypatch.setattr(client, "_get_session", _get_session)
    monkeypatch.setattr(client, "_send_art_request", _send)
    return client, session, probes


async def test_frame_flag_true_is_enough(monkeypatch):
    """A Frame is a Frame: the REST flag answers, the channel is not asked."""
    client, session, probes = _client(
        monkeypatch, option=True, rest={"frame": "true"}, channel={"version": "4.3.4.0"}
    )
    assert await client.supported() is True
    assert session.gets == 1
    assert probes == []


async def test_option_off_keeps_the_rest_answer(monkeypatch):
    """Off (the default): a non-Frame stays unsupported, channel untouched."""
    client, session, probes = _client(
        monkeypatch,
        option=False,
        rest={"frame": "false"},
        channel={"version": "6.0.0.0"},
    )
    assert await client.supported() is False
    assert session.gets == 1
    assert probes == []


async def test_channel_answer_enables_art_and_is_remembered(monkeypatch, caplog):
    """The R95H: flag false, channel answers -> supported, once per session."""
    client, session, probes = _client(
        monkeypatch,
        option=True,
        rest={"frame": "false"},
        channel={"version": "6.0.0.0"},
    )
    with caplog.at_level(logging.INFO):
        assert await client.supported() is True
    assert probes == ["api_version"]
    assert "6.0.0.0" in caplog.text

    # Later calls (every platform setup, every art service) cost nothing.
    assert await client.supported() is True
    assert session.gets == 1
    assert probes == ["api_version"]


@pytest.mark.parametrize("channel", [None, {}, {"version": ""}, OSError("closed")])
async def test_silent_channel_is_not_supported_and_not_cached(monkeypatch, channel):
    """No version from the channel: not supported now, asked again next time.

    A TV asleep at startup must not be written off for the session; the REST
    path has the same retry-on-failure behaviour.
    """
    client, _session, probes = _client(
        monkeypatch, option=True, rest={"frame": "false"}, channel=channel
    )
    assert await client.supported() is False
    assert await client.supported() is False
    assert probes == ["api_version", "api_version"]


async def test_rest_failure_still_asks_the_channel(monkeypatch):
    """REST unreachable (TV booting, port closed): the channel still decides."""
    client, _session, probes = _client(
        monkeypatch, option=True, rest={"fail": True}, channel={"version": "6.0.0.0"}
    )
    assert await client.supported() is True
    assert probes == ["api_version"]


async def test_rest_failure_without_option_is_unsupported(monkeypatch):
    """Without the option a failed REST check is simply unsupported."""
    client, _session, probes = _client(
        monkeypatch, option=False, rest={"fail": True}, channel={"version": "6.0.0.0"}
    )
    assert await client.supported() is False
    assert probes == []


def test_option_default_is_off():
    """The constructor default keeps today's behaviour for every caller."""
    from samsungtv_smart_test_api import art  # noqa: PLC0415

    client = art.SamsungTVAsyncArt(host="192.0.2.10")
    assert client._art_on_non_frame is False
    assert client._art_channel_verified is False


async def test_art_channel_confirmed_names_the_source(monkeypatch):
    """art_channel_confirmed is True only after the probe answered.

    The sensor platform reads it when it persists CONF_IS_FRAME_TV, to record
    whether the flag came from the REST answer (a Frame) or from the probe,
    so turning the option off later knows which flag it may drop.
    """
    frame, _session, _probes = _client(
        monkeypatch, option=True, rest={"frame": "true"}, channel={"version": "4.3.4.0"}
    )
    assert await frame.supported() is True
    assert frame.art_channel_confirmed is False  # REST answered, channel not asked

    r95h, _session, _probes = _client(
        monkeypatch,
        option=True,
        rest={"frame": "false"},
        channel={"version": "6.0.0.0"},
    )
    assert r95h.art_channel_confirmed is False  # not asked yet
    assert await r95h.supported() is True
    assert r95h.art_channel_confirmed is True

    silent, _session, _probes = _client(
        monkeypatch, option=True, rest={"frame": "false"}, channel=None
    )
    assert await silent.supported() is False
    assert silent.art_channel_confirmed is False


@pytest.mark.parametrize("channel", [[], "6.0.0.0", {"version": None}])
async def test_malformed_channel_answer_is_not_supported(monkeypatch, channel):
    """Only a dict carrying a version counts; anything else is "no answer"."""
    client, _session, probes = _client(
        monkeypatch, option=True, rest={"frame": "false"}, channel=channel
    )
    assert await client.supported() is False
    assert client.art_channel_confirmed is False
    assert probes == ["api_version"]


async def test_probe_uses_the_client_timeout(monkeypatch):
    """The probe must not wait longer than the platforms' own 5 s guard."""
    client, _session, _probes = _client(
        monkeypatch, option=True, rest={"frame": "false"}, channel=None
    )
    seen = {}

    async def _send(request, timeout=5.0, **_kw):
        seen["timeout"] = timeout
        return {"version": "6.0.0.0"}

    monkeypatch.setattr(client, "_send_art_request", _send)
    assert await client.supported() is True
    assert seen["timeout"] == client._timeout


async def test_concurrent_first_probes_ask_once(monkeypatch):
    """Five platform setups call supported() together: one api_version.

    sensor, select, number, switch and button are set up concurrently and all
    reach supported() before the first probe has answered. Only the first one
    needs to ask the TV; the rest must find the answer already there instead
    of each sending their own api_version (and logging their own INFO line).
    """
    client, _session, probes = _client(
        monkeypatch,
        option=True,
        rest={"frame": "false"},
        channel={"version": "6.0.0.0"},
    )
    results = await asyncio.gather(*(client.supported() for _ in range(5)))
    assert results == [True] * 5
    assert client.art_channel_confirmed is True
    assert probes == ["api_version"]


async def test_concurrent_callers_on_a_silent_channel_all_get_false(monkeypatch):
    """No answer for any of them, and nobody is cached as supported."""
    client, _session, probes = _client(
        monkeypatch, option=True, rest={"frame": "false"}, channel=None
    )
    results = await asyncio.gather(*(client.supported() for _ in range(3)))
    assert results == [False] * 3
    assert client.art_channel_confirmed is False
    assert probes  # it did ask; it just was not answered


async def test_silent_channel_is_reported_once_per_session(monkeypatch, caplog):
    """The first silent probe says so at INFO, later ones only at DEBUG."""
    client, _session, _probes = _client(
        monkeypatch, option=True, rest={"frame": "false"}, channel=None
    )
    with caplog.at_level(logging.DEBUG):
        assert await client.supported() is False
        assert await client.supported() is False
    silent = [
        r for r in caplog.records if "did not answer api_version" in r.getMessage()
    ]
    assert [r.levelno for r in silent] == [logging.INFO, logging.DEBUG]


async def test_persisted_confirmation_seeds_the_client(monkeypatch):
    """A set confirmed on an earlier setup is not asked again this session.

    __init__.py calls mark_art_channel_confirmed() when the entry carries
    IS_FRAME_TV_ART_APP, so the media player's own check does not re-ask a
    sleeping TV and call the art unsupported.
    """
    client, session, probes = _client(
        monkeypatch, option=True, rest={"frame": "false"}, channel=None
    )
    client.mark_art_channel_confirmed()
    assert client.art_channel_confirmed is True
    assert await client.supported() is True
    assert session.gets == 0
    assert probes == []
