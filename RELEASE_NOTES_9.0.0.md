# Release notes — 9.0.0

If this project is useful to you, you can support its development:

# <a href="https://buymeacoffee.com/thefab21" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-black.png" alt="Buy Me A Coffee" height="41" width="174"></a>

> **Status: stable release.** This note covers everything since **8.0.0**:
> the 8.1 → 8.19 line plus this release. If you are already on a recent 8.19.x,
> 9.0.0 adds nothing you must do. **Nothing to reconfigure** whichever 8.x you
> come from. New entities appear on their own, and two existing sensors behave
> differently — see [Upgrading](#upgrading) and
> [What may look different](#what-may-look-different).

## Why 9.0

8.0.0 brought OAuth2 for SmartThings and a second local channel, IP Control.
Since then, SmartThings API access has become a **paid plan** (October 2026),
and the integration has moved the other way: **local first**.

The last step was the TV's own **quick-settings channel**, the one Samsung's
SmartThings app uses on the local network. It needs no pairing and no cloud
account. Over it the integration now reads and sets the picture mode the
current input offers, the sound mode, ten quick settings, the input, and the
apps on the home screen with their icons. A change made with the remote shows
within a second.

Add IP Control (power, volume, channel, picture calibration, speaker output,
Art Mode, reboot) and the WebSocket channels (remote keys, Art Mode, artwork),
and **a Frame works day to day without SmartThings**. SmartThings is still used
when you have it, mainly for power metering, the ambient light sensor and Hue
Sync.

## Highlights

- **Local quick settings** — picture mode with the real per-input list
  (FILMMAKER MODE included), sound mode and ten more selects: AI mode, sleep
  timer, energy saving, game mode, picture clarity, contrast enhancer, colour
  tone, colour space and digital audio format. All local, no cloud call.
- **Inputs and apps switch locally**, and the apps on the TV's home screen fill
  the source list on TVs whose remote channel never listed any.
- **The media player shows the icon of the app on screen**, fetched from the TV.
- **A `select` for the source**, because the Home Assistant mobile app's media
  dialog showed only the current source.
- **Art Mode is far more robust**: a self-healing art channel, writes read back
  from the panel, ports that stop flapping, a settle after HDMI-CEC wake-ups and
  `art_restart_app` for an art app that has stopped answering.
- **Frame Art for every generation** — 2019 Frames (art API 0.97) upload and
  return thumbnails, and 2024/2025 firmware quirks are handled.
- **One-click upload from any device**, HEIC included, re-encoded to suit the
  panel, and a gallery card with folders, a default folder and server-side
  thumbnails.
- **Artwork identification** (opt-in): title, artist, date and a short bio of
  what is on your wall, in five languages.
- **Power and energy sensors** that fill the Energy dashboard correctly, even
  when the TV only reports energy per window.
- **IP Control on older TVs too**, with a tunable local poll and new expert
  picture settings.
- **Much quieter logs, far less traffic** to sleeping TVs and to SmartThings,
  and a test suite run on every change.

---

## Local control without the cloud

### The TV's quick-settings channel (8.16 → 8.19)

- **Picture mode.** The list and the current mode come from the TV, per input:
  a Frame 2024 offers FILMMAKER MODE on the TV input but not on HDMI, which the
  SmartThings list never showed. The select follows an input switch or a change
  made with the remote within a second. Without the channel, it falls back to
  SmartThings, then to IP Control.
- **Sound mode and ten quick-setting selects** (`sound_mode`, `ai_mode`,
  `sleep_timer`, `energy_saving_solution`, `game_mode`, `picture_clarity`,
  `contrast_enhancer`, `color_tone`, `color_space`, `digital_audio_format`).
  They use the TV's own labels in its menu language. A selection counts as done
  only once the TV confirms it, and each entity appears the first time the TV
  reports that setting.
- **Input switching** goes local first, matched by id (`ST_HDMI3`, `HDMI1`…) or
  by name, with SmartThings and remote keys as fallbacks.
- **Apps from the home screen** are listed and opened locally, with the TV's
  own names. The player follows an app opened this way straight away instead of
  waiting for the next scan.
- **App icons as the media image**: while an app is on screen, the player shows
  its 152×152 icon, fetched once from the TV and kept in memory.

### IP Control (JSON-RPC, port 1516 / 1515)

- **Power, reboot and Art Mode** without SmartThings (8.0), then **input and
  channel number** read locally (8.7), **absolute volume** — including with an
  HDMI/eARC soundbar selected (8.7) — and the **picture calibration** sliders
  and **speaker output** select (8.3).
- **Older TVs get local control back** (8.6): Samsung moved the port in 2020,
  and 2019-and-earlier sets on port 1515 are now tried automatically.
- **Picture mode over IP Control** with stable, translated options (8.14), the
  two AI picture modes (8.15), and a **browser launch** where the WebSocket path
  opens nothing (8.8).
- **Your poll cadence** (8.7), three times faster by default.
- **Verified local input and standby control before SmartThings** (8.19.5):
  when IP Control has confirmed the TV's state, inputs and standby no longer
  wait for the cloud.
- **New expert picture and eco settings** (8.19.13), disabled by default: Auto
  HDR Remastering, Brightness optimisation, Motion lighting, Auto power saving,
  Auto Motion Plus, Apply picture settings and gamma (BT.1886). These are the
  values a 2025 Frame accepted live; methods it refused are left out on
  purpose.

### Receiver and soundbar feedback (8.19.6)

An **external soundbar or receiver status entity** can supply volume and mute
to the media player when the sound goes out over HDMI/eARC, where the TV itself
can only refuse.

---

## Media player

- **The source follows your remote** on TVs whose SmartThings status reports no
  input (8.7). Without SmartThings, a source falls back to the matching WebSocket
  key (8.14.3).
- **`select.<tv>_source`** mirrors the media player's source list (8.18).
- **Additional apps to detect** (8.19.7): a list of native apps the TV leaves out
  of discovery, checked locally only when the normal tracker has no recent
  reading. A separate `input_source` attribute reports the physical input.
- **Report media player off in Art Mode** (8.19.8), opt-in. Viewing automations
  can then use the player's on/off directly; `art_mode_status` and the Power
  switch still show the real state.
- **Source after leaving Art Mode** (8.14.2): pick the input or app to switch to
  when Art Mode is turned off from Home Assistant.
- **App launch method**, with a per-app override, and a clear log line when the
  TV accepts a launch and ignores it (8.15.1).
- **The volume slider hides when audio goes to an external device** (8.3), and
  absolute-volume reads the TV can only refuse are skipped (8.10).
- **`send_text`** types into a text field on the TV (8.5), and driving the TV
  like a remote (D-pad, Home, Back…) is documented.
- **Power-on uses your wake method** on a cold wake, and the log says which one
  actually worked (8.9, 8.11.5, 8.12.2).

---

## Frame Art Mode

### Reliability

- **The art channel heals itself** (8.3.4, 8.4): auto-reconnect with backoff,
  plus a circuit breaker for a dead art app behind a live socket. No more
  "Art Mode turned off randomly, reload needed".
- **No freezes, no request storms** (8.9): requests are serialized, a
  reconnect that doesn't answer backs off, and each channel keeps the port that
  has proven to work, with every change logged.
- **Writes are checked against the panel** (8.8 → 8.10): read back afterwards,
  never repeated blindly, and a write the TV confirmed is no longer refused
  because the panel lagged.
- **Waking into Art Mode works**: a settle after an HDMI-CEC wake, a retry when
  a just-woken Frame swallows the write, and trust in a late broadcast
  (8.13 → 8.13.1).
- **`art_mode_status` stays truthful**: a real standby announced as art no
  longer latches it on (8.12.6), a lagging SmartThings reading no longer hides
  it (8.12.5), and a fresh `art_mode_changed` wins over an older IP Control
  reading (8.10).
- **`samsungtv_smart.art_restart_app`** (8.13) stops and relaunches the TV's art
  app — the last resort when it has stopped answering.

### Every Frame generation

- **2019 Frames (art API 0.97)** upload artwork and return thumbnails over the
  binary WebSocket those panels speak, and a local-only 2019 Frame keeps its Art
  Mode switch in sync (8.11).
- **2024/2025 firmware**: the newer field names, art-mode detection while the
  panel reports `standby`, and the upload format they require (8.3.4).

### Controls

- **Matte selects apply instantly** and follow the TV (8.3).
- **Art Mode brightness and colour temperature**, a brightness reset button,
  motion sensor and light sensor selects, and an orientation sensor (8.3 → 8.11).
- **Art Store collections in slideshows** (8.19.11): `art_set_slideshow` takes a
  full Art-app category id such as `STORE22_C0003_SC_477`, reports an error when
  the TV doesn't acknowledge, and returns the status read back afterwards.
- **`art_select_image` can leave a sleeping TV asleep** (`wake`, 8.14.1).
- **The TV's own labels** for entity names, mattes and motion timers, taken from
  the art app's translations (36 languages, 8.11.5).
- **19 services return data** that Home Assistant used to throw away, such as
  `art_get_current`, `art_get_thumbnail` and `art_get_matte_list` (8.8).
- **`matte_id` is validated** before an upload (8.8).

### Uploads and cards

- **`samsung-art-upload-card`** (8.5): pick an image on your phone or laptop and
  push it to the Frame in one tap. HEIC/HEIF is accepted, and every upload is
  re-encoded as a baseline JPEG fitted to the panel, EXIF rotation applied. An
  upload never switches the TV away from what it shows.
- **Batch folder upload** with idempotent re-runs and optional duplicate
  detection (8.4).
- **Folder Gallery card**: server-side thumbnails (8.3), a folder dropdown and
  nested folders (8.11), and a `default_folder` (8.12). Thumbnail widths snap to
  five sizes, so the public endpoint can no longer fill the disk (8.19.9).
- **[Frame Art Automations cookbook](Frame_Art_Automations.md)** (8.13.4):
  ambient-light brightness, slideshows, AI-chosen mattes and more.

### Artwork identification (opt-in)

Reverse image search (Google Vision) confirmed by an LLM (Claude, OpenAI or
Gemini) gives the title, artist, date and a short bio of the artwork, in five
languages (8.4). The model list is read from your provider, so a retired model
no longer breaks it, and famous works are recognised (8.6 → 8.11.7).

---

## SmartThings

- **Native OAuth2** (8.0), alongside a Personal Access Token or your existing
  SmartThings integration. Multi-TV OAuth no longer invalidates the other TVs on
  refresh (8.5), a dead refresh token triggers one reauth instead of thousands
  of retries (8.1), and an OAuth token wins over a leftover PAT (8.12.3).
- **Far fewer cloud calls**: local WebSocket first (8.3), no refreshes sent to a
  sleeping TV (8.11), failed background refreshes back off, and matching status
  requests and unchanged coordinator updates are shared or skipped (8.15.4).
- **Power and energy sensors** (8.1). When the TV reports 0 W, the Power sensor
  now shows the average over the last report window. *Energy difference* is
  counted once per report, so the Energy dashboard no longer stays near zero
  (8.19.9). The diagnostics include the raw report (8.19.12).
- **Ambient light**: illuminance and brightness-intensity sensors stay live
  while the TV is off (8.4).
- **Hue Sync** start/stop (8.7). Failures say why SmartThings refused (8.10).
- **A repaired or replaced TV can be re-linked** without deleting the
  integration (8.7).

---

## Quality

- **Multi-Frame**: several Frames side by side, isolated by config entry (8.0).
- **Token problems** show as clear notifications that clear themselves (8.0).
- **The integration has its own icon** (8.9).
- **Logs**: sleeping TVs no longer flood them (8.8), and the noisiest debug
  sources are gone (8.5, 8.9).
- **Translations**: the interface ships in 36 languages; Bulgarian is complete
  since 8.19.7.
- **Tests and checks on every change**: lint, the test suite, hassfest and HACS
  validation run on every pull request (8.15.2).
- **Async hygiene** (8.19.10): no disk access on the event loop at setup, and
  no unreferenced background tasks.

---

## Upgrading

- **Nothing to reconfigure.** Options, entity ids and automations are kept.
- **New entities appear by themselves**: the quick-setting selects and the
  source select. Each quick-setting select appears the first time your TV
  reports that setting, so a TV without it gets none. The IP Control expert
  settings are **disabled by default**; enable them under the device if you
  want them.
- **Statistics repair, once.** *Energy difference* changes state class
  (`total_increasing` → `total`), so Home Assistant may ask once whether to keep
  or delete the old statistics. Either answer is fine.
- **Thumbnail cache.** Gallery thumbnails cached before 8.19.9 at other widths
  stay in `www/frame_art/.thumb_cache`. Deleting that folder once frees the
  space; it is rebuilt on demand.
- **Folder Gallery card from before 8.3**: open it in the visual editor and save
  once, so the fullscreen-preview buttons know which Frame to use.
- **SmartThings is optional.** Everything local works without it. Since October
  2026 SmartThings third-party API access needs a paid Personal plan — see the
  README.

## What may look different

- **Picture mode options** come from the TV for the current input, in its menu
  language, and can change when you switch input.
- **The media image** is the app's own icon while a home-screen app is on
  screen, instead of a logo matched by name.
- **The source list** includes the apps from the TV's home screen on TVs whose
  remote channel listed none.
- **The Power sensor** shows an average instead of 0 W on TVs that report only
  energy per window, with a `source` attribute saying so.
- **The IP Control Art Mode option controls switching only** (since 8.10).
  Detection reads the TV over IP Control either way.
- **Media player attributes**: `input_source` reports the physical input, next
  to `app_name` and `app_id`.

---

## Thanks

To everyone who sent logs, tested betas and opened pull requests since 8.0:

- [@ahmadtawakol](https://github.com/ahmadtawakol) — shared IP Control status,
  SmartThings backoff, local input and standby control, receiver feedback,
  additional apps, and the off state in Art Mode.
- [@rudizl](https://github.com/rudizl) — the complete Bulgarian translation,
  thumbnail width buckets, the power-per-window fix, async hygiene, Art Store
  slideshows, the power-report diagnostics and the IP Control expert settings.
- [@albertoriella](https://github.com/albertoriella) — local channel number and
  absolute volume.
- [@alienpoop03](https://github.com/alienpoop03) — browser launch over IP
  Control.
- [@nvk86](https://github.com/nvk86) — the SmartThings refresh and matte
  discovery fixes.
- [@luvaas](https://github.com/luvaas) — the identification schema fix.
- [@serjeleone](https://github.com/serjeleone) — custom source lists that
  survive discovery, and local source selection over IP Control.
- [@tothemoonsands](https://github.com/tothemoonsands),
  [@ajguerre1](https://github.com/ajguerre1),
  [@excprocess](https://github.com/excprocess),
  [@bcsteeve](https://github.com/bcsteeve),
  [@IonasElate](https://github.com/IonasElate),
  [@PrestonMcAfee](https://github.com/PrestonMcAfee) and
  [@potatosalad](https://github.com/potatosalad) — diagnosis, logs and testing
  across many TV generations.

The per-release details are in the earlier `RELEASE_NOTES_8.*.md` files and on
the [releases page](https://github.com/TheFab21/ha-samsungtv-smart/releases).
