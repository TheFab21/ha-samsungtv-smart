"""A launch the TV drops has to say so, once, and name the way out.

Both WebSocket launch methods are fire-and-forget. On a 2026 Frame Pro
(#351) the TV accepted ``ms.application.start`` and did nothing: the player
showed the app optimistically and fell back a poll later, with no error
anywhere. A REST POST to the same id brought the app up at once, and a wrong
id answered 404 — so the method works there and the WebSocket one does not.

The reporter found this by reading the app's own REST status and seeing
``visible: false`` stay put. That read is now taken after a WebSocket launch,
and a launch that went nowhere produces one warning naming the option to
change. It must stay quiet about an app that is merely slow, and about a TV
whose status cannot be read at all: a wrong warning here sends people
changing an option that works.
"""

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

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

from custom_components.samsungtv_smart import media_player as mp  # noqa: E402
from custom_components.samsungtv_smart.const import (  # noqa: E402
    CONF_APP_LAUNCH_METHOD,
    AppLaunchMethod,
)

Device = mp.SamsungTVDevice
YOUTUBE = "111299001912"


class _Log:
    """Collects what the device logged, by level."""

    def __init__(self):
        self.warnings = []
        self.debugs = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)

    def debug(self, msg, *args):
        self.debugs.append(msg % args if args else msg)

    def info(self, msg, *args):
        pass

    def error(self, msg, *args):
        pass


class _Rest:
    """A REST api that answers each status read from a scripted list."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.reads = []

    async def async_rest_app_status(self, app_id):
        self.reads.append(app_id)
        assert self.answers, "read the status more times than the test scripted"
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _device(answers=(), logged=False):
    device = object.__new__(Device)
    device._log = _Log()
    device._rest_api = _Rest(answers)
    device._app_launch_ignored_logged = logged
    return device


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Same number of reads, none of the 12 seconds between them."""
    monkeypatch.setattr(mp, "APP_LAUNCH_VERIFY_DELAYS", (0, 0, 0, 0))


CLOSED = {"visible": False, "running": False}
OPEN = {"visible": True, "running": True}


# --- what the check concludes ---------------------------------------------


def test_a_launch_the_tv_dropped_is_reported_once():
    device = _device([CLOSED] * 4)
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))

    assert len(device._log.warnings) == 1
    warning = device._log.warnings[0]
    assert YOUTUBE in warning
    # The option's own wording for the method that failed, not the
    # internal command name.
    assert "the Standard launch" in warning
    assert "the run_app launch" not in warning
    # Both ways out are named.
    assert "App launch method" in warning
    assert "@run_app_rest" in warning


def test_the_remote_method_is_named_by_its_option_label():
    device = _device([CLOSED] * 4)
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP_REMOTE))
    assert "Remote" in device._log.warnings[0]


def test_an_app_that_comes_up_late_is_not_reported():
    device = _device([CLOSED, CLOSED, OPEN, CLOSED])
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))
    assert device._log.warnings == []
    # Stopped as soon as the app was up: the fourth read never happened.
    assert len(device._rest_api.reads) == 3


def test_an_app_up_on_the_first_read_costs_one_read_and_no_warning():
    device = _device([OPEN])
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))
    assert device._log.warnings == []
    assert device._rest_api.reads == [YOUTUBE]


def test_a_status_that_cannot_be_read_is_no_verdict():
    device = _device([OSError("TV unreachable")])
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))
    assert device._log.warnings == []
    assert len(device._rest_api.reads) == 1
    assert any("Could not read the REST status" in d for d in device._log.debugs)


def test_a_status_without_visible_is_no_verdict():
    device = _device([{"running": False}])
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))
    assert device._log.warnings == []
    assert len(device._rest_api.reads) == 1
    assert any("no 'visible' status" in d for d in device._log.debugs)


def test_a_status_that_is_not_a_mapping_is_no_verdict():
    device = _device(["not json"])
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))
    assert device._log.warnings == []


def test_it_is_said_once_and_not_again():
    device = _device([CLOSED] * 8)
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))
    asyncio.run(device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP))
    assert len(device._log.warnings) == 1


