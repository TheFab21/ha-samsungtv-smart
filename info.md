# SamsungTV Smart and Art Mode

Control Samsung Smart TVs (Tizen OS) from Home Assistant, with first-class
support for **The Frame**. An evolution of
[ollo69/ha-samsungtv-smart](https://github.com/ollo69/ha-samsungtv-smart),
now well beyond the original.

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

This is a **custom repository** (it is not in the default catalogue, because
the `samsungtv_smart` domain belongs to the upstream project). Home Assistant
2026.11 replaced HACS with the built-in **Marketplace**.

1. Settings → Marketplace → **Add from link** (top right)
2. Enter `TheFab21/ha-samsungtv-smart` and select **Add** (approve the GitHub
   device sign-in the first time — custom repositories need it)
3. Open **SamsungTV Smart**, download it, then restart Home Assistant.

On Home Assistant before 2026.11, use HACS → Integrations → ⋮ → **Custom
repositories** instead.

Full setup, SmartThings authentication and options are in the
[README](https://github.com/TheFab21/ha-samsungtv-smart#readme).
