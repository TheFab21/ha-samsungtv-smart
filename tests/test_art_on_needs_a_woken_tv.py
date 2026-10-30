"""A sleeping Frame answers the art channel, and lies about art mode (#319).

The reported morning, 30 October. A script asked for Art Mode at 19:44:59 and
the TV never came on, yet Home Assistant showed Art Mode ON:

    19:44:59.950  switch   TV is off, turning it on first...
    19:45:06.979  wol      WOL packet sent (5 time(s))        one burst, missed
    19:45:07→19:46:02      REST: PowerState=standby, 13 readings in a row
    19:45:07→19:46:02      IP Control: artModeControl = False, every 5 s
    19:45:08.071  switch   TV ready after 1 seconds           <- (A)
    19:45:16.073  switch   Art Mode ON attempt 1/5
    19:45:21.222  art      Timeout waiting for the set
    19:45:21.244  art      get_artmode_status -> {'value': 'on'}
    19:45:21.245  switch   Art Mode is already ON (confirmed by get_artmode)
                           -> returned, attempts 2-5 never ran               <- (B)

Two separate faults. (A) the readiness probe asks the art channel, which a TV
in standby answers on port 8001, so it declared the TV ready one second after
a power-on it had ignored. (B) ``get_artmode`` on a sleeping Frame returns its
STORED art flag — what it will show when it wakes — and taking that as proof
reported success for a dark panel and abandoned the remaining attempts.

The same TV woke on his next try, after a SECOND WOL burst. The wake itself is
his network's problem; reporting success for it is ours.
"""

from contextlib import nullcontext
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart import switch as sw  # noqa: E402
from custom_components.samsungtv_smart.const import DOMAIN  # noqa: E402

ArtSwitch = sw.FrameArtModeSwitch
ENTRY_ID = "entry-319"


def _switch(**attrs):
    """An art switch with only what the paths under test touch."""
    entity = object.__new__(ArtSwitch)
    entity._log = MagicMock()
    entity._device_name = "Frame TV"
    entity._entry = SimpleNamespace(entry_id=ENTRY_ID)
    entity._hass = SimpleNamespace(data={DOMAIN: {ENTRY_ID: {}}})
    entity._attr_is_on = None
    entity._available = False
    entity._pending_art_on = False
    entity._optimistic_value = None
    entity.async_write_ha_state = MagicMock()
    for name, value in attrs.items():
        setattr(entity, name, value)
    return entity


# ── (A) the readiness probe ────────────────────────────────────────────────


