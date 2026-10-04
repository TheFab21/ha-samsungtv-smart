"""A WebSocket art-mode write is verified by a real read-back, not a cache (#290, #248).

#290 (QE55LS03B, no IP Control): Art Mode on, off, on within 60 s was refused
with "art mode 'on' was already written 31s ago and did not take". The write
guard is only right if each write is read back. On that TV the two directions
confirm in opposite orders:

  ON   art_mode_changed 'on' broadcast +0.49 s, set_artmode_status reply +2.01 s
  OFF  set_artmode_status reply +0.06/+0.68 s, 'off' broadcast +4.40/+1.43 s

The reply does not update the art channel's art_mode (#264). So checking
art_mode right after set_artmode returns verifies the ON but never the OFF;
the OFF is verified in the background when its broadcast lands, so the switch
still flips as soon as the TV has replied.
art_mode is also exactly what set_artmode already compared to report success,
so a cache that already held the requested value "verified" a write the TV
never confirmed.

#248 (13 Frames, IP Control paired, "Enable IP Control Art Mode" off): the art
channel froze for hours, so a WebSocket write there is read back from the panel
(getTVStates.pictureMode), as in the IP Control branch. The art channel may
never clear a record the panel set.

switch.py and art.py need Home Assistant / aiohttp to import. The tests run the
real _set_artmode, async_turn_on and async_turn_off, extracted from switch.py.
They drive them against the real set_artmode and _process_event from
api/art.py over a scripted transport replaying the #290 log's event order, and
against the real ArtModeWriteGuard. Time is compressed 25x.
"""

import ast
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SWITCH_PATH = ROOT / "switch.py"
SCALE = 25.0  # virtual seconds per real second (margins stay >= 20 ms real)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard_mod = _load("art_mode_guard_ws_readback", ROOT / "art_mode_guard.py")


def _load_art():
    """Load api/art.py in a private package, stubbing aiohttp if it's absent."""
    pkg_name = "_ws_readback_api"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ROOT / "api")]
    sys.modules[pkg_name] = pkg
    stubbed = False
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")
        stubbed = True
    try:
        _load(f"{pkg_name}._image_prep", ROOT / "api" / "_image_prep.py")
        return _load(f"{pkg_name}.art", ROOT / "api" / "art.py")
    finally:
        if stubbed:
            del sys.modules["aiohttp"]


art = _load_art()


def vnow():
    return asyncio.get_running_loop().time() * SCALE


class _Loop:
    def __init__(self, loop):
        self._loop = loop

    def time(self):
        return self._loop.time() * SCALE


class _Asyncio:
    """What switch.py sees as `asyncio`: the same, with time compressed."""

    TimeoutError = asyncio.TimeoutError

    @staticmethod
    async def sleep(seconds):
        await asyncio.sleep(seconds / SCALE)

    @staticmethod
    def timeout(seconds):
        return asyncio.timeout(seconds / SCALE)

    @staticmethod
    def get_running_loop():
        return _Loop(asyncio.get_running_loop())


class _IPError(Exception):
    pass


def _constants(*names):
    """Read module-level float constants from switch.py, so the tests follow them."""
    source = SWITCH_PATH.read_text()
    return {
        name: float(re.search(rf"^{name} = ([0-9.]+)$", source, re.M).group(1))
        for name in names
    }


def _switch_methods():
    tree = ast.parse(SWITCH_PATH.read_text())
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(getattr(f, "name", "") == "_set_artmode" for f in node.body)
    )
    wanted = {
        "_set_artmode",
        "_panel_shows_art",
        "_panel_read_back",
        "_confirm_by_broadcast",
        "async_turn_on",
        "async_turn_off",
        "_verify_art_mode_broadcast",
        "_panel_lags_broadcast",
        "_settle_panel_conflict",
        "_broadcast_confirms",
    }
    namespace = {
        "asyncio": _Asyncio(),
        "guard_for": guard_mod.guard_for,
        "DOMAIN": "samsungtv_smart",
        "ArtModeWriteSuppressed": guard_mod.ArtModeWriteSuppressed,
        "SamsungIPControlError": _IPError,
        "Any": object,
        **_constants(
            "ART_MODE_BROADCAST_VERIFY_WINDOW",
            "ART_MODE_PANEL_READ_BACK_WINDOW",
            "ART_MODE_PANEL_CONFLICT_WINDOW",
        ),
    }
    for node in cls.body:
        if getattr(node, "name", "") in wanted:
            code = compile(ast.Module(body=[node], type_ignores=[]), "switch", "exec")
            exec(code, namespace)  # noqa: S102 - our own source, under test
    return {name: namespace[name] for name in wanted}


