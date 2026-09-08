# SamsungTV Smart and Art Mode

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/github/v/release/TheFab21/ha-samsungtv-smart?style=flat&color=blue)](https://github.com/TheFab21/ha-samsungtv-smart/releases/latest)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-%3E%3D2025.6.0-green.svg)](https://www.home-assistant.io)
[![License: LGPL v2.1](https://img.shields.io/badge/License-LGPL%20v2.1-yellow.svg)](https://www.gnu.org/licenses/lgpl-2.1)

A custom integration for Home Assistant to control Samsung Smart TVs (Tizen OS), based on the excellent work of [ollo69/ha-samsungtv-smart](https://github.com/ollo69/ha-samsungtv-smart).

If this project is useful to you, you can support its development:

<a href="https://buymeacoffee.com/thefab21" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-black.png" alt="Buy Me A Coffee" height="41" width="174"></a>

**What this fork adds.** It began as a handful of Frame TV fixes and grew a
second control channel. Next to the WebSocket and SmartThings paths it now
speaks Samsung's local **IP Control** (JSON-RPC), so power, input, volume,
picture calibration, speaker output and a reboot button work without the
cloud — on 2020+ sets and on older ones, which use a different port that is
tried automatically.

On a **Frame** it adds Art Mode proper: browse and upload artwork, mattes,
slideshow, brightness and colour temperature, a gallery card and an upload
card, uploads re-encoded to suit the panel — and optional **artwork
identification**, which tells you the title, artist and date of what is on
your wall, in five languages.

Everywhere: **OAuth2** for SmartThings (or a token, or your existing
SmartThings integration), a self-healing art WebSocket, and per-TV polling
you can tune.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Migrating from ollo69](#migrating-from-ollo69ha-samsungtv-smart)
- [Installation](#installation)
- [Configuration](#configuration)
  - [SmartThings Authentication](#smartthings-authentication)
  - [Integration Setup](#integration-setup)
  - [Options](#options)
  - [Reconfigure](#reconfigure)
- [Entities](#entities)
  - [Detecting Art Mode vs. watching TV](#detecting-art-mode-vs-watching-tv)
- [Services](#services)
  - [Standard TV Services](#standard-tv-services)
  - [Driving the TV like a remote (D-pad, Home, Back…)](#driving-the-tv-like-a-remote-d-pad-home-back)
  - [Frame Art Services](#frame-art-services)
- [Frame Art Mode](#frame-art-mode)
  - [What happens to your image](#what-happens-to-your-image)
  - [Thumbnail Downloads](#thumbnail-downloads)
  - [Artwork Identification](#artwork-identification)
- [Automations & Tips](#automations--tips)
- [Troubleshooting](#troubleshooting)
  - [Integration not appearing in Add Integration](#integration-not-appearing-in-add-integration)
  - [SmartThings stops working: "Forbidden"](#smartthings-stops-working-forbidden-in-the-log)
  - [Picture mode does not change the TV at all](#picture-mode-does-not-change-the-tv-at-all)
  - [IP Control reports Art Mode "on" when it isn't](#ip-control-reports-art-mode-on-when-it-isnt)
- [Credits](#credits)

---

## Features

- Full Samsung Smart TV (Tizen OS) control via WebSocket
- Power on/off, volume, source selection, app launching
- SmartThings integration for enhanced status polling (channel info, picture mode, sound mode…)
- Background Philips Hue Sync start/stop control on supported Samsung TVs
- **Three SmartThings authentication methods**: OAuth2, Personal Access Token, or existing ST integration
- **Samsung Frame TV Art Mode** — full artwork management via a dedicated async API
- **Self-healing Art WebSocket** — auto-reconnect + a zombie-channel circuit breaker fix the long-standing "Art Mode turns off randomly, needs a reload" problem
- **Artwork identification (opt-in)** — reverse image search (Google Vision) confirmed by an LLM (Claude / OpenAI / Gemini) shows the title, artist, date and bio of the art on your Frame, in 5 languages
- New dedicated entities: Art Mode switch and Frame Art sensor
- **Picture mode control** — `select` entity with dual-strategy (SmartThings API + WS fallback for HDMI inputs)
- **Audio output selection** — `select` entity for TV speaker / external / **Q-Symphony** when a compatible Samsung soundbar is paired
- **Improved source detection** — REST fallback for Frame 2024 TVs where `supportedInputSources` returns empty; supports custom source names
- **App name resolution** — unknown SmartThings app IDs are parsed and resolved to display names
- Improved WebSocket connection stability — prevents zombie connections and saturation
- Wake-on-LAN support
- Channel and app list management
- Logo fetching for apps and sources
- `folder-gallery-card` Lovelace card bundled — no manual installation required
- `samsung-art-upload-card` Lovelace card bundled — pick an image on any device (phone, laptop) and push it straight to the Frame in one tap
- **Uploads are prepared for the panel** — baseline JPEG, fitted to your screen resolution (8K and portrait included), EXIF rotation applied, **HEIC/HEIF from iPhone accepted**, and never switching the TV away from what you are watching

---

## Requirements

- Home Assistant **≥ 2025.6.0**
- Python packages (installed automatically): `websocket-client`, `wakeonlan`, `aiofiles`, `casttube`, `Pillow`, `pysmartthings>=6.0`
- For Artwork Identification (optional): a Google Cloud Vision API key and an LLM API key (Anthropic, OpenAI or Gemini)
- A Samsung Smart TV running **Tizen OS** (2016+), reachable on the local network
- For SmartThings features: a Samsung account and a SmartThings-registered TV

---

## Migrating from ollo69/ha-samsungtv-smart

This fork is a drop-in replacement for ollo69's integration. Migration is straightforward and preserves entity IDs, automations, and all existing configuration.

### Before you start

- Note down your custom **source list**, **app list**, and **channel list** for each TV: Settings → Integrations → SamsungTV Smart → Configure
- Have your **SmartThings credentials** ready (API key or OAuth token)
- Optionally, install [Spook](https://github.com/frenck/spook) — it will flag any broken entity references after migration, saving you a lot of manual checking

### Migration steps

1. **Remove ollo69's integration** for each TV via Settings → Integrations → SamsungTV Smart → Delete. Do not rename or remove any entities beforehand.

2. **Uninstall ollo69 via HACS** — go to HACS → Integrations, find the ollo69 integration and remove it. Then **verify** that the `samsungtv_smart` folder is gone from your `config/custom_components/` directory (use File Editor or SSH). If it still exists, delete it manually.

   > ⚠️ This step is critical: when migrating between two integrations sharing the same domain (`samsungtv_smart`), HACS can sometimes remove the newly installed files when uninstalling the old one. Always confirm the folder is absent before reinstalling.

3. **Restart Home Assistant** before proceeding. This ensures the old domain is fully cleared from HA's internal registry.

4. **Install this fork** via HACS (add `https://github.com/TheFab21/ha-samsungtv-smart` as a custom repository, see [Installation](#installation)), then restart Home Assistant again.

5. **Verify files are on disk** before trying to add the integration:
   ```bash
   ls /config/custom_components/samsungtv_smart/
   ```
   If the folder is missing or empty despite HACS showing the integration as "Downloaded", use the HACS 3-dot menu → **Redownload** to force a fresh install.

6. **Add each TV** using the badge below or via Settings → Devices & Services → + Add Integration → search **SamsungTV Smart**. Use the **exact same device name** as before — this preserves your entity IDs (e.g. `media_player.living_room_tv`).

7. **Re-enter your SmartThings credentials** when prompted. OAuth2 is recommended for a maintenance-free setup (see [SmartThings Authentication](#smartthings-authentication)).

8. **Restore your source, app, and channel lists** via Settings → Integrations → SamsungTV Smart → Configure for each TV. The format is identical to ollo69.

9. If any entity ID got a `_2` suffix, rename it back via Settings → Entities.

### Key differences from ollo69

- **Art Mode**: use the dedicated `switch.<tv_name>_art_mode` entity to toggle Art Mode instead of the `set_art_mode` service. The switch is more reliable and works consistently across all Frame TV models, including multi-TV setups.
- **Picture mode**: a new `select.<tv_name>_picture_mode` entity is available. If you had automations calling `samsungtv_smart.select_picture_mode`, they continue to work.
- **SmartThings authentication**: OAuth2 is now available and strongly recommended over Personal Access Tokens (PATs expire after 24 hours).
- **Frame 2024 TVs**: source list and picture mode detection are fixed via REST fallbacks — no manual workarounds needed.

---

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and add a custom repository.](https://my.home-assistant.io/badges/hacs_custom_repository.svg)](https://my.home-assistant.io/redirect/hacs_custom_repository/?owner=TheFab21&repository=ha-samsungtv-smart&category=integration)

Or manually add the custom repository in HACS:
1. Go to **HACS → Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/TheFab21/ha-samsungtv-smart` as **Integration**
3. Search for **SamsungTV Smart** and install


### Manual

1. Download or clone this repository.
2. Copy the `samsungtv_smart` folder into your Home Assistant `custom_components` directory:

   ```
   config/
   └── custom_components/
       └── samsungtv_smart/
   ```

3. Restart Home Assistant.

---

## Configuration

### SmartThings Authentication

> ⚠️ **Upcoming SmartThings API pricing change.** Samsung has announced that the
> SmartThings API will move to paid tiers, with free access phasing out around
> **October 2026** (a **$4.99/month "Personal" plan** with a monthly call quota
> for individual/non-commercial developers, plus separate commercial tiers —
> exact quotas not yet published). All SmartThings features of this integration
> (picture mode, channel/app info, sound mode, etc.) call the SmartThings API
> **under your own Samsung account**, so this may eventually apply to you, not
> just to the project itself. Nothing changes today and no code changes are
> required yet — this integration still works exactly as before. We're tracking
> the published quotas and will document any impact (e.g. reducing polling
> frequency) once Samsung releases the details. See the
> [SmartThings blog post](https://blog.smartthings.com/smartthings-updates/a-new-enhanced-smartthings-api-experience/)
> for the announcement.
>
> **How this integration already minimises SmartThings calls.** By design it is
> **local-first**: control (power, apps, sources, keys, volume, and the entire
> Frame Art Mode) goes over the local WebSocket / IP Control path, and the
> SmartThings cloud API is only used as a **secondary enrichment/status layer**
> (and last resort when local state is unavailable). SmartThings polling is
> throttled and **configurable from 5 to 30 seconds** (with a separate, longer
> content-list interval), so you can dial the call volume down to stay within a
> free/cheap tier. If you set the SmartThings poll interval to 30 s, that's at
> most ~2 calls/minute while the TV is on, and effectively none while it's off.

Three methods are available. Choose **one**.

---

#### Option 1 — OAuth2 (Recommended)

This method authenticates via your Samsung account. Tokens are refreshed automatically.

> **Prerequisite — My Home Assistant.** The redirect URI below
> (`https://my.home-assistant.io/redirect/oauth`) is a **relay**, not a page on
> your server: after you authorise, your browser goes to my.home-assistant.io,
> which sends you back to *your* instance using the URL registered there. So
> before starting:
>
> - the **My Home Assistant** integration must be enabled (it is part of
>   `default_config`, but not of a stripped-down `configuration.yaml`);
> - **Settings → System → Network** must have your instance URL set;
> - <https://my.home-assistant.io> must have the same URL under *My Home
>   Assistant Instance URL*, including scheme and port
>   (e.g. `http://192.168.1.50:8123`).
>
> If any of these is missing, clicking **Authorize** ends on a **404: Not
> found** ([#204](https://github.com/TheFab21/ha-samsungtv-smart/issues/204)) —
> the authorisation itself succeeded, the way back did not. This bites Docker
> installs in particular, where no external URL is configured by default.
>
> ⚠️ **Do not replace the hostname with your own.** `https://my.home-assistant.io/redirect/oauth`
> works as a whole; `https://your-ha.example.com/redirect/oauth` gives a 404,
> because `/redirect/oauth` is a route on the relay, not on your server.

**Prefer not to use My Home Assistant?** You can point SmartThings at your own
instance instead — Home Assistant uses this form automatically when My Home
Assistant is not in use. Register this redirect URI instead of the one above:

```text
https://your-ha.example.com/auth/external/callback
```

`/auth/external/callback` is the route Home Assistant genuinely serves. Set the
same URL as your external URL under **Settings → System → Network**. This keeps
the callback on your own domain, with no third-party hop — it does require your
instance to be reachable over HTTPS at that address. Failing that, the
[Personal Access Token](#option-2--personal-access-token-pat) method involves no
redirect at all.

**Step 1 — Create a SmartThings OAuth App (one time)**

> ⚠️ The SmartThings Developer Portal web UI no longer supports creating OAuth apps. Use the SmartThings CLI instead.

1. Install the [SmartThings CLI](https://github.com/SmartThingsCommunity/smartthings-cli).
2. Run `smartthings apps:create` and follow the interactive prompts:
   - **Display Name**: `Home Assistant Samsung TV`
   - **Description**: `For Home Assistant integration of Samsung The Frame TV`
   - **Icon Image URL**: leave blank
   - **Target URL**: leave blank
   - **Scopes**: select `r:devices:*` and `x:devices:*`
   - **Redirect URI**: `https://my.home-assistant.io/redirect/oauth`
   - Select **Finish and create OAuth-In SmartApp**
3. The CLI will display your app credentials — save the **OAuth Client Id** and **Client Secret**.

> ⚠️ Use the **OAuth Client Id**, NOT the App Id.

**Step 2 — Add Application Credentials in Home Assistant**

1. Go to **Settings → Devices & Services**.
2. Click **⋮ → Application Credentials → + Add Application Credentials**.
3. Select **SamsungTV Smart**.
4. Enter your **Client ID** and **Client Secret**.
5. Click **Add**.

> The OAuth2 option will only appear in the integration setup after completing this step.

**Step 3 — Configure the integration**

When adding the integration, select **OAuth2 (Sign in with Samsung)** and follow the login flow.

---

#### Option 2 — Personal Access Token (PAT)

> ⚠️ **Not recommended.** SmartThings Personal Access Tokens have a limited lifetime (24 hours). When the token expires, SmartThings features will stop working silently and you will need to manually generate a new token and reconfigure the integration. Consider using **OAuth2** or **SmartThings Integration Link** for a maintenance-free setup.

1. Go to [https://account.smartthings.com/tokens](https://account.smartthings.com/tokens).
2. Create a new token with at least **Devices** permissions.
3. Copy the token.
4. When adding the integration, select **Personal Access Token** and paste it.

---

#### Option 3 — SmartThings Integration Link

If you already have the native SmartThings integration configured in Home Assistant:

1. When adding the integration, select **Personal Access Token**.
2. In the dropdown, select your existing SmartThings integration instead of entering a token manually.

---

### Integration Setup

[![Start Setup](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=samsungtv_smart)

Click the badge above to open the config flow directly, or follow these steps manually:

1. Go to **Settings → Devices & Services → + Add Integration**.
2. Search for **SamsungTV Smart** and select it.
3. Choose your authentication method (see above).
4. Enter your **TV's IP address** (a static IP or DHCP reservation is strongly recommended).
5. Follow the on-screen steps. Your TV may prompt you to accept the pairing request — accept it.

> **Note**: Custom integrations installed via HACS do not appear under the "Samsung" brand group in the Add Integration search. If you can't find "SamsungTV Smart" in the search results, use the badge above — it opens the config flow directly regardless.

> **Tip**: If your TV is off and you are using Wake-on-LAN, make sure WOL is enabled in your TV's network settings.

---

### Options

After initial setup, click **Configure** on the integration card to access these options:

| Option | Description |
|---|---|
| **Source list** | Define custom input sources (JSON: `{"HDMI 1": "KEY_HDMI1", ...}`); on supported IP Control TVs, local sources can use values such as `IP_HDMI1` |
| **App list** | Define custom app shortcuts (JSON) |
| **App load method** | How to load the app list: All, Default, or Disabled |
| **App launch method** | Standard, Remote, or REST |
| **Power on method** | Wake-on-LAN or SmartThings |
| **WOL repeat count** | Number of WOL packets sent (1–5) |
| **Scan interval** | SmartThings polling interval (seconds) |
| **Use ST channel info** | Fetch live channel info from SmartThings |
| **Use ST status info** | Fetch power/input status from SmartThings |
| **Show channel number** | Display channel number alongside channel name |
| **Use mute check** | Detect mute state via SmartThings |
| **Logo option** | Show logos for apps/channels (local or remote) |
| **Sync turn on/off** | Optional entity to mirror TV power state |
| **External power entity** | Use an external sensor to determine power state |
| **Toggle Art Mode** | Toggle Art Mode when turning on a Frame TV that is already in Art Mode |
| **IP Control poll interval** | How often the local IP Control state is read (5–600 s, default 10). This snapshot carries the input source, picture/sound modes and — on the tuner — the channel number, so it decides how fast those follow a change made with the remote. Local LAN calls, no cloud quota: lower is fine on modern panels, raise it if an older TV is slow to answer |
| **Ping port** | Port used to detect TV presence |
| **WS name** | Name shown on the TV when pairing (default: `[Home Assistant]`) |

> **IP Control moved.** Pairing and the *Enable IP Control* / *Enable IP Control Art Mode* toggles are no longer in this Options screen — they now live under **Reconfigure → IP Control** (see [Reconfigure](#reconfigure) below).

---
#### Local source selection via IP Control

On TVs that support `inputSourceControl`, input sources can be selected
locally without SmartThings by using the `IP_` prefix in the Source list.

For example:

```yaml
HDMI 1: IP_HDMI1
HDMI 2: IP_HDMI2
HDMI 3: IP_HDMI3
HDMI 4: IP_HDMI4
```

The `IP_` prefix is removed before the value is sent to the TV, so
`IP_HDMI4` calls `inputSourceControl` with `HDMI4`.

IP Control must be paired and enabled under **Reconfigure → IP Control**.

Support for `inputSourceControl` is model- and firmware-dependent.
TVs that do not expose this method may return `-32601 "Method not found"`.

---
### How control works — and what *App launch method* actually does

These are two different layers. Don't confuse the **App launch method** option with how
the integration *connects* to the TV.

#### App launch method (an option — app launching only)

The **App launch method** option (*Standard / Remote / REST*) only decides which channel is
used to **launch an application**. It has no effect on power, keys, volume, sources or Art Mode.

| Value | What it does |
|---|---|
| **Standard** | `ms.application.start` over the control WebSocket channel (falls back to remote). Recommended. |
| **Remote** | `ed.apps.launch` over the remote WebSocket channel (same channel as key presses). |
| **REST** | `POST /api/v2/applications/{id}` over HTTP. Deprecated by Samsung on recent Tizen — often a no-op on 2022+ sets, and the integration reports success even when nothing launched. |

> **Recommendation:** leave this on **Standard**. Switch to **Remote** only if a specific app
> won't launch. **REST** is mainly relevant to older TVs.

#### Connection layers (the architecture — they coexist)

The integration does **not** pick a single connection mechanism. Three layers run together,
each with its own job:

| Layer | Transport | Role |
|---|---|---|
| **WebSocket** | Local, ports 8001/8002 | Primary control: power, keys, volume, sources, app launching, and Frame Art Mode. |
| **SmartThings** | Cloud (OAuth2 / PAT) | Status polling (power, input, channel, picture/sound mode) and optional power-on. Read-mostly — not a local command channel. |
| **IP Control** | Local JSON-RPC, port 1516 | Optional. Reliable power on/off without SmartThings, local source selection on supported TVs, plus optional Art Mode control. Paired and toggled under [Reconfigure](#reconfigure). |

> The three layers are complementary, not alternatives. *App launch method* sits **on top of**
> the WebSocket layer (two of its three values are WebSocket channels); it is not a fourth
> connection mechanism.
>
> ---
> 
### Reconfigure

To change connection or credentials after setup, open **Settings → Devices & Services → Samsung TV Smart → Reconfigure**. The flow is split into clear sections so you only touch what you need:

| Section | What it changes |
|---|---|
| **Connection** | TV IP address and WebSocket port (8001, or 8002 for SSL-only TVs). Use **8001** unless your TV only answers on **8002**. The integration also falls back between the two ports automatically at runtime if a firmware update filters the configured one. |
| **Authentication** | The auth method (OAuth2 / Personal Access Token / SmartThings link). For OAuth2, selecting it starts the login flow immediately. |
| **SmartThings device** | Which SmartThings device this TV points at. Re-select it after the TV gets a **new device id** — see below. |
| **IP Control** | Pair the local JSON-RPC channel (port 1516 on 2020+ models, 1515 on 2019 and earlier — both are tried) and, once paired, toggle **Enable IP Control** (reliable power on/off without SmartThings) and **Enable IP Control Art Mode** (⚠️ off by default — see the warning below). To pair, check *Pair now* with the TV **ON and in normal viewing (not Art Mode)** and accept the on-screen prompt. |

> **When to use *SmartThings device*.** A TV re-registers in SmartThings under a
> **new device id** after a mainboard repair, a factory reset, or being removed
> and re-added in the SmartThings app. The stored id is then refused for good,
> with `Forbidden` in the log and every cloud-only value (channel name, picture
> mode, sound mode, power metering) frozen — while local control keeps working.
>
> Re-selecting the TV here writes the new id and reloads the integration, **keeping
> your entities, their history and your automations**. Deleting and re-adding the
> integration would not.
>
> If the TV has just been repaired or reset, link it in the SmartThings app
> **first** — there is nothing to select until it appears there.

> ⚠️ **Do not enable *Enable IP Control Art Mode*** unless you know your firmware handles it — it can break Art Mode entirely and may need a factory reset to recover (seen on QE55LS03D fw 2123). See [IP Control reports Art Mode "on" when it isn't](#ip-control-reports-art-mode-on-when-it-isnt).

---

## Entities

**Not every TV gets every entity** — most are created only when the channel
they depend on is available. A plain (non-Frame) TV without SmartThings or IP
Control gets just the media player and remote; that is expected, not a fault.
The **Requires** column below says what each one needs.

| Entity | Type | Requires | Description |
|---|---|---|---|
| `media_player.<tv_name>` | Media Player | — | Main TV control entity |
| `remote.<tv_name>` | Remote | — | Send remote key sequences |
| `switch.<tv_name>_power` | Switch | — | Power the TV on/off (uses SmartThings when configured, WebSocket/WOL otherwise) |
| `select.<tv_name>_picture_mode` | Select | SmartThings | Change picture mode (Standard, Movie, etc.) |
| `select.<tv_name>_speaker_select` | Select | SmartThings **or** IP Control | Audio output — TV speaker, external, or Q-Symphony when a compatible soundbar is paired |
| `select.<tv_name>_color_tone` | Select | **IP Control** | Colour tone / white balance preset |
| `number.<tv_name>_backlight` | Number | **IP Control** | Panel backlight level |
| `number.<tv_name>_contrast` / `_brightness` / `_sharpness` / … | Number | **IP Control** | Expert picture calibration sliders (normal viewing only) |
| `button.<tv_name>_reboot` | Button | **IP Control** | Reboot the TV |
| `switch.<tv_name>_art_mode` | Switch | Frame TV | Toggle Art Mode on/off |
| `sensor.<tv_name>_frame_art` | Sensor | Frame TV | Currently displayed artwork info |
| `sensor.<tv_name>_personal` / `_store` / `_other` | Sensor | Frame TV | Thumbnail folder size (MB) per subdirectory, with a `file_list` attribute for gallery cards (auto-created in v7) |
| `select.<tv_name>_matte_type` / `_matte_color` | Select | Frame TV | Art Mode matte style and colour |
| `select.<tv_name>_motion_sensitivity` / `_motion_timer` / `_brightness_sensor` | Select | Frame TV | Frame motion detector and ambient light sensor settings |
| `number.<tv_name>_art_mode_brightness` / `_art_mode_color_temperature` | Number | Frame TV | Art Mode panel brightness and colour temperature |
| `sensor.<tv_name>_art_metadata` | Sensor | Frame TV + [Artwork Identification](#artwork-identification) | Title, artist and description of the current artwork |

Where:

- **SmartThings** — an API key and device ID are configured (see [SmartThings Authentication](#smartthings-authentication)).
- **IP Control** — the local JSON-RPC channel (port 1516) is **paired** *and*
  *Enable IP Control* is on, under **Reconfigure → IP Control**. Pairing is
  optional and is **not** done automatically: if you have never paired it, none
  of the entities marked *IP Control* exist. See [Reconfigure](#reconfigure).

  > **Not every Samsung TV has it.** IP Control is a server the TV runs, opened
  > by the *IP Remote* setting in its menus — but many models, older ones
  > especially, do not run it at all, so having *IP Remote* on proves nothing.
  > Pairing tries both known ports: **1516** (2020 and later) and **1515**
  > (2019 and earlier — Samsung moved the port once). If it fails **instantly**
  > with "could not reach the TV", nothing is listening on either and no change
  > of TV settings will help. To confirm from a shell:
  >
  > ```bash
  > nc -vz <tv-ip> 1516 ; nc -vz <tv-ip> 1515
  > ```
  >
  > Everything else in the integration works without it.
- **Frame TV** — the TV reports Art Mode support. Detected automatically.

> **Note:** The `folder-gallery-card` Lovelace card is bundled with this integration and registered automatically. No manual installation or resource configuration required.

> **One-click Frame upload:** The `samsung-art-upload-card` Lovelace card is also bundled and auto-registered. Add it to any dashboard to pick an image on your phone or laptop and push it straight to The Frame — no pre-placed file, no folder sensor, no coding:
>
> ```yaml
> type: custom:samsung-art-upload-card
> entity: media_player.samsung_frame   # optional; a picker is shown if omitted
> matte: shadowbox_polar               # optional default matte
> title: Upload to The Frame           # optional
> ```
>
> The card POSTs the selected image to the integration's authenticated endpoint (`/api/samsungtv_smart/art_upload`), which reuses the `art_upload` service to display and refresh it on the TV.

### Media Player Attributes

In addition to standard media player attributes, the following are available:

- `picture_mode`, `picture_mode_list`
- `sound_mode`, `sound_mode_list`
- `channel`, `channel_name`, `channel_number`
- `app_id`, `source`, `source_list`
- `ip_address`, `config_entry_id`
- `screen_resolution` — the panel's native resolution (e.g. `3840x2160`), used
  to fit uploaded images to the screen
- `art_mode_status` — `on` or `off` on Frame TVs; **absent** when the TV is in
  standby. See [Detecting Art Mode vs. watching TV](#detecting-art-mode-vs-watching-tv)
- `frame_art_last_result` — outcome of the last Frame Art service call

### Detecting Art Mode vs. watching TV

A Frame is a **tri-state** device — standby, Art Mode, or actually being
watched — while a `media_player` only has `on` and `off`:

| the panel is | `media_player` state | `art_mode_status` |
|---|---|---|
| in standby | `off` | *(absent)* |
| showing artwork | `on` | `on` |
| being watched | `on` | `off` |

Art Mode reports `on` because the panel *is* powered: it answers commands,
accepts uploads, and `turn_on` would do nothing. The third state lives in the
attribute. To automate on "someone is actually watching":

```yaml
template:
  - binary_sensor:
      - name: TV actually being watched
        state: >
          {{ is_state('media_player.YOUR_TV', 'on')
             and state_attr('media_player.YOUR_TV', 'art_mode_status') != 'on' }}
        delay_on: "00:00:05"
```

The `delay_on` matters: waking from standby powers the panel *before*
`art_mode_status` settles (up to ~30–45 s when the value comes from the
SmartThings cloud rather than local IP Control), so without it a TV waking
straight into Art Mode briefly looks like someone switched it on. Debouncing
the rise only keeps "turned off" instantaneous.

### Frame Art Sensor Attributes

- `art_mode` — current Art Mode state
- `content_id` — artwork content ID
- `content_type` — artwork category
- `thumbnail_url` — local URL to the thumbnail (if downloaded)

---

## Services

### Standard TV Services

These are called on the `media_player` entity.

| Service | Description |
|---|---|
| `media_player.turn_on` | Turn on the TV (WOL or SmartThings) |
| `media_player.turn_off` | Turn off the TV |
| `media_player.volume_up/down` | Adjust volume |
| `media_player.mute_volume` | Mute/unmute |
| `media_player.set_volume_level` | Set volume level (0.0–1.0) |
| `media_player.select_source` | Switch input source or launch an app |
| `media_player.play_media` | Send a key command or launch a URL |
| `samsungtv_smart.select_picture_mode` | Change picture mode |
| `samsungtv_smart.start_hue_sync` | Start Philips Hue Sync without opening the TV app |
| `samsungtv_smart.stop_hue_sync` | Stop Philips Hue Sync without opening the TV app |
| `samsungtv_smart.send_text` | Type a text string into a native Tizen text field |
| `remote.send_command` | Send raw key commands (via remote entity) |

**Sending key commands via `play_media`:**

```yaml
action: media_player.play_media
target:
  entity_id: media_player.samsung_tv
data:
  media_content_type: send_key
  media_content_id: KEY_MUTE
```

### Driving the TV like a remote (D-pad, Home, Back…)

Everything the on-screen Samsung remote does is available as a service call, so
you can build a remote dashboard, bind keys to physical buttons, or script a
navigation sequence. Use the `remote.<tv_name>` entity — it takes a **list** of
keys and a repeat count:

```yaml
action: remote.send_command
target:
  entity_id: remote.samsung_tv
data:
  command:
    - KEY_HOME
    - KEY_RIGHT
    - KEY_ENTER
  num_repeats: 1
```

The keys for the navigation cluster:

| Button | Key |
|---|---|
| Up / Down / Left / Right | `KEY_UP` / `KEY_DOWN` / `KEY_LEFT` / `KEY_RIGHT` |
| Select / OK | `KEY_ENTER` |
| Back / Return | `KEY_RETURN` |
| Home | `KEY_HOME` |
| Info | `KEY_INFO` |
| Menu | `KEY_MENU` |
| Guide | `KEY_GUIDE` |
| Exit | `KEY_EXIT` |
| Channel list | `KEY_CH_LIST` |

The full key list is in
[Key_codes.md](https://github.com/jaruba/ha-samsungtv-tizen/blob/master/Key_codes.md).
Not every key exists on every model — a key the TV does not know is simply
ignored.

**Timing.** Keys in a sequence are sent 0.5 s apart. When a menu needs longer
to appear, insert a delay in milliseconds as its own step (clamped to
0.2–2 s) — this works in the `media_player.play_media` form, where the sequence
is one `+`-joined string:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.samsung_tv
data:
  media_content_type: send_key
  media_content_id: KEY_HOME+1500+KEY_RIGHT+KEY_ENTER
```

The same string also accepts `ST_` source keys (routed through SmartThings) and
`IP_` source keys (routed through IP Control), so a single sequence can mix
input switching with remote keys.

**Typing text into a native Tizen text field:**

```yaml
action: samsungtv_smart.send_text
target:
  entity_id: media_player.samsung_tv
data:
  text: "hello"
```

**Starting Philips Hue Sync in the background:**

```yaml
action: samsungtv_smart.start_hue_sync
target:
  entity_id: media_player.samsung_tv
```

Use `samsungtv_smart.stop_hue_sync` with the same target to stop syncing.
These services require SmartThings and a Samsung TV with the Philips Hue Sync
TV app and `samsungvd.lightControl` capability. They do not launch the app or
change the active input.

> **Not every TV exposes this capability.** Verified working on an S95C;
> reported absent on a 2022 Frame (`QE65LS03BAUXXH`). Two things can cause that,
> and the error does not tell them apart: the model may not support it at all,
> or the capability may only appear **once the Philips Hue Sync TV app is
> installed on the TV and paired with a Hue bridge**. Set that up first, then
> re-check. To check, with your SmartThings token and device id (both visible in
> *Download diagnostics*):
>
> ```bash
> curl -s -H "Authorization: Bearer YOUR_TOKEN" \
>   https://api.smartthings.com/v1/devices/YOUR_DEVICE_ID \
>   | grep -o 'samsungvd.lightControl' || echo "capability NOT present"
> ```

> **Scope — read this before expecting it to work in streaming apps.** This uses
> Samsung's `SendInputString` (the native **Tizen IME**). It only produces text
> while a **native Tizen text field** is open and focused — for example the
> **Settings search**, the **built-in web browser's** address bar, a **Wi‑Fi
> password**, or a login form. In those fields the on-screen Tizen keyboard is
> visible.
>
> It does **not** work inside the search screens of Netflix, YouTube, Apple TV,
> Prime Video, etc. Those apps draw their **own on-screen character grid** (not a
> Tizen text field), so there is nothing for `SendInputString` to fill. A
> physical Bluetooth keyboard works there because the TV OS injects it as HID
> input at a lower level — but **Samsung's WebSocket remote API exposes no
> alphabet/HID keys** (only directional keys, ENTER, digits, colored and media
> keys), so this cannot be emulated over the network. This is a Samsung API
> limitation, and it's why remote-keyboard cards (e.g. the
> [Universal Remote Card](https://github.com/Nerwyn/universal-remote-card))
> can't type into those app grids on Samsung either.
>
> Equivalent to `media_player.play_media` with `media_content_type: send_text`.

---

### Frame Art Services

These services require a Samsung **Frame TV** with Art Mode. They are called on the `media_player` entity.

| Service | Description |
|---|---|
| `samsungtv_smart.art_get_artmode` | Get current Art Mode status |
| `samsungtv_smart.art_set_artmode` | Enable or disable Art Mode |
| `samsungtv_smart.art_available` | List all available artworks (optionally filtered by category) |
| `samsungtv_smart.art_get_current` | Get info about the currently displayed artwork |
| `samsungtv_smart.art_select_image` | Display a specific artwork by content ID |
| `samsungtv_smart.art_upload` | Upload a local image to the TV — see [What happens to your image](#what-happens-to-your-image) |
| `samsungtv_smart.art_upload_batch` | Upload every image in a folder — idempotent re-runs (skips unchanged files) + throttle for large batches + optional perceptual duplicate check |
| `samsungtv_smart.art_identify` | Identify the current artwork (reverse image search + LLM) and return its metadata. Requires the Art Identification option to be configured |
| `samsungtv_smart.art_delete` | Delete a user-uploaded artwork (MY-* IDs only) |
| `samsungtv_smart.art_get_thumbnail` | Download a single artwork thumbnail |
| `samsungtv_smart.art_get_thumbnails_batch` | Batch-download thumbnails for multiple artworks |
| `samsungtv_smart.art_set_brightness` | Set Art Mode brightness (0–100, mapped to TV scale 1–10) |
| `samsungtv_smart.art_get_brightness` | Get current Art Mode brightness |
| `samsungtv_smart.art_change_matte` | Change the matte/frame style of an artwork |
| `samsungtv_smart.art_set_photo_filter` | Apply a photo filter to an artwork |
| `samsungtv_smart.art_get_photo_filter_list` | List available photo filters |
| `samsungtv_smart.art_get_matte_list` | List available matte styles |
| `samsungtv_smart.art_set_favourite` | Add/remove artwork from favourites |
| `samsungtv_smart.art_set_slideshow` | Configure slideshow (duration, shuffle, category). Alias of `art_set_auto_rotation` — auto-routed to whichever API the TV speaks |
| `samsungtv_smart.art_set_auto_rotation` | Configure auto-rotation (duration, shuffle, category). Alias of `art_set_slideshow` — works on older Frames that don't support the slideshow API |

#### Service Examples

**Select an artwork:**

```yaml
action: samsungtv_smart.art_select_image
target:
  entity_id: media_player.samsung_frame
data:
  content_id: SAM-F0206
  show: true
```

**Upload a local image:**

```yaml
action: samsungtv_smart.art_upload
target:
  entity_id: media_player.samsung_frame
data:
  file_path: /config/www/my_art.jpg
  matte_id: modern_apricot
  file_type: jpg
```

**Batch download thumbnails:**

```yaml
action: samsungtv_smart.art_get_thumbnails_batch
target:
  entity_id: media_player.samsung_frame
data:
  category_id: MY-C0002
  favorites_only: false
  force_download: false
```

**Batch upload a folder (idempotent):**

```yaml
action: samsungtv_smart.art_upload_batch
target:
  entity_id: media_player.samsung_frame
data:
  folder: /config/www/frame_upload
  # skip_unchanged: true   # (default) re-running uploads only new/changed files
  # throttle: 2.0          # (default) seconds between uploads
  # dedup: true            # also skip photos already on the TV (needs thumbnails downloaded)
```

**Configure slideshow:**

```yaml
action: samsungtv_smart.art_set_slideshow
target:
  entity_id: media_player.samsung_frame
data:
  duration: 15min
  shuffle: true
  category_id: 2
```

> **Note on Frame generations.** The slideshow feature uses different underlying
> APIs depending on firmware: newer Frames (2024+) use `slideshow_status`, while
> older Frames (≈2020–2021) only support `auto_rotation_status`. The integration
> detects which one your TV speaks on first use and routes automatically, so
> `art_set_slideshow` and `art_set_auto_rotation` are interchangeable aliases —
> use either. `duration` accepts the presets (`3min`, `15min`, `1h`, `12h`, `1d`,
> `7d`) or any integer number of minutes (e.g. `30`, `180`); some models reject
> values outside their supported set.

---

## Frame Art Mode

### Overview

Frame TVs can display artwork when not in use. This integration provides full programmatic control over the Art Mode, including artwork selection, brightness, matting, filters, and slideshow settings.

### Content IDs

Artworks are identified by content IDs:

| Prefix | Source |
|---|---|
| `SAM-*` | Samsung Art Store content |
| `MY_F*` | User-uploaded photos |
| `MY-C*` | Categories (MY-C0002=My Photos, MY-C0004=Favorites, MY-C0008=All) |

### Matte Styles

Format: `type_color`

> ⚠️ Available matte types and colors are **retrieved dynamically from your TV** at startup and vary by model and firmware. The `select.samsung_*_matte_type` and `select.samsung_*_matte_color` entities are populated automatically with the options your TV actually supports. Call `samsungtv_smart.art_get_matte_list` to see the full list for your device.

**Example** (QE55LS03D 2024): `modern_apricot`, `shadowbox_polar`

### Photo Filters

Available filters: `none`, `mono`, `original`, `ink`, `watercolor`, `oil`, `pastel`, `posterize`, `noir`, `quartertone`

### What happens to your image

Frame TVs are strict about what they accept, and a rejected upload usually
fails as a grey rectangle rather than an error. Every upload — from the
`art_upload` and `art_upload_batch` services, the upload card and the gallery
card alike — is therefore normalised first:

- **Converted to baseline JPEG** (never progressive), 4:2:0 chroma, quality 92.
  Maximum quality was tried and the TVs refuse it.
- **Fitted to your panel's resolution**, read from the `screen_resolution`
  attribute — so 8K panels are not downscaled to 4K, and **portrait images keep
  their orientation** instead of being fitted to a landscape box.
- **EXIF orientation applied, then all metadata stripped.** Photos that
  appeared rotated on the TV no longer do.
- **HEIC/HEIF accepted.** Photos straight from an iPhone work without
  converting them first.

Uploading does **not** change what is on screen: it never switches the TV into
Art Mode, and never pulls it away from the input you are watching. The artwork
is added to the Frame's library, exactly as the SmartThings app does it.

### Thumbnail Downloads

Thumbnails are automatically organized and saved to a **per-TV** directory keyed
by the config-entry ID (new in v7 — see the v7 changelog for migration notes):

```
config/www/frame_art/{entry_id}/
├── current.jpg  ← currently displayed artwork
├── personal/    ← user-uploaded photos (MY-F*)
├── store/       ← Samsung Art Store (SAM-*)
└── other/       ← everything else
```

Find your `{entry_id}` on the `sensor.<tv_name>_frame_art` entity (Developer
Tools → States) — it's exposed as the `entry_id` attribute, and the ready-to-use
URL base is in `thumbnail_folder`.

These are accessible via Home Assistant's `/local/` URL path, making them
directly usable in Lovelace dashboards and galleries.

**Smart caching**: thumbnails are only downloaded once. Subsequent calls to
`art_get_thumbnail` or `art_get_thumbnails_batch` skip files that already exist,
making batch operations fast on repeat runs. Use `force_download: true` to
override.

**Resilient `current.jpg`**: if a live thumbnail fetch for the current artwork
fails (a transient TV/WebSocket hiccup), the integration reuses a previously
downloaded copy of that artwork as `current.jpg` instead of showing an error
placeholder. (Contributed by @prestonmcafee.)

### Artwork Identification

*Opt-in.* Identify the artwork currently on the Frame and show its **title,
artist, date and a short bio** — in the viewer's language — via a two-stage,
cache-first pipeline: a **reverse image search** (Google Cloud Vision) proposes
candidates from the real web, then an **LLM** (Anthropic / OpenAI / Gemini)
confirms only a genuine match against the image (so it doesn't hallucinate on
obscure works, and identifies photographs too).

**Enable it** under **Settings → Devices & Services → your TV → Configure →
Art Identification**: turn it on, paste a Google Vision API key, pick the LLM
provider and paste its key. Keys are stored in the config entry, never in YAML.

Once a provider and key are saved, the **Model** field becomes a dropdown of
the models your key can actually use, read live from the provider — so a model
the provider retires no longer breaks identification silently. It stays
free-text-capable for a brand-new model or a fine-tune, and **leaving it blank
picks the best available model automatically**.

Reverse image search fails more often than you would expect on artwork, so when
it returns nothing usable the model may identify a piece it recognises on its
own. Those answers are capped at **confidence 0.6**, so the attribute stays
meaningful: above 0.6 means corroborated by the web, at or below means the
model recognised it unaided.

Once enabled, **`sensor.<tv>_art_metadata`** identifies each artwork
automatically as it changes (debounced) and exposes the metadata as attributes,
including a `translations` map with **5 languages** (`en`, `fr`, `es`, `it`,
`pt-BR`) so each viewer can read it in their own UI language. A ready-to-use
Lovelace card (plain and per-viewer-language variants) is in
`RELEASE_NOTES_8.4.0.md`. There is also a manual `samsungtv_smart.art_identify`
service (returns the metadata; `force: true` bypasses the cache).

Results are cached per artwork (by the stable `SAM-*` Art-Store id, or by image
content for personal uploads), so identification runs once and is then instant
and free. Google Vision is free under ~1000 requests/month; the LLM is rarely
called thanks to the cache. Personal-photo identification is off by default.

## See Also

- **[Frame Art Gallery Guide](Frame_Art_Gallery.md)** - Interactive Lovelace galleries
- **[Frame Art Guide](Frame_Art.md)** - Complete service documentation
---

## Automations & Tips

### Wake-on-LAN with delayed command

Samsung TVs may need a moment to become responsive after WOL. Use a delay in automations:

```yaml
automation:
  - alias: "Turn on TV and switch to HDMI 1"
    trigger:
      - platform: ...
    action:
      - service: media_player.turn_on
        target:
          entity_id: media_player.samsung_tv
      - delay: "00:00:08"
      - service: media_player.select_source
        target:
          entity_id: media_player.samsung_tv
        data:
          source: HDMI 1
```

### Preventive maintenance (integration reload)

To prevent WebSocket connection saturation over time, you can schedule a periodic reload:

```yaml
automation:
  - alias: "Reload SamsungTV integration nightly"
    trigger:
      - platform: time
        at: "03:00:00"
    action:
      - service: homeassistant.reload_config_entry
        target:
          entity_id: media_player.samsung_tv
```

### Art Mode automation on TV off

```yaml
automation:
  - alias: "Enable Art Mode when TV turns off"
    trigger:
      - platform: state
        entity_id: media_player.samsung_frame
        to: "off"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.samsung_frame_art_mode
```

---

## Troubleshooting

### Integration not appearing in Add Integration

Custom integrations installed via HACS do not appear under branded groups (e.g. "Samsung") in the Add Integration search. Use the badge in [Integration Setup](#integration-setup) to open the config flow directly, or search for `samsungtv` (not `samsung`) in the search box.

If the config flow still fails with *"This integration cannot be added from the UI"*, the most likely cause is that the integration files are missing from disk despite HACS showing it as installed — a known issue when migrating from ollo69 (same domain, same folder name).

**Verify files are present:**
```bash
ls /config/custom_components/samsungtv_smart/
```

If the folder is missing or empty, use HACS → SamsungTV Smart → ⋮ → **Redownload**, select the version explicitly, then restart Home Assistant.

If the folder exists but the flow still fails, enable DEBUG logging and check for errors on startup:
```yaml
# configuration.yaml
logger:
  default: warning
  logs:
    custom_components.samsungtv_smart: debug
```

### TV not found during setup

- Make sure the TV is on and connected to the same network as Home Assistant.
- Ensure the TV's IP address is correct and reachable (`ping <ip>`).
- Disable any VLANs or firewall rules blocking ports **8001** and **8002**.

### TV accepts pairing but integration shows unavailable

- The first pairing token is stored in the config entry. If the token is rejected, delete the integration and re-add it — the TV will prompt again for pairing.
- Make sure you **Accept** the pairing request on the TV screen within the timeout window.

### WebSocket connectivity issues / TV becomes unresponsive

Samsung TVs have strict limits on simultaneous WebSocket connections. If the integration creates too many connections without properly closing them, the TV's SmartThings service can become saturated.

Signs of this issue:
- TV stops responding to commands
- Logs show repeated `WebSocketProtocolException` or connection refused errors
- Issue resolves temporarily after a TV restart

Mitigations built into this fork:
- Proper handling of invalid WebSocket close opcodes
- Active connection cleanup to prevent zombie connections
- Use the **nightly reload automation** above as a preventive measure.

### Recurring "IP Control state read failed" / "Host is unreachable" errors

If your log shows repeated errors like this, spaced minutes apart, for a TV that is otherwise powered on and working:

```
Error fetching IP Control state <name> data: IP Control state read failed:
transport failure talking to <ip>:1516: [Errno 113] Host is unreachable
Error fetching IP Control state <name> data: IP Control state read failed:
transport failure talking to <ip>:1516: timed out
```

These are network-layer errors (the TV briefly becomes unreachable at the IP
level), not something the integration can retry around — it just means the
TV's network connection dropped for a moment.

The most common cause is a **DHCP lease renewal hiccup**: even with a DHCP
reservation, a short or unstable lease can cause brief unreachability when it
renews. The fix is to **configure a fully static IP on the TV itself**
instead of relying on a router-side reservation:

- On the TV: **Settings → General → Network → Network Status/Settings → IP Settings → Manual**, and enter the IP, subnet, gateway and DNS yourself.

This removes DHCP renewal from the equation entirely. If the errors persist
after switching to a static IP, also check the network switch port the TV is
plugged into (port resets, re-negotiation, rising CRC/error counters point to
a cabling/port issue rather than the integration).

### "Host is unreachable" / connection-failure logs only when the TV is OFF

If the errors above happen **only while a TV is powered off** (and stop once it's
on), this is expected behaviour for some Frames — **not** a bug or a network
problem.

Older Frames (notably the **2020 and 2021** models) **drop off the network
entirely in standby**: their network chip powers down, so they stop answering
*everything* incoming — ping, IP Control (port 1516) and the Art WebSocket. From
the integration's side that's indistinguishable from "the TV is simply off", so a
read failure there just means the TV is off. (2024+ Frames keep their network
interface alive in standby and still answer a ping, so they don't show this.)

You can confirm which case you have: with the TV off, `ping <tv-ip>` from any
machine. No reply → that TV goes fully off the network in standby, and the
off-state log lines are normal.

Recent versions already keep this quiet: while a TV is off, IP Control transport
failures and Art channel connection failures are logged at **DEBUG** (with at most
a single INFO line), not ERROR/WARNING — so real problems still stand out.

**"Then how does Home Assistant turn it back on if it's off the network?"** — it
doesn't *reach* the TV, it *pushes* a wake signal, and neither path needs the TV
to be reachable:

- **SmartThings (cloud):** even in deep standby the TV keeps an *outbound*
  connection to Samsung's cloud (an always-on part, separate from the main
  network stack). The power-on command goes to the SmartThings cloud, which pushes
  it down that channel.
- **Wake-on-LAN:** the network card's WoL engine listens for a magic packet at the
  Ethernet level even while the OS network stack is down; a magic packet expects no
  reply, so it works on a TV that won't answer a ping.

Set the wake method per TV with the **Power On Method** option (Wake-on-LAN by
default; SmartThings or IP Control also available).

### SmartThings features not working

- Verify your API key/token has `Devices` permissions.
- Check that your TV is registered and visible in the SmartThings app.
- For OAuth2: confirm your Developer Portal app is still active and has the correct scopes (`r:devices:*`, `x:devices:*`).

### SmartThings stops working: "Forbidden" in the log

```text
[192.168.1.50] Error updating SmartThings status: Forbidden
media_player.your_tv - SmartThings refused this device 3 times in a row (Forbidden).
```

This means the stored **SmartThings device id no longer belongs to your account**
— not that your API key or token is wrong. The tell: any *other* TV on the same
credentials keeps working.

It happens when the TV re-registers under a new id: a **mainboard repair or
replacement**, a **factory reset**, being **removed and re-added** in the
SmartThings app, or a change of owning account.

What the integration does on its own:

- local control (power, keys, sources, Art Mode) keeps working throughout — only
  the cloud-only values pause;
- after three refusals it **slows SmartThings polling to once every 5 minutes**
  instead of every few seconds, and raises a notification explaining the cause;
- both clear themselves the moment a poll succeeds, so a fix is picked up without
  a restart.

To fix it:

1. If the TV was just repaired or reset, **link it in the SmartThings app first**.
   Home Assistant has nothing to select until it appears there.
2. Open **Settings → Devices & Services → Samsung TV Smart → Reconfigure →
   SmartThings device** and pick the TV again.

Your entities, history and automations are preserved — unlike deleting and
re-adding the integration.

### OAuth2 — "Token refresh failed"

If the SmartThings OAuth token can no longer be refreshed, the integration raises
an alert in **Settings → Repairs** ("SmartThings authentication failed for …")
explaining the cause and pointing you to the fix. The alert clears automatically
once authentication is restored. To recover:

1. Check internet connectivity from Home Assistant.
2. Verify your OAuth app is still active by running `smartthings apps` in the SmartThings CLI.
3. Re-authenticate: go to **Settings → Devices & Services → Samsung TV Smart → Reconfigure**.

A refresh token can become invalid if it is revoked, expires, or is rotated by
SmartThings (which can happen after repeated re-authentication). Reconfiguring
issues a fresh token pair.

### Source list empty on Frame 2024 TVs

The SmartThings `supportedInputSources` attribute returns empty on some 2024 Frame TV models. This fork automatically falls back to a REST API call (`samsungvd.mediaInputSource`) to retrieve the full source list with custom names. If sources still appear missing, check that SmartThings is properly configured and the TV is reachable.

### Picture mode not updating after change

SmartThings caches the picture mode value. This fork automatically sends a `refresh` command after any `setPictureMode` call to force the TV to report the new value. If the `select` entity still shows a stale value, wait a few seconds for the next poll cycle.

### Picture mode does not change the TV at all

The error now names the response SmartThings gave for every attempt, and the
code tells you where the problem is:

| response | meaning | what to do |
|---|---|---|
| **422** Unprocessable Entity | that capability does not exist on your model | nothing — the other capability is tried automatically |
| **429** Too Many Requests | rate limited | wait a minute; avoid changing mode repeatedly in quick succession |
| **409** Conflict on *every* attempt | the cloud will not deliver commands to your TV | see below |

Also look for this warning, which is the decisive one:

```text
SmartThings accepted refresh/refresh but the TV did not execute it (result: FAILED)
— the TV is registered in the cloud but the cloud cannot reach it.
```

**A `FAILED` result, or a 409 on every attempt, is not an integration problem.**
It means Samsung's cloud has your TV registered but cannot talk to it, so it
serves the last state it knows while accepting and dropping every command. The
tell-tale signs: nothing works from the **SmartThings mobile app** either, and
changes made on the TV with its own remote never appear in Home Assistant.

The usual cause is the TV's own internet access being filtered — Pi-hole,
AdGuard Home or a router blocklist catching Samsung's cloud endpoints as
"telemetry". To check:

1. In your DNS filter's query log, filter by the TV's IP address and look for
   **blocked** entries.
2. Allow `*.samsungcloudsolution.com`, `*.samsungcloudsolution.net`,
   `*.samsungiotcloud.com`, `*.samsungosp.com` and `*.samsungqbe.com`.
3. **Power-cycle the TV at the mains** — a standby toggle is not enough, the
   cloud connection is only re-established on a cold boot.

Picture mode also has a local fallback: a WebSocket remote key is sent straight
to the TV alongside the cloud command, which works on TVs that honour those
keys regardless of SmartThings. `FILMMAKER MODE` and `Natural` have no
dedicated key and are cloud-only.

### Frame Art services not working

- These services require a Samsung **Frame** TV with Art Mode capability.
- Make sure the TV is on (not just in Art Mode).
- Check that port **8002** (encrypted WebSocket) is not blocked.

### Art Mode fails briefly after TV wakes from standby

When the TV wakes from standby (e.g. via an automation), the WebSocket connection needs a short time to re-establish before the Art API becomes available. This is normal — the integration will self-recover within a minute. If you experience consistent failures in morning automations, add a 60–90 second delay after the TV turns on before triggering Art Mode commands.

### IP Control reports Art Mode "on" when it isn't

> [!WARNING]
> **Do not enable *Enable IP Control Art Mode* unless you know your firmware handles it correctly.** On affected firmwares it can leave **Art Mode completely broken** (detection stuck/flickering, switching unreliable) — and the damage can persist at the TV level, requiring a **factory reset** to recover. This was observed on a **QE55LS03D with firmware 2123**. The option is **off by default**; leave it off and use the WebSocket / Frame Art path, which is unaffected. Power on/off over IP Control is a separate setting and is **not** impacted.

On some Frame TVs the local IP Control (JSON-RPC, port 1516) `artModeControl` flag can **wedge "on"**: it keeps returning `artModeOn` even when the TV is on a real input (e.g. HDMI), so `art_mode_status` is reported as `on` permanently or flickers between `on` and `off`. The flag is wrong at the source — the same value is returned even when querying the TV directly, outside Home Assistant. The actual panel state in that situation is given by `getTVStates.pictureMode` (`Ambient` only while art is really on the panel).

This typically appears **after a TV factory reset and re-pairing** of the IP Control channel, and looks like a TV firmware fault.

**Workarounds, in order of preference:**

1. **Disable Art Mode over IP Control.** Under **Reconfigure → IP Control**, turn **Enable IP Control Art Mode** off (this is the default). Art Mode detection **and switching** then fall back to the WebSocket / Frame Art channel, which is unaffected, and no `artModeControl` request — read or write — is sent to the TV. Power on/off over IP Control (**Enable IP Control** / the *IP Control* power-on method) keeps working — only the Art Mode path is disabled.

   > **Before 8.7.7 this was only half true.** The option disabled the `artModeControl` *getter*, but art-mode switching still sent `artModeOn`/`artModeOff` over JSON-RPC. If you disabled the option on the advice above and still saw Art Mode misbehave, that is why — update to 8.7.7 or later, where "off" means no `artModeControl` traffic at all. To stop **every** IP Control write to a TV you suspect, turn off **Enable IP Control** itself.
2. **Factory reset the TV.** If you need IP Control for Art Mode and the flag is wedged, the only known way to clear the stuck `artModeControl` flag on the TV side is a **factory reset of the TV** (Settings → General → Reset), followed by re-pairing. There is no remote/API command that unsticks it.

Once a firmware update reports `artModeControl` correctly again, you can re-enable **Enable IP Control Art Mode** under **Reconfigure → IP Control**.

---

## Credits

This project was a fork of [ollo69/ha-samsungtv-smart](https://github.com/ollo69/ha-samsungtv-smart), itself based on work by [@jaruba](https://github.com/jaruba) and [@screwdgeh](https://github.com/screwdgeh).

Channel logos provided by [jaruba/channel-logos](https://github.com/jaruba/channel-logos) by [@jaruba](https://github.com/jaruba) — used by the media-player logo feature.

Frame Art API based on [xchwarze/samsung-tv-ws-api](https://github.com/xchwarze/samsung-tv-ws-api) (art-updates branch), with contributions from Matthew Garrett and Nick Waterton.

WebSocket library: [websocket-client](https://github.com/websocket-client/websocket-client) / [Xchwarze](https://github.com/Xchwarze).

Special thanks to [@PrestonMcAfee](https://github.com/PrestonMcAfee) and [@potatosalad](https://github.com/potatosalad) for extensive real-world testing across multiple TV generations and detailed bug reports — many of the 8.0.0 reliability fixes exist because of their feedback.

---

*Licensed under the GNU Lesser General Public License v2.1.*
