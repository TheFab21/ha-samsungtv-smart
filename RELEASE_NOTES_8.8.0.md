# Release notes — 8.8.0

If this project is useful to you, you can support its development:

# <a href="https://buymeacoffee.com/thefab21" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-black.png" alt="Buy Me A Coffee" height="41" width="174"></a>

> **Status: stable release.** This note covers everything since **8.7.1**
> (8.7.2 → 8.7.9 plus this release). Nothing to reconfigure. Two things may
> change what you see in the log or in an automation — both are called out
> below under [What may look different](#what-may-look-different).

## ⚠️ If you disabled "Enable IP Control Art Mode" on our advice, read this

Our README has long said: do not enable *Enable IP Control Art Mode* unless
you know your firmware handles it — it can break Art Mode entirely and may
need a factory reset (seen on a QE55LS03D). And it said that with the option
off, Art Mode switching falls back to the WebSocket channel.

**Until 8.7.7 that was only half true.** The option disabled the
`artModeControl` *read*. Every Art Mode *write* — the Art Mode switch,
`art_select_image`, and the six other services that put the TV into Art Mode
first — still went to the TV over IP Control's JSON-RPC, on every toggle,
including from automations. If you set the option off believing it protected
you, it did not stop the traffic it was meant to stop.

This was not hypothetical. One user with 11 Frames, every one with the option
off precisely because of the warning, counted **237 Art Mode transitions in
seven hours** that all took the IP Control write path anyway
([#248](https://github.com/TheFab21/ha-samsungtv-smart/issues/248)).

From 8.7.7, **off means no `artModeControl` traffic at all**, reads and
writes. To stop *every* IP Control write to a TV you suspect, turn off
**Enable IP Control** itself. Sorry — this was our gap, not yours.

## Highlights

- **Art Mode writes are now checked against the panel**, read back
  afterwards, and never repeated blindly when they did not take. This closes
  the one unbounded write pattern found in an audit of everything this
  integration sends to a TV.
- **Sleeping TVs no longer flood the log** — the two warnings per poll
  (ours and Home Assistant core's) are gone at the source, measured
  705/725 → 0/0.
- **19 services can finally return data** — `art_get_current`,
  `art_get_thumbnail`, `art_get_matte_list` and the rest computed a result
  that Home Assistant threw away.
- **`matte_id` is validated** before an upload, instead of letting the TV
  store artwork it then cannot render.
- **Browser launch over IP Control**, for TVs where the WebSocket path opens
  nothing — contributed by [@alienpoop03](https://github.com/alienpoop03).
- **Driving the TV like a remote is documented** — it always worked, nobody
  could find it.
- **Two more entities stop reading sleeping TVs**, removing what became the
  largest log producer once the poll overrun was fixed.

---

## Art Mode writes: panel truth first, read-back after, no blind repeats

Every path that puts a Frame into or out of Art Mode trusted a *derived*
`art_mode_status` — the IP Control flag (which can wedge, as the README
documents), the WebSocket art channel (which can go stale), or
SmartThings — and then wrote, with no check of what the panel showed, no
read-back, and no memory of having just done the same thing.

When that reading was wrong while the panel was in fact showing art, a
periodic automation became a periodic write, indefinitely: an `artModeOn`
sent to a panel already in Art Mode on every run — or, on the fallback path,
a `KEY_POWER` that toggled the panel **out** of Art Mode onto HDMI and a
`set_artmode` ten seconds later to put it back.

Four changes, applied to both the Art Mode switch and the media player:

1. **The panel is asked first.** `getTVStates.pictureMode` is `Ambient`
   exactly while art is on screen. It is a plain getter that needs only IP
   Control to be paired (not the Art Mode option) and is independent of both
   the wedgeable flag and the WebSocket channel. If it already shows what
   was asked for, nothing is written and the stale reading is logged.
2. **No power key at a Frame whose art channel says art is on.**
3. **A cooldown per intent**, shared per TV. Only writes that were *not*
   read back as applied are remembered, so toggling art on, off with the
   remote, and on again within a minute is never blocked. A second
   unverified write of the same intent inside 60 s is refused with one
   warning, and the retry loops stop instead of thrashing.
4. **Every write is read back** — the panel after IP Control,
   `get_artmode()` after the WebSocket — instead of trusting an accepted
   command.

Two new log lines are worth knowing, because they are the first measurement
of something nobody had measured:

```
… art_mode_status reads off but the panel shows Ambient — the reading is stale; … NOT writing
… art mode 'on' was already written 42s ago and did not take; not writing it again within 60s
```

The first means the Art Mode reading was wrong and the integration did *not*
act on it. The second means the TV accepted a write and did not apply it. If
you see either regularly, please say which model in
[#248](https://github.com/TheFab21/ha-samsungtv-smart/issues/248) — it tells
us how common the stale-reading case is in the wild.

## One picture-mode change writes once

`select_picture_mode` sent the SmartThings command **and** the WebSocket key,
always — even when the cloud write had been read back as applied. The
SmartThings side is itself a matrix of up to four verified sends, so one
change could put five writes on the same subsystem within a second or two.

The WebSocket key exists for a real reason: SmartThings answers COMPLETED
while the TV shows "function not available" when HDMI content protection
blocks the cloud command. It is still sent in every case where SmartThings
could not be confirmed — no SmartThings, a failure, an unverifiable send. It
is no longer sent when the cloud write was read back as applied.

## Sleeping TVs and the slow-update warning

A Frame in standby leaves the network entirely, so its power probe can only
end in a timeout — and that timeout (6 s) was longer than the scan interval
(5 s) it was racing. A sleeping TV therefore overran **every single poll**,
by construction, and both this integration and Home Assistant core logged a
warning each time: about 5 000 lines per day per sleeping TV, doubled.

A TV already believed to be off now gets a 3 s probe, which finishes inside
the interval. Polling cadence is unchanged, so nothing is detected later.
Measured on a 13-TV install with the same two TVs asleep in both windows:

| | 8.7.4 | 8.7.9 |
|---|---:|---:|
| `samsungtv_smart.media_player` warnings | 705 | **0** |
| Home Assistant core warnings | 725 | **0** |

Core's line went with ours, which is the part a log-level change could never
have achieved. The warning is kept where it still means something: a
genuinely slow *reachable* TV warns at most once per five minutes, with the
count it stands for.

## Sleeping TVs, part two: the colour tone and backlight entities

The same shape as above, one platform over, found by the same reporter on the
same fleet. Five of the seven IP Control picture entities read through a
shared coordinator that returns early when the TV is off or in Art Mode.
**The colour-tone select and the backlight number polled independently and
had no such gate**, so they kept opening a connection to a sleeping TV every
30 s. Measured: 7.8 s for one such read, against 0.15 s awake — and because
each takes the per-host lock on its own, two of them queued behind each other
cross Home Assistant's 10 s per-entity threshold.

That produced **6 019 `Update of <entity> is taking over 10 seconds` lines in
21.4 hours** on a 13-TV install, the largest single log producer there once
the sleeping-TV overrun was fixed. Correlation with time asleep: 0.81.

Both now use the same gate as their five siblings, which is also why those
five never produced a single warning. As a side effect, the speaker select's
private copy of that gate is now the shared one — three definitions became
one.

## Services that answer

19 of the 26 services computed a result and were registered without
`supports_response`, so Home Assistant discarded it. Only `art_identify`,
`art_upload` and `art_upload_batch` could ever answer. All 19 now can —
`art_get_current`, `art_get_thumbnail`, `art_get_brightness`,
`art_get_matte_list`, `art_available` and the rest. Existing calls that
ignore the response are unaffected. Use a response variable, or run them
from Developer Tools → Actions to see the result.

`art_upload` also gains `show: true`, so upload-then-display is one call.

## `matte_id` is validated

A matte is `<type>_<color>` and both halves must exist on *your* TV — the
lists differ by model, and the TV also has to pair them. An id built from a
type and a colour the panel does not know used to be sent anyway; on a
QN55LS03HEFXZA the TV stored the artwork and then crashed rendering it, which
looked like a TV fault ([#243](https://github.com/TheFab21/ha-samsungtv-smart/issues/243)).

`art_upload` now checks both halves against the TV's own lists first and
refuses with a message naming what is wrong and what the TV offers.
(`art_upload_batch` does not validate yet — it takes the same `matte_id`, and
that gap is on the list.) If the lists cannot be read, the upload proceeds as before —
validation never blocks something that would have worked. Call
`art_get_matte_list` (which now returns its lists) to see what your TV
accepts.

## Browser launch over IP Control

`media_player.play_media` with `media_content_type: browser` uses
`directAccessControl` with `applicationName: "webBrowser"` when IP Control is
paired, and falls back to the WebSocket launch otherwise. On a QE65Q80TATXZT
the WebSocket path opened nothing; the IP Control path opens the browser at
the URL. The TV never reports which page it actually opened, so the INFO
line names the URL that was requested — that is the one fact that makes a
"wrong page" report diagnosable. Contributed by
[@alienpoop03](https://github.com/alienpoop03).

## Driving the TV like a remote

Asked for as a feature in
[#246](https://github.com/TheFab21/ha-samsungtv-smart/issues/246); it has
existed for years. The README now has a section on `remote.send_command`
with a key list and repeat count, a table of the navigation keys (D-pad,
Enter, Return, Home, Info, Menu, Guide, Exit…), and the two things that were
implemented but documented nowhere: a **millisecond delay step** inside a
`+`-joined sequence (`KEY_HOME+1500+KEY_RIGHT+KEY_ENTER`), and that such a
sequence can mix `ST_` and `IP_` source keys with remote keys.

## What may look different

- **An `art_upload` with a `matte_id` your TV does not list now fails**
  with a clear error instead of succeeding and producing artwork the TV
  cannot show. If an automation used such an id, it will start reporting
  an error — that is the TV telling you the truth earlier.
- **An Art Mode action can now be refused for up to 60 s**, with a WARNING,
  when the same action was just sent and did not take. Before, it was sent
  again silently. If you see that warning, the TV — not the automation —
  is the thing to look at.
- **`Art Mode ON aborted …` is reworded.** It claimed more than it did: the
  power-on request *had* been sent, and only the art-mode part was deferred.
  It now says so, and repeats while a deferral is already outstanding drop to
  INFO — a TV that simply wakes late used to produce one WARNING per attempt
  all morning.

---

## Upgrading

HACS → update → restart Home Assistant. Nothing to reconfigure.

If you disabled **Enable IP Control Art Mode** believing it stopped IP
Control Art Mode writes: it does now. If you want to stop every IP Control
write to a particular TV, turn off **Enable IP Control** on that entry.