METHODS = _switch_methods()


class _Log:
    def __init__(self):
        self.lines = []

    def _log(self, level, msg, *args):
        self.lines.append((level, msg % args if args else msg))

    def debug(self, msg, *args):
        self._log("DEBUG", msg, *args)

    def info(self, msg, *args):
        self._log("INFO", msg, *args)

    def warning(self, msg, *args):
        self._log("WARNING", msg, *args)


class _Tv:
    """The panel. It follows each WebSocket write after `lag` s, or never."""

    def __init__(self, shows_art=False, moves=True, lag=1.0):
        self.shows_art = shows_art
        self.moves = moves
        self.lag = lag
        self.changes = []

    def written(self, value):
        if self.moves:
            self.changes.append((vnow() + self.lag, value))

    def panel(self):
        value = self.shows_art
        for at, written in self.changes:
            if at <= vnow():
                value = written
        return value


class _IpClient:
    def __init__(self, tv):
        self.tv = tv

    async def async_panel_shows_art(self):
        await asyncio.sleep(0.05 / SCALE)
        return self.tv.panel()


class _ArtChannel(art.SamsungTVAsyncArt):
    """Real set_artmode / _process_event over a scripted transport.

    Each set_artmode_status request plays the next script: a list of
    ("reply" | "broadcast", "on" | "off", seconds after the request). A
    script with no reply times out after 5 s, like the real request.
    """

    def __init__(self, art_mode, ws_get=None):
        self.art_mode = art_mode
        self.art_mode_broadcast_count = 0
        self.art_mode_broadcast_at = {}
        self._art_mode_broadcast_waiters = []
        self._pending_requests = {}
        self._log = logging.getLogger(__name__)
        self.ws_get = ws_get
        self.scripts = []
        self.writes = 0
        self.on_write = None

    def _fire_art_event(self):
        pass

    async def _event(self, kind, status):
        if kind == "broadcast":
            data = {"event": "art_mode_changed", "status": status}
        else:
            data = {"event": "set_artmode_status", "request_id": "r", "status": status}
        await self._process_event(
            art.D2D_SERVICE_MESSAGE_EVENT, {"data": json.dumps(data)}
        )

    async def _send_art_request(self, request, **kwargs):
        if request["request"] == "get_artmode_status":
            return None if self.ws_get is None else {"value": self.ws_get}
        self.writes += 1
        if self.on_write:
            self.on_write(request["value"] == "on")
        script = self.scripts.pop(0) if self.scripts else []
        reply = asyncio.get_running_loop().create_future()

        async def play():
            elapsed = 0.0
            for kind, status, at in sorted(script, key=lambda e: e[2]):
                await asyncio.sleep((at - elapsed) / SCALE)
                elapsed = at
                await self._event(kind, status)
                if kind == "reply" and not reply.done():
                    reply.set_result({"status": status})

        asyncio.ensure_future(play())
        try:
            return await asyncio.wait_for(asyncio.shield(reply), 5 / SCALE)
        except asyncio.TimeoutError:
            return None