def test_a_check_already_in_flight_cannot_double_up():
    # Two launches in a row both schedule a check before either has logged;
    # the latch is re-read after the reads, not only before them.
    device = _device([CLOSED] * 8)

    async def both():
        await asyncio.gather(
            device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP),
            device._async_check_app_launched(YOUTUBE, mp.CMD_RUN_APP),
        )

    asyncio.run(both())
    assert len(device._log.warnings) == 1


# --- when the check is scheduled at all -----------------------------------


def _launcher(method_value, app_list=None):
    """A device whose only job is to record what _async_launch_app did."""
    device = object.__new__(Device)
    device._log = _Log()
    device._app_list = app_list if app_list is not None else {"YouTube": YOUTUBE}
    device._app_launch_ignored_logged = False
    device.sent = []
    device.checked = []

    async def async_send_command(payload, command_type):
        device.sent.append((payload, command_type))

    def get_option(key, default=None):
        assert key == CONF_APP_LAUNCH_METHOD
        return method_value

    def create_task(coro):
        coro.close()
        device.checked.append(True)

    device.async_send_command = async_send_command
    device._get_option = get_option
    device.hass = SimpleNamespace(async_create_task=create_task)
    return device


def test_a_standard_launch_is_verified():
    device = _launcher(AppLaunchMethod.Standard.value)
    asyncio.run(device._async_launch_app("YouTube"))
    assert device.sent == [(YOUTUBE, mp.CMD_RUN_APP)]
    assert device.checked == [True]


def test_a_remote_launch_is_verified():
    device = _launcher(AppLaunchMethod.Remote.value)
    asyncio.run(device._async_launch_app("YouTube"))
    assert device.sent == [(YOUTUBE, mp.CMD_RUN_APP_REMOTE)]
    assert device.checked == [True]


def test_a_rest_launch_has_nothing_to_verify():
    device = _launcher(AppLaunchMethod.Rest.value)
    asyncio.run(device._async_launch_app("YouTube"))
    assert device.sent == [(YOUTUBE, mp.CMD_RUN_APP_REST)]
    assert device.checked == []


def test_the_documented_per_app_suffix_picks_the_rest_method():
    # README: media_content_id "YouTube@run_app_rest" in a play_media call.
    device = _launcher(AppLaunchMethod.Standard.value)
    asyncio.run(device._async_launch_app("YouTube@run_app_rest"))
    assert device.sent == [(YOUTUBE, mp.CMD_RUN_APP_REST)]
    assert device.checked == []


def test_a_per_app_websocket_suffix_is_still_verified():
    device = _launcher(AppLaunchMethod.Rest.value)
    asyncio.run(device._async_launch_app("YouTube@run_app_remote"))
    assert device.sent == [(YOUTUBE, mp.CMD_RUN_APP_REMOTE)]
    assert device.checked == [True]


def test_nothing_is_scheduled_once_it_has_been_said():
    device = _launcher(AppLaunchMethod.Standard.value)
    device._app_launch_ignored_logged = True
    asyncio.run(device._async_launch_app("YouTube"))
    assert device.sent == [(YOUTUBE, mp.CMD_RUN_APP)]
    assert device.checked == []


def test_the_meta_data_suffix_does_not_reach_the_status_read():
    # A deep link sends "<id>,,<meta>" on the remote channel; the REST status
    # read wants the id on its own, or it would 404 on every TV.
    checked = []
    device = _launcher(AppLaunchMethod.Standard.value)

    async def check(app_id, method):
        checked.append((app_id, method))

    def create_task(coro):
        asyncio.get_running_loop().create_task(coro)
        device.checked.append(True)

    device._async_check_app_launched = check
    device.hass = SimpleNamespace(async_create_task=create_task)

    async def run():
        await device._async_launch_app("YouTube", meta_data="v=abc")
        await asyncio.sleep(0)

    asyncio.run(run())

    assert device.sent == [(f"{YOUTUBE},,v=abc", mp.CMD_RUN_APP_REMOTE)]
    assert checked == [(YOUTUBE, mp.CMD_RUN_APP_REMOTE)]
