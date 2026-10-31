"""go_to_standby is ambiguous about art mode, and definite about power.

The handler deliberately leaves ``art_mode`` alone: a Frame also fires this
when it dims for its own sleep timer while still in Art Mode, and forcing the
value False there flipped art_mode_status off while artwork was on screen.

But the event is the TV saying it is on its way off, and a caller about to
send it an input key needs exactly that. Measured on a QE55LS03D, 31 October:
art off at 11:11:00.4, art_mode_changed 'off' at :00.7, go_to_standby at
:03.5, and the art exit source sent KEY_HDMI3 at :04.8 — 0.3 s before both
WebSocket threads terminated and the TV read standby. The announcement was in
hand 1.3 s before the key went out; nothing recorded it.
"""

import json


def _d2d(art, inner: dict) -> tuple[str, dict]:
    """Build the (event, response) pair _process_event expects."""
    return art.D2D_SERVICE_MESSAGE_EVENT, {
        "event": art.D2D_SERVICE_MESSAGE_EVENT,
        "data": json.dumps(inner),
    }


async def test_nothing_is_stamped_before_the_tv_says_anything(art_client):
    assert art_client.standby_last_broadcast is None


async def test_go_to_standby_is_stamped(art_client):
    import art

    event, response = _d2d(art, {"event": "go_to_standby"})
    await art_client._process_event(event, response)

    assert art_client.standby_last_broadcast is not None


async def test_go_to_standby_still_leaves_art_mode_alone(art_client):
    # The contract the handler's comment exists for: ambiguous about art mode.
    import art

    art_client.art_mode = True
    event, response = _d2d(art, {"event": "go_to_standby"})
    await art_client._process_event(event, response)

    assert art_client.art_mode is True


async def test_a_second_announcement_moves_the_stamp_forward(art_client):
    import art

    event, response = _d2d(art, {"event": "go_to_standby"})
    await art_client._process_event(event, response)
    first = art_client.standby_last_broadcast

    await art_client._process_event(event, response)

    assert art_client.standby_last_broadcast >= first


async def test_an_art_mode_broadcast_does_not_stamp_it(art_client):
    # Only the power announcement counts; leaving art mode is not leaving on.
    import art

    event, response = _d2d(art, {"event": "art_mode_changed", "status": "off"})
    await art_client._process_event(event, response)

    assert art_client.standby_last_broadcast is None