class _Switch:
    """Just enough of the Art Mode switch to run its write path."""

    def __init__(self, channel, tv=None):
        self._art_api = channel
        self._tv = tv
        self._ip = _IpClient(tv) if tv else None
        if tv:
            channel.on_write = tv.written
        self.tasks = []
        self._hass = types.SimpleNamespace(
            data={"samsungtv_smart": {"e": {}}},
            async_create_background_task=self._background,
        )
        self._entry = types.SimpleNamespace(
            entry_id="e",
            options={},
            data={},
            async_create_background_task=lambda hass, coro, name: self._background(
                coro, name
            ),
        )
        self._log = _Log()
        self._device_name = "Frame"
        self._pending_art_on = False
        self._attr_is_on = None
        self._available = True
        self.guard = guard_mod.ArtModeWriteGuard(clock=vnow)
        self._hass.data["samsungtv_smart"]["e"][
            guard_mod.DATA_ART_MODE_GUARD
        ] = self.guard

    def _background(self, coro, name=None):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    def __getattr__(self, name):
        method = METHODS[name]
        return lambda *a, **k: method(self, *a, **k)

    # IP Control paired (panel readable) or not; the art-mode option is off.
    def _get_ip_control(self):
        return self._ip

    def _ip_control_art_mode(self):
        return False

    def _get_media_player_entity_id(self):
        return None

    async def _is_tv_on(self):
        return True

    def _set_optimistic(self, value):
        self._attr_is_on = value

    def async_write_ha_state(self):
        pass

    def refused(self):
        return [m for level, m in self._log.lines if "already written" in m]


ON = [("broadcast", "on", 0.49), ("reply", "on", 2.01)]  # 12:38:34
OFF = [("reply", "off", 0.058), ("broadcast", "off", 4.40)]  # 12:38:50
OFF_1 = [("reply", "off", 0.683), ("broadcast", "off", 1.43)]  # 12:33:30


class _Base(unittest.IsolatedAsyncioTestCase):
    async def wait(self, seconds):
        await asyncio.sleep(seconds / SCALE)

    async def at(self, t):
        delay = self.start + t - vnow()
        if delay > 0:
            await asyncio.sleep(delay / SCALE)

    async def toggles(self, switch, steps):
        self.start = vnow()
        for t, turn_on, scripts in steps:
            await self.at(t)
            switch._art_api.scripts = [list(s) for s in scripts]
            if turn_on:
                await switch.async_turn_on()
            else:
                await switch.async_turn_off()


