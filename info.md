# SamsungTV Smart and Art Mode

Control Samsung Smart TVs (Tizen OS) from Home Assistant, with first-class
support for **The Frame**. A maintained fork of
[ollo69/ha-samsungtv-smart](https://github.com/ollo69/ha-samsungtv-smart).

## What this fork adds

- **Local IP Control** (JSON-RPC) next to WebSocket and SmartThings — power,
  input, volume, picture calibration, speaker output and a reboot button work
  without the cloud, on 2020+ sets and on older ones (a different port is tried
  automatically).
- **Local quick settings** over the TV's own quick-settings channel (no
  pairing, no cloud): the picture modes of the current input, sound mode and
  ten quick settings, inputs and home-screen apps opened locally, and the app's
  icon as the media image. With IP Control, a Frame works day to day without
  SmartThings.
- **Art Mode** on a Frame: browse and upload artwork, mattes, slideshow,
  brightness and colour temperature, a gallery card and an upload card, uploads
  re-encoded to suit the panel.
- Optional **artwork identification** — title, artist and date of what is on
  your wall, in five languages.
- **OAuth2** for SmartThings (or a token, or your existing SmartThings
  integration), a self-healing art WebSocket, and per-TV polling you can tune.

## Install

This is a HACS **custom repository** (it is not in the HACS default store,
because the `samsungtv_smart` domain belongs to the upstream project).

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/TheFab21/ha-samsungtv-smart` as **Integration**
3. Search for **SamsungTV Smart** and install, then restart Home Assistant.

Full setup, SmartThings authentication and options are in the
[README](https://github.com/TheFab21/ha-samsungtv-smart#readme).