async def test_the_art_channel_alone_does_not_mean_the_tv_is_awake():
    entity = _switch(
        _art_api=SimpleNamespace(supported=AsyncMock(return_value=True)),
        _is_tv_on=AsyncMock(return_value=False),
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        assert await entity._wait_for_tv_ready(max_wait=3) is False

    # It kept asking rather than giving up on the first answer.
    assert entity._art_api.supported.await_count == 3
    assert entity._is_tv_on.await_count == 3
    said = " ".join(str(c.args[0]) for c in entity._log.debug.call_args_list)
    assert "a TV in standby also does" in said
    entity._log.warning.assert_called_once()


async def test_an_awake_tv_answering_the_art_channel_is_ready():
    entity = _switch(
        _art_api=SimpleNamespace(supported=AsyncMock(return_value=True)),
        _is_tv_on=AsyncMock(return_value=True),
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        assert await entity._wait_for_tv_ready(max_wait=20) is True

    assert entity._art_api.supported.await_count == 1
    entity._log.warning.assert_not_called()


async def test_a_tv_that_wakes_on_the_third_second_is_waited_for():
    entity = _switch(
        _art_api=SimpleNamespace(supported=AsyncMock(return_value=True)),
        _is_tv_on=AsyncMock(side_effect=[False, False, True]),
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        assert await entity._wait_for_tv_ready(max_wait=20) is True

    assert entity._is_tv_on.await_count == 3


async def test_a_silent_art_channel_is_not_probed_for_power():
    # Unchanged behaviour, and no reason to ask: there is nothing to write to.
    entity = _switch(
        _art_api=SimpleNamespace(supported=AsyncMock(return_value=False)),
        _is_tv_on=AsyncMock(return_value=True),
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        assert await entity._wait_for_tv_ready(max_wait=2) is False

    entity._is_tv_on.assert_not_awaited()


async def test_a_frame_waking_straight_into_art_mode_counts_as_awake():
    """The real _is_tv_on, because this is the case it exists for.

    A Frame showing art publishes media_player 'off' with art_mode_status
    'on'. Requiring the power state must not start calling that asleep.
    """
    entity = _switch(
        _art_api=SimpleNamespace(supported=AsyncMock(return_value=True)),
        _get_media_player_entity_id=MagicMock(return_value="media_player.frame"),
    )
    entity._hass.states = SimpleNamespace(
        get=lambda _entity_id: SimpleNamespace(
            state="off", attributes={"art_mode_status": "on"}
        )
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        assert await entity._wait_for_tv_ready(max_wait=5) is True


# ── (B) "already ON (confirmed by get_artmode)" ────────────────────────────


class _Guard:
    def __init__(self):
        self.verified = []

    def pending(self, _value):
        return None

    def record_verified(self, value):
        self.verified.append(value)


def _wake_path(entity, monkeypatch, guard):
    """Stub everything between the service call and the attempt loop."""
    entity._turn_on_tv = AsyncMock(return_value=True)
    # The old probe's verdict: the art channel answered, so this said yes even
    # on a TV that never woke. (A) fixes that; (B) must hold anyway.
    entity._wait_for_tv_ready = AsyncMock(return_value=True)
    entity._get_media_player_entity_id = MagicMock(return_value=None)
    entity._set_optimistic = MagicMock()
    monkeypatch.setattr(sw, "guard_for", lambda _data: guard)
    monkeypatch.setattr(sw, "settle_after_source_switch", AsyncMock(return_value=0.0))
    monkeypatch.setattr(sw, "priority_requests", nullcontext)


async def test_a_stored_art_flag_on_a_sleeping_tv_is_not_success(monkeypatch):
    guard = _Guard()
    entity = _switch(
        _art_api=SimpleNamespace(
            art_mode_broadcast_count=0,
            get_artmode=AsyncMock(return_value="on"),
        ),
        _is_tv_on=AsyncMock(return_value=False),
        _set_artmode=AsyncMock(return_value=None),
    )
    _wake_path(entity, monkeypatch, guard)

    with patch("asyncio.sleep", new=AsyncMock()):
        await entity.async_turn_on()

    # Every attempt ran instead of the first one being called a success.
    assert entity._set_artmode.await_count == 5
    assert guard.verified == []
    assert entity._attr_is_on is not True
    # And the user is told, rather than shown a switch that is on.
    assert any(
        "Failed to turn Art Mode ON" in str(c.args[0])
        for c in entity._log.warning.call_args_list
    )
    said = " ".join(str(c.args[0]) for c in entity._log.debug.call_args_list)
    assert "stored flag, not the panel" in said


async def test_the_same_answer_on_an_awake_tv_is_still_accepted(monkeypatch):
    # The case the short-circuit exists for: the TV is in Art Mode already, so
    # set_artmode is a no-op that answers nothing.
    guard = _Guard()
    entity = _switch(
        _art_api=SimpleNamespace(
            art_mode_broadcast_count=0,
            get_artmode=AsyncMock(return_value="on"),
        ),
        _is_tv_on=AsyncMock(return_value=True),
        _set_artmode=AsyncMock(return_value=None),
    )
    _wake_path(entity, monkeypatch, guard)

    with patch("asyncio.sleep", new=AsyncMock()):
        await entity.async_turn_on()

    assert entity._set_artmode.await_count == 1
    assert guard.verified == [True]
    assert entity._attr_is_on is True
    assert any("already ON" in str(c.args[0]) for c in entity._log.info.call_args_list)


async def test_a_pending_write_still_suppresses_the_read_entirely(monkeypatch):
    # Unchanged: a just-read panel showing no art outranks the art channel
    # (#273), and the power check must not start calling get_artmode anyway.
    class _Pending(_Guard):
        def pending(self, _value):
            return True

    guard = _Pending()
    entity = _switch(
        _art_api=SimpleNamespace(
            art_mode_broadcast_count=0,
            get_artmode=AsyncMock(return_value="on"),
        ),
        _is_tv_on=AsyncMock(return_value=True),
        _set_artmode=AsyncMock(return_value=None),
    )
    _wake_path(entity, monkeypatch, guard)

    with patch("asyncio.sleep", new=AsyncMock()):
        await entity.async_turn_on()

    entity._art_api.get_artmode.assert_not_awaited()
    assert guard.verified == []
    # Three, not five: this TV was already awake, so async_turn_on took the
    # no-wake path, which uses the shorter retry budget.
    assert entity._set_artmode.await_count == 3