class NoIpControlTest(_Base):
    """#290: the only read-back is the TV's own art_mode_changed broadcast."""

    async def test_on_off_on_off_within_the_cooldown(self):
        sw = _Switch(_ArtChannel(art_mode=False))
        await self.toggles(
            sw,
            [
                (0, True, [ON]),
                (16.8, False, [OFF]),
                (31, True, [ON]),
                (46, False, [OFF]),
            ],
        )
        self.assertEqual(sw.refused(), [])
        self.assertEqual(sw._art_api.writes, 4)
        await self.wait(5)  # the last OFF is verified when its broadcast lands
        self.assertIsNone(sw.guard.pending(True))
        self.assertIsNone(sw.guard.pending(False))

    async def test_an_off_confirmed_by_its_reply_first_is_verified(self):
        # The reply lands 4.3 s before the broadcast; art_mode is still True
        # when set_artmode returns. #291 recorded this OFF as "did not take".
        sw = _Switch(_ArtChannel(art_mode=True))
        await self.toggles(
            sw, [(0, False, [OFF]), (15, True, [ON]), (30, False, [OFF])]
        )
        self.assertEqual(sw.refused(), [])
        self.assertEqual(sw._art_api.writes, 3)

    async def test_the_logged_off_then_off_15s_later(self):
        # 12:33:30 OFF, 12:33:46 OFF: the second one was refused ("15s ago").
        sw = _Switch(_ArtChannel(art_mode=True))
        await self.toggles(
            sw, [(0, False, [OFF_1]), (15.4, False, [[("reply", "off", 0.1)]])]
        )
        self.assertEqual(sw.refused(), [])

    async def test_an_on_confirmed_before_return_does_not_wait(self):
        sw = _Switch(_ArtChannel(art_mode=False))
        sw._art_api.scripts = [list(ON)]
        start = vnow()
        await sw._set_artmode(True)
        self.assertLess(vnow() - start, 1.0)
        self.assertIsNone(sw.guard.pending(True))

    async def test_an_off_returns_before_its_broadcast(self):
        # The #290 OFF: reply +0.06 s, broadcast +4.40 s. The switch must not
        # wait for the broadcast; the record is cleared when it lands.
        sw = _Switch(_ArtChannel(art_mode=True))
        sw._art_api.scripts = [list(OFF)]
        start = vnow()
        self.assertTrue(await sw._set_artmode(False))
        self.assertLess(vnow() - start, 1.0)
        self.assertIsNotNone(sw.guard.pending(False))
        await self.wait(5)
        self.assertIsNone(sw.guard.pending(False))

    async def test_the_background_read_back_gives_up_after_its_window(self):
        sw = _Switch(_ArtChannel(art_mode=True))
        sw._art_api.scripts = [[("reply", "off", 0.1)]]  # never broadcast
        self.assertTrue(await sw._set_artmode(False))
        await self.wait(9)
        self.assertTrue(all(t.done() for t in sw.tasks))
        self.assertIsNotNone(sw.guard.pending(False))

    async def test_a_redundant_off_does_not_block_a_later_genuine_off(self):
        # Art already off; SmartThings lag shows the switch on, so OFF is
        # pressed anyway and the TV only replies. Then a confirmed ON, then a
        # genuine OFF within 60 s. The redundant OFF's record must not refuse
        # it and leave the TV in Art Mode (#290 by another route).
        sw = _Switch(_ArtChannel(art_mode=False))
        silent_off = [("reply", "off", 0.1)]
        await self.toggles(
            sw, [(0, False, [silent_off]), (10, True, [ON]), (25, False, [OFF])]
        )
        self.assertEqual(sw.refused(), [])
        self.assertEqual(sw._art_api.writes, 3)

    async def test_a_redundant_write_the_tv_broadcasts_is_confirmed(self):
        # Same redundant OFF, but the TV broadcasts 'off' for it: confirmed by
        # the event even though the cache already held False.
        sw = _Switch(_ArtChannel(art_mode=False))
        await self.toggles(
            sw, [(0, False, [[("reply", "off", 0.1), ("broadcast", "off", 0.6)]])]
        )
        await self.wait(1)
        self.assertIsNone(sw.guard.pending(False))

    async def test_a_broadcast_between_two_polls_is_not_missed(self):
        # off then on again 0.1 s apart, both after the write returned: the
        # verifier looks the broadcast up by number, not by the current value.
        sw = _Switch(_ArtChannel(art_mode=True))
        flicker = [
            ("reply", "off", 0.05),
            ("broadcast", "off", 1.0),
            ("broadcast", "on", 1.1),
        ]
        sw._art_api.scripts = [flicker]
        self.assertTrue(await sw._set_artmode(False))
        await self.wait(2)
        self.assertIsNone(sw.guard.pending(False))

    async def test_a_write_the_tv_never_confirms_is_still_recorded(self):
        # Reply, no broadcast: the loop the guard exists to stop (#248).
        sw = _Switch(_ArtChannel(art_mode=True))
        silent = [[("reply", "off", 0.1)]]
        await self.toggles(sw, [(0, False, silent), (20, False, silent)])
        self.assertEqual(sw._art_api.writes, 1)
        self.assertEqual(len(sw.refused()), 1)

    async def test_a_cache_already_at_the_request_is_not_a_read_back(self):
        # set_artmode returns True on art_mode == desired even after a
        # timeout. That is not the TV confirming this write.
        sw = _Switch(_ArtChannel(art_mode=True))
        sw._art_api.scripts = [[]]  # no reply, no broadcast
        self.assertTrue(await sw._set_artmode(True))
        self.assertIsNotNone(sw.guard.pending(True))


