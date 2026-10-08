# Release notes — 8.11.4

If this project is useful to you, you can support its development:

# <a href="https://buymeacoffee.com/thefab21" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-black.png" alt="Buy Me A Coffee" height="41" width="174"></a>

> **Status: stable release.** This note covers everything since **8.10.0**
> (8.10.1 → 8.11.3 plus this release). Nothing to reconfigure, and the new
> Gallery card options are opt-in — a card that sets neither behaves exactly as
> before. Two entities gain a proper name, and some log lines are new or
> reworded — see [What may look different](#what-may-look-different).

## Highlights

- **A local-only 2019 Frame keeps its Art Mode switch in sync**
  ([#315](https://github.com/TheFab21/ha-samsungtv-smart/issues/315)). Without
  SmartThings or IP Control, the switch could lag several minutes; it now tracks
  the TV within about a second again. The matte selects on these Frames are
  usable again too.
- **Switch between folders in the Gallery card**
  ([#303](https://github.com/TheFab21/ha-samsungtv-smart/issues/303)). A
  dropdown, from either an explicit list of folder sensors or one recursive
  sensor's sub-folders — no more `input_select` plus conditional cards. Nested
  folders now resolve too.
- **Frame Art works on 2019 Frames — uploads *and* thumbnails**
  ([#307](https://github.com/TheFab21/ha-samsungtv-smart/issues/307),
  [#311](https://github.com/TheFab21/ha-samsungtv-smart/issues/311), thanks
  [@bcsteeve](https://github.com/bcsteeve)). Art API `0.97` refused every upload
  with `SYSTEM_FAIL (-1)` and returned thumbnails over a channel the integration
  did not read; both now use the binary-WebSocket transport those panels speak.
- **Hue Sync says why SmartThings refused**
  ([#298](https://github.com/TheFab21/ha-samsungtv-smart/issues/298)). "Unknown
  error" and a bare `409, message='Conflict'` are replaced by SmartThings' own
  reason, and start/stop now read an idle 2024 Frame correctly.
- **No more refresh commands to a sleeping TV**
  ([#308](https://github.com/TheFab21/ha-samsungtv-smart/pull/308), thanks
  [@nvk86](https://github.com/nvk86)). A stale SmartThings `ON` kept poking a
  powered-off panel once a minute, each time logging a WARNING about a healthy TV.
- **Three entities and two dialogs are no longer unnamed or missing their text**
  ([#302](https://github.com/TheFab21/ha-samsungtv-smart/issues/302)).
- **Several long-lived Art Mode defects, found by reading an 11 h log of 8.10.0
  line by line** against the code: absolute volume latched off for six hours, an
  art reconnect loop that cancelled itself, and a power-on that reported success
  whatever happened.

---

## New: switching between folders in the Gallery card

The folder-gallery card showed one folder. Showing several meant a manual
`input_select` and a stack of conditional cards. It now renders a dropdown as
soon as there is more than one folder, configured either way:

- **`folders:`** — an explicit list, one `platform: folder` sensor per theme
  (each entry takes a `name` plus `sensor` / `folder_sensor` / `folder` /
  `image_list`);
- **`group_by_subfolder: true`** — derives the list from a single recursive
  folder sensor's sub-folders: an **All** entry plus one per sub-folder.

```yaml
type: custom:folder-gallery-card
folder_sensor: sensor.media
group_by_subfolder: true
```

`group_by_subfolder` is also a **Group by sub-folder** checkbox in the visual
editor (translated in all six languages), so the selector can be turned on
without touching YAML.

Both options are backward compatible: with neither set, the card behaves exactly
as it did before — no selector, no change.

Fixed along the way:

- **Nested folders resolve.** File paths kept only their basename against the
  sensor's base folder, so anything in a sub-folder 404'd. Paths now keep their
  sub-path — a latent bug the selector needed fixed.
- **The anti-flicker check watches every sensor the card reads**, including the
  ones named under `folders:`.
- **Two folders with the same name are both reachable** (they shared one option
  value, so the second was unselectable and both rendered as selected), and
  folder labels are HTML-escaped — a name containing `<` or `&` broke the
  dropdown.
- **The divergent 722-line copy** under `www/community/folder-gallery-card/` is
  gone. The integration bundles and auto-registers the real card at
  `/api/samsungtv_smart/folder-gallery-card.js`, so that manual copy only ever
  went out of date. HACS installs just `custom_components/`, so nothing you have
  installed is affected.

The Gallery guide (`Frame_Art_Gallery.md`) documents both options, with examples
and the configuration table updated.

## Frame Art on 2019 Frames: uploads and thumbnails

A 2019 Frame (QN55LS03R, art API `0.97`) speaks an older binary-frame dialect
for the image transfers, which the integration did not read.

- **Upload** ([#307](https://github.com/TheFab21/ha-samsungtv-smart/issues/307)).
  The Frame answers the D2D socket `send_image` handshake with `SYSTEM_FAIL (-1)`
  before any connection is made, so every upload failed with "no content_id
  returned". There is **no version guessing**: D2D is still tried first on every
  TV, so nothing that works today changes. The binary-WebSocket transport is
  used only when D2D actually answers `SYSTEM_FAIL (-1)`, on a fresh request id,
  with exactly the on-device-verified payload.
- **Thumbnails** ([#311](https://github.com/TheFab21/ha-samsungtv-smart/issues/311)).
  The same Frame returns a thumbnail as a binary WebSocket frame (a 2-byte
  length, a JSON header, then the bytes) rather than handing back a separate
  socket to read. The integration now decodes that frame and hands the image to
  the waiting request. Newer Frames never send one, so their socket path is
  untouched; once a TV is seen answering this way, the redundant probe before it
  is skipped. Individual and batch thumbnail downloads both work (the reporter
  measured 88/88, zero failures).

*(Both paths verified on a 2019 Frame by
[@bcsteeve](https://github.com/bcsteeve); not retested here against a physical
Frame.)*

## 2019 Frame, local only: the Art Mode switch keeps up

From [#315](https://github.com/TheFab21/ha-samsungtv-smart/issues/315): a 2019
Frame (QE65LS03R, art API `0.97`) run **local only** — no SmartThings, no IP
Control — showed its Art Mode switch minutes behind the TV.

With no cloud or IP fallback, the only source of Art Mode state is the art
WebSocket's `art_mode_changed` broadcasts, which arrive instantly. But this
Frame never answers the request/response reads the integration polls
(`get_current_artwork`, `get_artmode_settings`, …); those timed out, tripped the
"channel wedged" breaker, and forced a reconnect with an escalating back-off.
While the socket was down during that back-off, the broadcasts were lost — so
the switch only caught up minutes later.

- **An unsolicited event now counts as proof the art app is alive.** The breaker
  exists for a crashed art app that answers nothing at all; a Frame still
  pushing `art_mode_changed` is not that. While events are arriving, the timeout
  breaker no longer force-reconnects and the back-off no longer escalates, so the
  channel stays up and the broadcasts land — the switch tracks within about a
  second. A genuinely dead channel pushes nothing and still recovers as before.
- **The matte selects fall back to the built-in catalogue.** `get_matte_list` is
  the only way to enumerate matte types/colours, and this Frame does not serve
  it, so the Matte Type / Matte Color selects were stuck on a single option. When
  the enumeration keeps failing, the selects are now seeded from the known
  Samsung matte catalogue so they are usable; a TV that does answer is unchanged.
  (Applying a matte always worked — only listing the choices was missing.)

*(Diagnosed from the reporter's debug log; not retested here against a physical
0.97 Frame.)*

## Hue Sync

From [#298](https://github.com/TheFab21/ha-samsungtv-smart/issues/298) on a
TQ55LS03DAUXXC: start/stop Hue Sync sometimes failed, Home Assistant showed
"Unknown error", and the log held only `Error setting Hue Sync mode: 409,
message='Conflict'`.

- **The refusal's reason is no longer thrown away.** Commands posted with
  `raise_for_status=True`, so aiohttp kept the status line and dropped the body
  SmartThings explains a refusal in. Every SmartThings command error now carries
  the `code`, `message` and `requestId`, with the body logged at DEBUG.
- **Home Assistant shows the reason** instead of "Unknown error": anything but a
  422 used to escape as a raw `ClientResponseError`.
- **A 409 is worded by the TV's health.** The TV's SmartThings health is read and
  the advice follows it: `OFFLINE` → check the TV is on and online (the command
  never reached it); `UNKNOWN` → the health could not be read; otherwise the
  previous advice, with the health shown. The README lists
  `ONLINE` / `OFFLINE` / `UNHEALTHY` / `UNKNOWN`.
- **"Offline" is decided on the real status code.** The choice between "device
  appears offline" and "Error turning on device" tested for `"409"` anywhere in
  the error text — which now includes SmartThings' random `requestId`, so about
  one 401/403 in 140 was reported as an offline TV. It branches on
  `err.status == 409`.
- **An idle 2024 Frame is read correctly.** A real idle 55" Frame 2024 reports
  `supportedModes [""]`, `selectedAppId ""` and `streamControl false`, and `[""]`
  is truthy — so start never launched the Hue Sync app, and stop sent its command
  instead of reporting nothing to stop. Empty strings and lists of them no longer
  count as a running session, and the full `samsungvd.lightControl` status is
  logged at DEBUG.
- **Docs corrected**: the README and `services.yaml` still said the services do
  not open the app; `start_hue_sync` has launched it when no session runs since
  8.8.12.

## Art Mode and IP Control: what the 8.10.0 log exposed

An 11-hour log of 8.10.0 on two Frames, checked line by line against the code,
showed no regression from 8.10.0 and five older defects.

- **A single refusal no longer switches off absolute volume for the session.**
  `directVolumeControl` is refused in Ambient mode, and the code told that apart
  from "not on this model" using the `getTVStates` snapshot alone — which trails
  the `art_mode_changed` broadcast. Measured: absolute volume detected at
  05:00:04.171, broadcast ON at .346, `-32601` at .830 → "not available on this
  TV", then no IP volume read for six hours. A broadcast heard recently now
  counts, and a volume the TV has already reported is not taken away by one
  refusal. The art-mode getter had the same latch (never triggered) and is now
  marked supported once it answers.
- **The art WebSocket reconnect loop no longer cancels itself.** On its first
  failed port it cleaned up with `close()`, which cancels the reconnect task —
  and that task was the caller. The `CancelledError` surfaced on the alternate
  port and escaped the `except Exception`: no failure counted, no backoff, no
  "reconnect gave up" line, just silence. It now drops only the half-open socket,
  which also stops a failed lazy open from killing a sleeping reconnect loop. The
  end of the receive loop is logged (aiohttp ends the iteration on CLOSE
  silently).
- **The reconnect loop no longer locks itself out.** Its retries at +1, +7 and
  +15 s fed the anti-saturation accounting and armed the 2-minute backoff, so a
  TV back after 46 s was refused both by the loop and by any Art Mode write.
- **A power-on that did not wake the TV says so.** `_ensure_art_mode_ready`
  waited 10 s and logged "TV should now be on" whatever happened, called an
  unanswered art read "Art Mode is OFF", and recorded a write that never left
  Home Assistant — refusing the next Art Mode ON for 60 s. It now checks the TV
  answers (once more after 5 s), reports an unknown state as unknown, and holds
  only a write that actually reached the socket.
- **A redundant Art Mode OFF is not a WARNING** blaming a stale reading. When the
  published `art_mode_status` already matches, it is a DEBUG "nothing to write".
  `turn_off` still asks the panel
  ([#248](https://github.com/TheFab21/ha-samsungtv-smart/issues/248)).
- **Misleading log lines fixed**: "keeping last value (failure 10/3)" after the
  value had been cleared, "waiting" where nothing waits, empty device-info and
  UPnP lines on a timeout (`str()` of a `TimeoutError` is empty), and an `OSError`
  logged without its text.

Two adversarial review passes over those fixes tightened them further: an unknown
snapshot now rules nothing out (a Frame woken straight into art sends no
broadcast, and Samsung's Ambient Mode on non-Frame sets reports `pictureMode
"Ambient"` too), a `set_artmode` whose send failed is not recorded as sent, and
one cut short by the 10 s timeout after its request went out is.

## SmartThings: no background refresh to a sleeping TV

Thanks to [@nvk86](https://github.com/nvk86)
([#308](https://github.com/TheFab21/ha-samsungtv-smart/pull/308)) for finding and
diagnosing this one.

SmartThings can keep reporting `switch=on` for a while after the panel has
powered down. The periodic `refresh/refresh` command — which exists so values
like `pictureMode` do not stay stale — was gated on that **cloud** state, so the
integration kept poking a sleeping TV. SmartThings accepted the request, the TV
could not execute it, and the result was a `FAILED` and a WARNING once per
refresh interval for a perfectly healthy powered-off TV.

The refresh is now gated on what the integration knows **locally**. It is:

- allowed during normal viewing;
- allowed while a Frame is displaying Art Mode — including the 2025 Frames that
  report `PowerState=standby` *while* Art Mode is on;
- suppressed immediately while a power-off is in progress;
- suppressed once the local state confirms real standby.

Read-only SmartThings status polling is unchanged, so nothing else about cloud
state refreshes any slower.

## Interface strings

From [#302](https://github.com/TheFab21/ha-samsungtv-smart/issues/302):

- **The application-credentials dialog** showed its placeholders raw instead of
  the SmartThings portal link, the OAuth docs link and your instance's callback
  URL.
- **"Successfully configured {name}"** showed the placeholder literally at the
  end of setup.
- **Three entities had a `translation_key` with no string behind it.**
  `switch.power` and the brightness-intensity sensor have no device class, so
  their name resolved to nothing and they displayed only the device name —
  effectively unnamed. The illuminance sensor read correctly from its device
  class, but its own key was dead. All three are now named, in English and
  French; the other languages fall back to English.

## Examples and documentation

- **The Frame Art example scripts called services that do not exist**, so copying
  them gave "service not found": `art_slideshow` → `art_set_slideshow` (with its
  real `category_id` / `duration` / `shuffle` fields, and "stop" as
  `duration: "0"`, which stops the slideshow while leaving Art Mode on),
  `art_current` → `art_get_current`, and `set_art_mode` → `art_set_artmode` with
  the required `enabled: true`.
- **Four links to the Frame Art guides 404'd**, pointing at a `docs/` folder that
  does not hold them. The guides themselves did not move.

Both are covered by guards in the test suite, so neither can drift again.

---

## What may look different

- **The Gallery card** gains a folder dropdown *only* if you set `folders:` or
  `group_by_subfolder:` (or tick **Group by sub-folder** in the editor).
  Otherwise it is unchanged.
- **Two entity names change** from the device name alone to a real name: the
  **Power** switch and the **Brightness intensity** sensor. Their entity ids are
  untouched; only the displayed name changes.
- **Hue Sync failures now say why**, with SmartThings' `code`, `message` and
  `requestId`, and the 409 message differs depending on whether the TV reads
  `OFFLINE`, `UNKNOWN` or healthy.
- **Fewer WARNINGs**: a redundant Art Mode OFF is now DEBUG, and a powered-off TV
  no longer produces one per refresh interval.
- **New or reworded log lines**, among them the end of the art receive loop, an
  unknown Art Mode state after a power-on that did not wake the TV, the reasons
  behind an absolute-volume decision, and a DEBUG line for the full
  `samsungvd.lightControl` status.
- **The manual card copy** under `www/community/folder-gallery-card/` is no
  longer in the repository. If you had copied it into your own `config/www/`, it
  is still there and still works, but it is stale — remove the Lovelace resource
  and use the card the integration registers for you.

---

## Thanks

- [@nvk86](https://github.com/nvk86) for
  [#308](https://github.com/TheFab21/ha-samsungtv-smart/pull/308) — the
  SmartThings refresh diagnosis, the fix and the on-device verification.
- [@bcsteeve](https://github.com/bcsteeve) for
  [#307](https://github.com/TheFab21/ha-samsungtv-smart/issues/307) and
  [#311](https://github.com/TheFab21/ha-samsungtv-smart/issues/311) — the 2019
  Frame's binary-frame upload and thumbnail dialect, diagnosed and verified
  on-device.
- [@IonasElate](https://github.com/IonasElate) for
  [#315](https://github.com/TheFab21/ha-samsungtv-smart/issues/315) — the
  local-only 2019 Frame's lagging Art Mode switch, with the debug log that
  pinned the cause.
- The reporters of
  [#298](https://github.com/TheFab21/ha-samsungtv-smart/issues/298),
  [#302](https://github.com/TheFab21/ha-samsungtv-smart/issues/302) and
  [#303](https://github.com/TheFab21/ha-samsungtv-smart/issues/303), and whoever
  ran their Frame overnight with debug logging on so 8.10.0 could be read line by
  line.

**Full Changelog**: https://github.com/TheFab21/ha-samsungtv-smart/compare/8.10.0...8.11.4
