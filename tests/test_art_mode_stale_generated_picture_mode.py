"""Art on the panel while getTVStates.pictureMode is stuck on a Gen_<n> id.

#447, QE43LS03FAUXXH: after an app closed and the Frame went back to art,
getTVStates kept ``pictureMode: Gen_327705`` (the home screen code) for almost
an hour while ``artModeControl`` correctly said ``artModeOn``. The desync
check in async_get_art_mode was written for the opposite fault (the flag
wedged on over a real input, QE55LS03D fw 2123), so it took the stale snapshot
for the truth and reported Art Mode off.

``pictureModeControl`` tells the two apart. Measured on the same Frame:

    art on the panel   artModeOn   getTVStates Ambient      -32601
    home screen        artModeOff  getTVStates Gen_327705   Gen_327705
    YouTube            artModeOff  getTVStates Gen_589826   Gen_589826
    the stuck case     artModeOn   getTVStates Gen_327705   -32601

and, for 2.5 minutes after a wake from standby into art, ``"Ambient"`` in
place of the -32601. So with the flag on and a Gen_ id in the snapshot,
-32601 or "Ambient" means art; a real mode means the flag has wedged, as
before; any other error decides nothing.

The IP Control state poll does the same check for the cached readers: see
test_ip_state_poll_stale_generated_picture_mode.py.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from custom_components.samsungtv_smart.api.ipcontrol import (
    _HOST_LOCKS,
    ART_DESYNC_THRESHOLD,
    ERROR_METHOD_NOT_FOUND,
    ERROR_NOT_SUPPORTED_MODEL,
    SamsungIPControl,
    SamsungIPControlError,
    SamsungIPControlModeLockedError,
    SamsungIPControlTransportError,
    SamsungIPControlUnsupportedError,
)

HOME_SCREEN = "Gen_327705"


def _refused(code=ERROR_METHOD_NOT_FOUND):
    error = SamsungIPControlUnsupportedError(f"TV returned error {code}")
    error.code = code
    return error


def _client(*, flag="artModeOn", snapshot=HOME_SCREEN, picture_mode=None):
    """A client whose TV answers the given values.

    ``picture_mode`` is what pictureModeControl answers: a mode name, or an
    exception to raise.
    """
    client = SamsungIPControl(MagicMock(), "192.0.2.10", token="synthetic")
    asked = []

    async def request(method, params=None, **_kwargs):
        asked.append(method)
        if method == "powerControl":
            return {"power": "powerOn"}
        if method == "artModeControl":
            return {"artMode": flag}
        if method == "getTVStates":
            return {"pictureMode": snapshot}
        if method == "pictureModeControl":
            if isinstance(picture_mode, Exception):
                raise picture_mode
            return {"pictureMode": picture_mode}
        raise AssertionError(f"unexpected request {method}")

    client._async_request = request
    return client, asked


async def _reads(client, count=ART_DESYNC_THRESHOLD + 1):
    return [await client.async_get_art_mode() for _ in range(count)]


# -- async_get_art_mode ----------------------------------------------------


async def test_a_refused_picture_mode_read_means_art_is_on_the_panel():
    client, _ = _client(picture_mode=_refused())
    assert await _reads(client) == [True] * (ART_DESYNC_THRESHOLD + 1)
    assert client._art_desync_count == 0


async def test_an_ambient_picture_mode_read_means_art_is_on_the_panel():
    """What the Frame answered for 2.5 min after waking from standby into art."""
    client, _ = _client(picture_mode="Ambient")
    assert await _reads(client) == [True] * (ART_DESYNC_THRESHOLD + 1)
    assert client._art_desync_count == 0


async def test_the_settled_read_clears_an_earlier_disagreement():
    client, _ = _client(picture_mode=_refused())
    client._art_desync_count = ART_DESYNC_THRESHOLD - 1
    assert await client.async_get_art_mode() is True
    assert client._art_desync_count == 0


@pytest.mark.parametrize("real_mode", [HOME_SCREEN, "Gen_589826", "Standard"])
async def test_a_real_mode_is_still_a_wedged_flag(real_mode):
    """The home screen answers its own Gen_ id: the QE55LS03D wedge is kept."""
    client, _ = _client(picture_mode=real_mode)
    expected = [True] * (ART_DESYNC_THRESHOLD - 1) + [False, False]
    assert await _reads(client) == expected


@pytest.mark.parametrize("named_mode", ["Movie", "Standard", "FilmmakerMode"])
async def test_a_named_mode_is_not_settled_with_another_request(named_mode):
    client, asked = _client(snapshot=named_mode, picture_mode=_refused())
    expected = [True] * (ART_DESYNC_THRESHOLD - 1) + [False, False]
    assert await _reads(client) == expected
    assert "pictureModeControl" not in asked


@pytest.mark.parametrize(
    "error",
    [
        SamsungIPControlError("TV returned error -32603: Internal error"),
        SamsungIPControlModeLockedError("TV returned error -32002"),
        SamsungIPControlTransportError("timed out"),
        _refused(ERROR_NOT_SUPPORTED_MODEL),
        # No code recorded: not known to be a -32601.
        SamsungIPControlUnsupportedError("unsupported"),
    ],
)
async def test_any_other_error_decides_nothing(error):
    client, _ = _client(picture_mode=error)
    expected = [True] * (ART_DESYNC_THRESHOLD - 1) + [False, False]
    assert await _reads(client) == expected


@pytest.mark.parametrize("answer", [None, "", "  ", 7])
async def test_an_empty_answer_decides_nothing(answer):
    client, _ = _client(picture_mode=answer)
    expected = [True] * (ART_DESYNC_THRESHOLD - 1) + [False, False]
    assert await _reads(client) == expected


async def test_the_flag_off_asks_nothing_more():
    client, asked = _client(flag="artModeOff", picture_mode=_refused())
    assert await client.async_get_art_mode() is False
    assert "pictureModeControl" not in asked


async def test_art_with_a_current_snapshot_asks_nothing_more():
    client, asked = _client(snapshot="Ambient", picture_mode=_refused())
    assert await client.async_get_art_mode() is True
    assert "pictureModeControl" not in asked


async def test_the_flag_is_not_read_twice():
    client, asked = _client(picture_mode=_refused())
    await client.async_get_art_mode()
    assert asked.count("artModeControl") == 1


# -- async_panel_shows_art (guards the Art Mode write) ---------------------


@pytest.mark.parametrize("answer", [_refused(), "Ambient"])
async def test_the_write_guard_sees_art_behind_a_stale_snapshot(answer):
    client, _ = _client(picture_mode=answer)
    assert await client.async_panel_shows_art() is True


async def test_the_write_guard_does_not_trust_the_flag_alone():
    """A wedged flag over the real home screen: still not art."""
    client, _ = _client(picture_mode=HOME_SCREEN)
    assert await client.async_panel_shows_art() is False


async def test_the_write_guard_on_the_home_screen_with_the_flag_off():
    client, asked = _client(flag="artModeOff", picture_mode=HOME_SCREEN)
    assert await client.async_panel_shows_art() is False
    assert "pictureModeControl" not in asked


async def test_the_write_guard_keeps_its_answer_on_any_other_error():
    client, _ = _client(picture_mode=SamsungIPControlTransportError("timed out"))
    assert await client.async_panel_shows_art() is False


@pytest.mark.parametrize(
    ("snapshot", "expected"), [("Ambient", True), ("Movie", False)]
)
async def test_the_write_guard_reads_a_named_mode_as_before(snapshot, expected):
    client, asked = _client(snapshot=snapshot, picture_mode=_refused())
    assert await client.async_panel_shows_art() is expected
    assert asked == ["powerControl", "getTVStates"]


# -- the helper on its own -------------------------------------------------


async def test_the_helper_reads_the_flag_when_it_is_not_given():
    client, asked = _client(picture_mode=_refused())
    assert await client.async_generated_mode_is_art(HOME_SCREEN) is True
    assert asked == ["artModeControl", "pictureModeControl"]


@pytest.mark.parametrize("mode", ["Ambient", "Movie", "", None, 5, "Generic"])
async def test_the_helper_asks_nothing_for_a_mode_that_is_not_generated(mode):
    client, asked = _client(picture_mode=_refused())
    assert await client.async_generated_mode_is_art(mode) is None
    assert asked == []


async def test_the_helper_decides_nothing_when_the_flag_cannot_be_read():
    client, asked = _client(picture_mode=_refused())

    async def request(method, params=None, **_kwargs):
        asked.append(method)
        raise SamsungIPControlTransportError("timed out")

    client._async_request = request
    assert await client.async_generated_mode_is_art(HOME_SCREEN) is None
    assert asked == ["artModeControl"]


# -- the error code reaches the caller -------------------------------------
#
# These go through the real _async_request and _sync_request, with only the
# HTTP post replaced, so the code is read from the instance the client raises.


class _Executor:
    """Stands in for hass: runs the blocking request inline."""

    async def async_add_executor_job(self, function, *args):
        return function(*args)


def _wired(picture_mode_code):
    """A client on the real request path, pictureModeControl refused with a code."""
    _HOST_LOCKS.clear()
    client = SamsungIPControl(_Executor(), "192.0.2.10", token="synthetic")
    answers = {
        "powerControl": {"result": {"power": "powerOn"}},
        "artModeControl": {"result": {"artMode": "artModeOn"}},
        "getTVStates": {"result": {"pictureMode": HOME_SCREEN}},
        "pictureModeControl": {
            "error": {"code": picture_mode_code, "message": "refused"}
        },
    }

    def post(payload, _timeout):
        return json.dumps(answers[json.loads(payload)["method"]])

    client._sync_post = post
    return client


@pytest.mark.parametrize("code", [ERROR_METHOD_NOT_FOUND, ERROR_NOT_SUPPORTED_MODEL])
def test_an_unsupported_error_carries_its_code(code):
    client = SamsungIPControl(MagicMock(), "192.0.2.10", token="synthetic")
    reply = json.dumps({"error": {"code": code, "message": "refused"}})
    with patch.object(client, "_sync_post", return_value=reply):
        with pytest.raises(SamsungIPControlUnsupportedError) as raised:
            client._sync_request("pictureModeControl", None, True, 5)
    # On the instance: the class keeps its default, so nothing leaks between
    # two errors.
    assert vars(raised.value)["code"] == code
    assert SamsungIPControlUnsupportedError.code is None


async def test_a_method_not_found_from_the_tv_counts_as_art():
    client = _wired(ERROR_METHOD_NOT_FOUND)
    assert await client.async_generated_mode_is_art(HOME_SCREEN) is True
    assert await client.async_panel_shows_art() is True
    assert await _reads(client) == [True] * (ART_DESYNC_THRESHOLD + 1)


async def test_a_not_supported_model_from_the_tv_does_not_count_as_art():
    """-32001 says the model has no pictureModeControl, not what is on screen."""
    client = _wired(ERROR_NOT_SUPPORTED_MODEL)
    assert await client.async_generated_mode_is_art(HOME_SCREEN) is None
    assert await client.async_panel_shows_art() is False
    expected = [True] * (ART_DESYNC_THRESHOLD - 1) + [False, False]
    assert await _reads(client) == expected