class PairedOptionOffTest(_Base):
    """#248: IP Control paired, "Enable IP Control Art Mode" off."""

    async def test_on_off_on_off_is_read_back_from_the_panel(self):
        # The art channel is frozen (no broadcast); the panel moves.
        tv = _Tv(shows_art=False, moves=True, lag=1.0)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        reply_on, reply_off = [("reply", "on", 0.3)], [("reply", "off", 0.3)]
        await self.toggles(
            sw,
            [
                (0, True, [reply_on]),
                (15, False, [reply_off]),
                (31, True, [reply_on]),
                (46, False, [reply_off]),
            ],
        )
        self.assertEqual(sw.refused(), [])
        self.assertEqual(sw._art_api.writes, 4)

    async def test_a_panel_slower_than_the_read_back_is_picked_up_by_the_retry(self):
        tv = _Tv(shows_art=False, moves=True, lag=3.0)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        await self.toggles(sw, [(0, True, [[("reply", "on", 0.3)]] * 3)])
        self.assertEqual(sw._art_api.writes, 1)
        self.assertIsNone(sw.guard.pending(True))
        self.assertTrue(sw._attr_is_on)

    async def test_a_stale_cache_at_the_request_does_not_clear_the_record(self):
        # Cache latched 'on' (#273), panel never moves, the TV replies.
        # #291 recorded each write verified and wrote on every run.
        tv = _Tv(shows_art=False, moves=False)
        sw = _Switch(_ArtChannel(art_mode=True), tv)
        runs = [(t, True, [[("reply", "on", 0.3)]] * 3) for t in (0, 20, 40)]
        await self.toggles(sw, runs)
        self.assertEqual(sw._art_api.writes, 1)

    async def test_the_art_channel_does_not_overrule_the_panel(self):
        # Same, and the live get_artmode() says 'on' while the panel shows an
        # input: the turn-on retry must not let it clear the record.
        tv = _Tv(shows_art=False, moves=False)
        sw = _Switch(_ArtChannel(art_mode=True, ws_get="on"), tv)
        runs = [(t, True, [[("reply", "on", 0.3)]] * 3) for t in (0, 20, 40)]
        await self.toggles(sw, runs)
        self.assertEqual(sw._art_api.writes, 1)
        self.assertIsNotNone(sw.guard.pending(True))

    async def test_an_off_that_times_out_on_a_stale_cache_is_not_repeated(self):
        tv = _Tv(shows_art=True, moves=False)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        runs = [(t, False, [[]] * 3) for t in (0, 20, 40)]
        await self.toggles(sw, runs)
        self.assertEqual(sw._art_api.writes, 1)

    async def test_a_panel_that_catches_up_is_logged_as_a_late_confirmation(self):
        # Panel slower than the whole read-back window: the retry's pre-check
        # finds it and says so, instead of calling the reading stale.
        tv = _Tv(shows_art=False, moves=True, lag=5.5)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        await self.toggles(sw, [(0, True, [[("reply", "on", 0.3)]] * 3)])
        self.assertEqual(sw._art_api.writes, 1)
        self.assertIsNone(sw.guard.pending(True))
        messages = [m for level, m in sw._log.lines]
        self.assertTrue(any("confirmed on re-check" in m for m in messages))
        self.assertFalse(any("reading was stale" in m for m in messages))

    async def test_an_unreadable_panel_falls_back_to_the_broadcast(self):
        class _Unreadable(_IpClient):
            async def async_panel_shows_art(self):
                return None

        sw = _Switch(_ArtChannel(art_mode=True))
        sw._ip = _Unreadable(None)
        sw._art_api.scripts = [list(OFF)]
        self.assertTrue(await sw._set_artmode(False))
        await self.wait(5)
        self.assertIsNone(sw.guard.pending(False))

    async def test_the_record_stands_if_the_read_back_is_cut_short(self):
        # The caller's per-attempt timeout fires while the panel read-back is
        # still waiting: the record set before it must stand.
        tv = _Tv(shows_art=False, moves=True, lag=1.0)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        sw._art_api.scripts = [[("reply", "on", 0.05)]]
        hang = asyncio.get_running_loop().create_future()

        async def never(*args, **kwargs):
            await hang

        sw.__dict__["_panel_read_back"] = never
        with self.assertRaises(asyncio.TimeoutError):
            async with asyncio.timeout(2.0 / SCALE):
                await sw._set_artmode(True)
        self.assertEqual(sw._art_api.writes, 1)
        self.assertIsNotNone(sw.guard.pending(True))


