# Release notes — 8.10.0

If this project is useful to you, you can support its development:

# <a href="https://buymeacoffee.com/thefab21" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-black.png" alt="Buy Me A Coffee" height="41" width="174"></a>

> **Status: stable release.** This note covers everything since **8.9.0**
> (8.9.1 → 8.9.10 plus this release). Nothing to reconfigure, but one option
> changes meaning: **the IP Control Art Mode option now only controls
> switching** — detection reads the TV over IP Control either way. See
> [The IP Control Art Mode option now only controls switching](#the-ip-control-art-mode-option-now-only-controls-switching).
> Some log lines are new or reworded — see
> [What may look different](#what-may-look-different).

## Highlights

- **`art_mode_status` follows the TV within seconds after a switch.** A fresh
  `art_mode_changed` from the TV is no longer overruled by an older or lagging
  IP Control reading.
- **The Art Mode sliders are usable as soon as Art Mode is on.** Brightness and
  color temperature no longer stay unavailable for up to 30 s.
- **Art Mode writes are confirmed properly.** A write the TV confirmed is no
  longer refused as one that "did not take"
  ([#290](https://github.com/TheFab21/ha-samsungtv-smart/issues/290)), and a
  panel that is slow to catch up no longer turns a successful write into a
  failure.
- **Much less traffic to the TV.** No art-only reads while Art Mode is off, the
  artwork count honours its own interval (60× fewer `get_content_list` calls),
  and no volume reads the TV can only refuse.
- **The thumbnail cache can't be wiped by a TV that doesn't answer.**

---

## The IP Control Art Mode option now only controls switching

The option is renamed **Switch Art Mode over IP Control** (it was *Use IP
Control for Art Mode*, and *Enable IP Control Art Mode* in the README). It
stays **off by default**, and it still decides whether Art Mode is *switched*
over IP Control (`artModeOn` / `artModeOff`) or over the WebSocket art channel.

What changes is detection. From 8.7.7 to 8.9.10 the option also stopped the
`artModeControl` **getter**, so with it off — the default — the integration
never asked the TV over IP Control whether it was in Art Mode. On two Frames
measured for this release, the IP Control art-mode reading was empty 1022 times
in 78 minutes. Detection then rested on a 10 s `getTVStates` snapshot, the
WebSocket channel and SmartThings, which is where the lags below came from.

- **The getter is now read on every IP-paired Frame**, every 5 s and right after
  each Art Mode transition, whatever the option says. A getter that wedges "on"
  is still caught: the integration checks it against `getTVStates.pictureMode`
  and overrules it when the two disagree twice in a row.
- **Non-Frame TVs are not asked** (they have no `artModeControl`), and a Frame
  that answers "method not found" outside Ambient mode is not asked again until
  IP Control is reconfigured.
- **Traffic per 5 s refresh**, on an IP-paired Frame: with the option off, one
  request becomes three (`powerControl`, `artModeControl`, `getTVStates`); with
  it on, four become three (`powerControl` is no longer sent twice).

> [!WARNING]
> The warning in the README still applies to **switching**: don't turn on
> *Switch Art Mode over IP Control* unless you know your firmware handles it
> (factory reset needed on a QE55LS03D with firmware 2123). That damage was
> traced to writes — before 8.7.7, users who had turned the option off still
> had every toggle written over JSON-RPC. If you suspect your TV and want **no**
> IP Control traffic at all, turn off **Enable IP Control** itself.

## Art Mode state: closer to what the TV shows

Measured on two Frames (a 55" LS03D and a 32" LS03C), with the Home Assistant
and on-TV logs aligned to the millisecond.

- **A fresh broadcast outranks an older panel reading.** Art was switched on at
  22:04:14.4 and the TV broadcast it, yet five seconds later `art_mode_status`
  still read off: the cached `getTVStates` snapshot had been taken before the
  switch. The result was a "the reading is stale" WARNING on a write that had
  just succeeded, and unavailable Art Mode sliders. Each IP Control reading now
  carries the time it was taken, and the TV's `art_mode_changed` wins over any
  reading taken before it — or less than 20 s after it, since the panel can
  trail the broadcast (a 2023 Frame's `getTVStates` had not reached "Ambient"
  11 s after it broadcast ON). After that the reading is the authority again,
  so a channel that froze long ago
  ([#248](https://github.com/TheFab21/ha-samsungtv-smart/issues/248)) still
  loses, and a powered-off reading is never overridden.
- **The Art Mode sliders follow Art Mode.** Their availability is derived from
  the media player, but Home Assistant only republished it on the number
  platform's 30 s poll. A script setting the brightness 10 s after switching
  Art Mode on failed twice in four tries with "Referenced entities … are
  missing or not currently available". They now update with the media player,
  and read the live value as soon as they become available.
- **The media title no longer says "Art Mode" after Art Mode ends.** SmartThings
  keeps reporting the "art" app for 30–45 s after the panel leaves it, and the
  title trusted it over the local signal (33 s measured, while
  `art_mode_status` already read off). The cloud's "art" now names the title
  only when nothing local is known.

## Art Mode switching

- **A confirmed write is not one that "did not take"**
  ([#290](https://github.com/TheFab21/ha-samsungtv-smart/issues/290)). On a
  Frame without IP Control, on → off → on within 60 s was refused. Writes are
  now confirmed by an `art_mode_changed` broadcast of the requested state
  received after the write, or by reading the panel back on IP-paired TVs —
  never by the art channel's cache, which is known to latch (#248,
  [#273](https://github.com/TheFab21/ha-samsungtv-smart/issues/273)). A
  confirmed change clears the record of both intents, so a genuine OFF right
  after a redundant one is no longer refused.
- **A panel that lags the broadcast no longer fails the write.** A 32" Frame
  woken by an automation confirmed Art Mode ON four ways, but `getTVStates` was
  slow to report "Ambient": the switch reported failure and Home Assistant
  showed Art Mode off for 40 minutes. A retry no longer rewrites a request the
  TV has already broadcast. The panel is re-read in the background for up to
  20 s, with a WARNING if it never agrees.
- **An explicit Art Mode write passes the recovery cooldown.** The cooldown is
  there to stop pollers refilling a recovering socket, but it also refused
  turning Art Mode on, for up to ~10 min after recurring wedges. Polls are still
  held back as before.
- **Waking straight into Art Mode is logged at INFO**, not as a "stale
  reading" WARNING — about 72 misleading lines a day on a Frame with a motion
  timer.

## Art channel: recovery, ports and traffic

- **The stored art port is read and kept**
  ([#273](https://github.com/TheFab21/ha-samsungtv-smart/issues/273)). It
  flipped 8002 → 8001 as Frames woke, then back at boot: the shared Art client
  was still built from the remote port, and any port answering at a wake was
  stored. Only the first port to answer in a session is stored now.
- **Recurring wedges back off.** A channel that answers some requests and times
  out on others looped at the 30 s base cooldown (11 wedges in 64 min on a
  43" LS03A). Wedges recurring within 15 minutes now escalate the cooldown, and
  the log says why it is backing off.
- **A capability isn't latched from a busy channel.** One 1 s timeout while a
  Frame was waking marked two art capabilities unsupported for good.
  Silence now counts only on a channel that has answered since connecting and
  isn't recovering.
- **No art-only reads while Art Mode is off.** On an input, the art app answers
  `get_artmode_status` but stays silent on the artwork and slideshow reads, so
  every 5 s poll piled up two timeouts. Three in a row tripped the wedge breaker
  (14 needless reconnects in 70 min on a 13-TV fleet). They are skipped now —
  about 17 000 fewer requests per TV per day.
- **The artwork count honours its interval.** `get_content_list` returns the
  whole library and ran every 5 s, for a count (about 135 MB over 18 h on two
  Frames). It now follows the existing *content list interval* option (300 s
  by default), and adding, selecting or deleting art still refreshes it at once.
- **Reconfigure → Connection keeps the stored token** instead of pairing from
  scratch, which failed on a Frame in Art Mode (no screen for the prompt).
- **No traceback at shutdown** from the ping thread outliving the event loop.

## Thumbnails

- **A TV that doesn't answer can't empty the cache.** With *cleanup orphans* on,
  a failed list read was taken as "no personal photos" and deleted all local
  personal thumbnails. Nothing is deleted or downloaded now unless the TV
  actually answered; a genuinely empty list still cleans up.
- **The batch download waits out art channel recovery** (up to three pauses,
  300 s in total), retries the image a wedge cut off, and resumes. What it still
  can't reach is reported as not processed instead of as a string of failures.

## Volume

- **No IP Control volume read in Ambient mode.** `directVolumeControl` is never
  available while the panel shows art; a Frame left in Art Mode refused 45 reads
  in 4 minutes. UPnP is used as before, and absolute volume comes back as soon
  as the TV leaves Ambient mode.
- **A TV that can't report its volume on an external output isn't asked every
  5 s.** With the sound on an AV receiver, an LS03D refused every read ("fail
  to get volume" in its own log) — 44 times in 4 minutes. The refusal is
  remembered for that output; another output, including the internal speakers,
  is asked again. Some TVs do report an eARC soundbar's volume, so nothing is
  assumed for external outputs in general. *Set volume* is hidden while the
  current output refuses.

## Packaging

- **Pillow is no longer listed in the manifest**: it ships with Home Assistant,
  and hassfest now rejects it. `pillow-heif` stays.
- **Releases are built from the tag**, and pre-releases no longer fail the
  release workflow.

---

## What may look different

- **The option label**: *Switch Art Mode over IP Control* (Reconfigure → IP
  Control). Same setting, same default.
- **"IP Control art-mode for … unchanged (None)"** now shows `True` / `False`
  on IP-paired Frames.
- **New log lines**:
  - `IP Control art-mode getter not available on … ; using the other art-mode sources` (DEBUG, once)
  - `IP Control absolute volume refused while the sound goes to '…' …` (DEBUG, once per output)
  - a WARNING when the panel never agrees with a broadcast the TV sent after a write
  - `woke directly into Art Mode` and `confirmed on re-check` at INFO
  - a backing-off line for recurring art channel wedges
  - a WARNING when the thumbnail batch leaves images not processed
- **The media title** shows the real input instead of "Art Mode" during the
  SmartThings lag after Art Mode ends.
- **Brightness / color temperature** become available, and unavailable, with
  Art Mode instead of on their next 30 s poll.
