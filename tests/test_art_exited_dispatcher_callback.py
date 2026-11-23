"""The art-exited dispatcher target must run on the event loop.

`_on_art_exited` is wired with `async_dispatcher_connect`. Home Assistant runs
a connected target that is a plain function in an executor thread, and a
`@callback` one on the event loop. The handler calls `hass.async_create_task`,
which is only valid on the loop, so without the decorator it raised a
RuntimeError from a SyncWorker thread on every Art-Mode-off — the dispatcher
swallowed it and the configured exit source never switched.

Seen in the maintainer's own log on 9.5.12: "Art Mode turned OFF", then
"calls hass.async_create_task from a thread other than the event loop", then
"Exception in _on_art_exited when dispatching 'samsungtv_smart_art_exited_…'".
"""

import sys
from types import ModuleType

from homeassistant.core import is_callback

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

from custom_components.samsungtv_smart.media_player import SamsungTVDevice  # noqa: E402


def test_on_art_exited_is_a_callback():
    # Else HA runs it in an executor thread and async_create_task raises there.
    assert is_callback(SamsungTVDevice._on_art_exited)


def test_the_wake_nudge_sibling_is_a_callback_too():
    # The pattern the fix restores parity with.
    assert is_callback(SamsungTVDevice._on_wake_nudge)