class PanelLagsBroadcastTest(_Base):
    """IP Control paired, the TV broadcasts the write, the panel lags behind.

    Frame chambre (QE32LS03C), 2 Oct 17:20, woken by an automation:

      17:20:18.0  set_artmode_status(on)       -> no reply within 5 s
      17:20:24.75 art_mode_changed 'on', late reply 'on', get_artmode 'on'
      17:20:26.9  set_artmode_status(on) again  (the retry; already on)
      17:20:33-36 getTVStates: not "Ambient"   -> "panel did not change"
      17:20:42.8  next attempt refused: "already written 11s ago"

    The TV said "on" four ways after the first write; the panel read-back
    overruled all of them. A broadcast of the requested state received after
    the write now marks it accepted and the panel is re-read in the
    background. The record stays provisional, so no write is added.
    """

    async def test_the_logged_wake_succeeds_without_a_refusal(self):
        # Broadcast + reply +6.7 s after the first write (past its 5 s
        # timeout), the retry gets nothing. The panel was still not "Ambient"
        # 18 s after the first write in the log; here it follows at +25 s.
        tv = _Tv(shows_art=False, moves=True, lag=25.0)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        late = [("broadcast", "on", 6.7), ("reply", "on", 6.7)]
        await self.toggles(sw, [(0, True, [late, [], []])])
        self.assertEqual(sw.refused(), [])
        self.assertEqual(sw._art_api.writes, 1)  # the retry does not rewrite
        self.assertTrue(sw._attr_is_on)
        warnings = [m for level, m in sw._log.lines if level == "WARNING"]
        self.assertFalse(any("did not change" in m for m in warnings), warnings)
        await self.wait(25)
        self.assertIsNone(sw.guard.pending(True))  # confirmed once it caught up

    async def test_a_lagging_panel_confirms_the_write_when_it_catches_up(self):
        tv = _Tv(shows_art=False, moves=True, lag=8.0)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        sw._art_api.scripts = [list(ON)]
        self.assertTrue(await sw._set_artmode(True))
        self.assertEqual(sw._art_api.writes, 1)
        self.assertIsNotNone(sw.guard.pending(True))  # provisional meanwhile
        await self.wait(10)
        self.assertIsNone(sw.guard.pending(True))
        self.assertTrue(any("caught up" in m for _, m in sw._log.lines))

    async def test_a_panel_that_never_follows_is_reported_and_not_rewritten(self):
        # The broadcast was wrong, or the panel getter is: either way say so,
        # keep the record, and refuse a rewrite within the cooldown (#248).
        tv = _Tv(shows_art=False, moves=False)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        sw._art_api.scripts = [list(ON)]
        self.assertTrue(await sw._set_artmode(True))
        await self.wait(22)
        warnings = [m for level, m in sw._log.lines if level == "WARNING"]
        self.assertTrue(any("disagree" in m for m in warnings), warnings)
        self.assertIsNotNone(sw.guard.pending(True))
        sw._art_api.scripts = [list(ON)]
        await sw.async_turn_on()
        self.assertEqual(sw._art_api.writes, 1)
        self.assertEqual(len(sw.refused()), 1)

    async def test_a_broadcast_from_before_the_write_does_not_count(self):
        # An old 'on' broadcast, then a write the TV only replies to while the
        # panel stays put: that is the #248 case and must still fail.
        tv = _Tv(shows_art=False, moves=False)
        channel = _ArtChannel(art_mode=False)
        channel.art_mode_broadcast_count = 5
        channel.art_mode_broadcast_at = {True: 5}
        sw = _Switch(channel, tv)
        sw._art_api.scripts = [[("reply", "on", 0.3)]]
        self.assertFalse(await sw._set_artmode(True))
        warnings = [m for level, m in sw._log.lines if level == "WARNING"]
        self.assertTrue(any("did not change" in m for m in warnings), warnings)
        self.assertEqual(sw.tasks, [])

    async def test_an_unreadable_panel_during_the_re_check_stays_quiet(self):
        tv = _Tv(shows_art=False, moves=False)
        sw = _Switch(_ArtChannel(art_mode=False), tv)
        sw._art_api.scripts = [list(ON)]
        self.assertTrue(await sw._set_artmode(True))

        class _GoneQuiet(_IpClient):
            async def async_panel_shows_art(self):
                raise _IPError("refused")

        sw._ip = _GoneQuiet(tv)
        await self.wait(22)
        warnings = [m for level, m in sw._log.lines if level == "WARNING"]
        self.assertFalse(any("disagree" in m for m in warnings), warnings)


if __name__ == "__main__":
    unittest.main()
