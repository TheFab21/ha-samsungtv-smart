# 🖼️ Frame Art Mode - Complete Guide

This guide covers all Frame Art Mode features for Samsung Frame TV integration.

---

## Table of Contents

- [Overview](#overview)
- [Requirements](#requirements)
- [Entities](#entities)
- [Available Services](#available-services)
- [Thumbnail Management](#thumbnail-management)
- [Configuration Examples](#configuration-examples)
- [Automation Examples](#automation-examples)
- [Troubleshooting](#troubleshooting)

---

## Overview

Frame Art Mode allows you to:
- 🎨 Display artwork when TV is off
- 📸 Manage personal photos and Art Store images
- 🖼️ Change frame styles (mattes)
- 💡 Control brightness levels
- 🌡️ Adjust color temperature (warm/cool tones)
- ⭐ Organize favorites
- 🎬 Create slideshows and auto-rotation
- 📤 Upload custom images
- 🎭 Apply photo filters

---

## Requirements

### Hardware
- Samsung Frame TV (2018+ models)
- Tested on: QE55LS03D (2024), QE55LS03B (2023), QE55LS03A (2022)

### Software
- Home Assistant 2024.1+
- SamsungTV Smart integration installed

### Network
- TV and Home Assistant on same network
- Stable connection (wired recommended)

---

## Entities

### Switch: `switch.samsung_*_frame_art_mode`

Toggle Art Mode on/off with automatic retry logic.

**Features:**
- 3 retry attempts with exponential backoff
- Automatic TV wake-up if needed
- Real-time state updates

### Sensor: `sensor.samsung_*_frame_art`

Main Frame Art sensor with current status and artwork information.

**State:** Current art mode status (`on`, `off`, `unavailable`)

**Attributes:**
| Attribute | Description |
|-----------|-------------|
| `current_content_id` | Currently displayed artwork ID |
| `current_matte_id` | Current matte style (e.g., `shadowbox_polar`) |
| `current_thumbnail_url` | URL to current artwork thumbnail |
| `artwork_count` | Total number of available artworks |
| `slideshow_status` | Slideshow active status |
| `entry_id` | This TV's config-entry ID (used in the per-TV thumbnail path) |
| `thumbnail_folder` | Ready-to-use `/local/frame_art/{entry_id}` base path |

### Sensors: `sensor.samsung_*_personal` / `_store` / `_other`

**Auto-created in v7** — one trio per Frame TV, tracking each thumbnail
subdirectory under the per-TV folder. No manual configuration needed.

**State:** folder size in MB (icon `mdi:folder-image`)

**Attributes:** `path`, `filter`, `number_of_files`, `bytes`, `file_list`

The `file_list` attribute is consumed directly by the bundled gallery card's
`folder_sensor` option, so you can build galleries without declaring any manual
`folder` platform sensor. See the [Frame Art Gallery guide](Frame_Art_Gallery.md).

### Sensor: `sensor.samsung_*_illuminance`

Ambient light sensor (SmartThings).

**State:** Illuminance in lux

### Sensor: `sensor.samsung_*_brightness_intensity`

Art Mode brightness level.

**State:** Brightness percentage (0-100)

### Number: `number.samsung_*_art_mode_brightness`

Interactive slider for Art Mode brightness. Adjustable from the HA UI without writing automations.

**Range:** 0-100 (step 10) — converted internally to the TV's native 1-10 scale

### Number: `number.samsung_*_art_mode_color_temperature`

Interactive slider for Art Mode color temperature.

**Range:** -5 (warmest) to +5 (coolest), step 1 — `0` is neutral

---

## Available Services

### Art Mode Control

#### `samsungtv_smart.art_get_artmode`

Get current Art Mode status.

```yaml
service: samsungtv_smart.art_get_artmode
target:
  entity_id: media_player.samsung_frame
```

#### `samsungtv_smart.art_set_artmode`

Enable or disable Art Mode.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `enabled` | boolean | Yes | `true` to enable, `false` to disable |

```yaml
service: samsungtv_smart.art_set_artmode
target:
  entity_id: media_player.samsung_frame
data:
  enabled: true
```

---

### Artwork Selection

#### `samsungtv_smart.art_select_image`

Display a specific artwork on Frame TV.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `content_id` | string | Yes | - | Artwork ID (e.g., `MY_F0001`, `SAM-S2701`) |
| `category_id` | string | No | - | Category filter |
| `show` | boolean | No | `true` | Show immediately |

```yaml
service: samsungtv_smart.art_select_image
target:
  entity_id: media_player.samsung_frame
data:
  content_id: SAM-S2701
  show: true
```

**Content ID Types:**
- Personal photos: `MY_F0001`, `MY_F0002`, etc.
- Art Store: `SAM-S2701`, `SAM-S0700`, etc.

#### `samsungtv_smart.art_available`

Get list of available artwork.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `category_id` | string | No | Filter by category (e.g., `MY-C0002`, `MY-C0004`) |

**Category IDs:**
- `MY-C0002` - Personal photos
- `MY-C0004` - Favorites

```yaml
service: samsungtv_smart.art_available
target:
  entity_id: media_player.samsung_frame
data:
  category_id: MY-C0004
```

#### `samsungtv_smart.art_get_current`

Get information about currently displayed artwork.

```yaml
service: samsungtv_smart.art_get_current
target:
  entity_id: media_player.samsung_frame
```

---

### Brightness Control

#### `samsungtv_smart.art_set_brightness`

Adjust Frame TV brightness.

**Parameters:**
| Parameter | Type | Required | Range | Description |
|-----------|------|----------|-------|-------------|
| `brightness` | integer | Yes | 0-100 | Brightness level |

```yaml
service: samsungtv_smart.art_set_brightness
target:
  entity_id: media_player.samsung_frame
data:
  brightness: 50
```

**Typical Values:**
- 100: Maximum (sunny rooms)
- 80: High (morning/daytime)
- 50: Medium (normal use)
- 30: Low (evening)
- 0: Minimum (night)

#### `samsungtv_smart.art_get_brightness`

Get current brightness level.

```yaml
service: samsungtv_smart.art_get_brightness
target:
  entity_id: media_player.samsung_frame
```

---

### Color Temperature Control

Adjust the warm/cool tone of artwork displayed in Art Mode. Useful for matching the room ambience (warmer in the evening, cooler during the day).

#### `samsungtv_smart.art_set_color_temperature`

Set Frame TV Art Mode color temperature.

**Parameters:**
| Parameter | Type | Required | Range | Description |
|-----------|------|----------|-------|-------------|
| `color_temperature` | integer | Yes | -5 to +5 | Color temperature value |

```yaml
service: samsungtv_smart.art_set_color_temperature
target:
  entity_id: media_player.samsung_frame
data:
  color_temperature: 0
```

**Typical Values:**
- `-5`: Warmest (most reddish/yellow tones — cozy evening look)
- `-2`: Slightly warm
- `0`: Neutral (default)
- `+2`: Slightly cool
- `+5`: Coolest (most bluish tones — bright daylight look)

#### `samsungtv_smart.art_get_color_temperature`

Get current color temperature value.

```yaml
service: samsungtv_smart.art_get_color_temperature
target:
  entity_id: media_player.samsung_frame
```

---

### Matte (Frame) Control

#### `samsungtv_smart.art_change_matte`

Change the frame style around artwork.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content_id` | string | Yes | Artwork ID to apply matte to |
| `matte_id` | string | Yes | Matte style ID (format: `{type}_{color}`) |

```yaml
service: samsungtv_smart.art_change_matte
target:
  entity_id: media_player.samsung_frame
data:
  content_id: MY_F0001
  matte_id: shadowbox_polar
```

**Available Matte Types and Colors:**

> ⚠️ Available mattes and colors are **retrieved dynamically from your TV** at startup and depend on your model and firmware version. The `select.samsung_*_matte_type` and `select.samsung_*_matte_color` entities are populated automatically with the options your TV actually supports.

To see the full list supported by your TV, call:

```yaml
service: samsungtv_smart.art_get_matte_list
target:
  entity_id: media_player.samsung_frame
```

Results are logged at INFO level.

**Format:** `{type}_{color}` (e.g., `shadowbox_polar`, `modern_black`)

**Examples observed on QE55LS03D (2024):**
- Types: `none`, `modernthin`, `modern`, `modernwide`, `flexible`, `shadowbox`, `panoramic`, `triptych`, `mix`, `squares`
- Colors: `black`, `neutral`, `antique`, `warm`, `polar`, `sand`, `seafoam`, `sage`, `burgandy`, `navy`, `apricot`, `byzantine`, `lavender`, `redorange`, `skyblue`, `turquoise`

#### `samsungtv_smart.art_get_matte_list`

Get list of available matte styles for the TV.

```yaml
service: samsungtv_smart.art_get_matte_list
target:
  entity_id: media_player.samsung_frame
```

---

### Photo Filters

#### `samsungtv_smart.art_set_photo_filter`

Apply a photo filter to artwork.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content_id` | string | Yes | Artwork ID |
| `filter_id` | string | Yes | Filter ID |

```yaml
service: samsungtv_smart.art_set_photo_filter
target:
  entity_id: media_player.samsung_frame
data:
  content_id: MY_F0001
  filter_id: mono
```

#### `samsungtv_smart.art_get_photo_filter_list`

Get list of available photo filters.

```yaml
service: samsungtv_smart.art_get_photo_filter_list
target:
  entity_id: media_player.samsung_frame
```

---

### Slideshow & Auto-Rotation

> **Frame generations.** Samsung uses two different APIs for this feature
> depending on firmware. Newer Frames (2024+) use `slideshow_status`; older
> Frames (≈2020–2021) only support `auto_rotation_status`. The integration
> detects which one your TV speaks on first use, persists the choice, and routes
> all reads/writes automatically. **`art_set_slideshow` and
> `art_set_auto_rotation` are therefore interchangeable aliases** — use either on
> any model.

#### `samsungtv_smart.art_set_slideshow`

Configure artwork slideshow.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `duration` | string | Yes | - | Rotation interval |
| `shuffle` | boolean | No | `true` | Randomize order |
| `category_id` | integer | No | `2` | Category (2=Personal, 4=Favorites) |

**Duration Options:** `3min`, `15min`, `1h`, `12h`, `1d`, `7d` — or any integer
number of minutes (e.g. `30`, `180`). Some models reject durations outside a
supported set; rejections are logged rather than failing silently.

```yaml
service: samsungtv_smart.art_set_slideshow
target:
  entity_id: media_player.samsung_frame
data:
  duration: "15min"
  shuffle: true
  category_id: 4
```

#### `samsungtv_smart.art_set_auto_rotation`

Functionally identical to `art_set_slideshow` (alias). Provided for clarity and
backward compatibility; the integration routes it to whichever API the TV
responds to.

**Parameters:** Same as `art_set_slideshow`

```yaml
service: samsungtv_smart.art_set_auto_rotation
target:
  entity_id: media_player.samsung_frame
data:
  duration: "1h"
  shuffle: true
  category_id: 4
```

---

### Favorites Management

#### `samsungtv_smart.art_set_favourite`

Add or remove artwork from favorites.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `content_id` | string | Yes | - | Artwork ID |
| `status` | string | No | `on` | `on` to add, `off` to remove |

```yaml
# Add to favorites
service: samsungtv_smart.art_set_favourite
target:
  entity_id: media_player.samsung_frame
data:
  content_id: SAM-S2701
  status: "on"

# Remove from favorites
service: samsungtv_smart.art_set_favourite
target:
  entity_id: media_player.samsung_frame
data:
  content_id: SAM-S2701
  status: "off"
```

---

### Upload & Delete

#### `samsungtv_smart.art_upload`

Upload an image to the TV as artwork.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | - | Path to image file |
| `matte_id` | string | No | `shadowbox_polar` | Default matte style |
| `file_type` | string | No | `jpg` | File type (`jpg`, `png`) |

```yaml
service: samsungtv_smart.art_upload
target:
  entity_id: media_player.samsung_frame
data:
  file_path: /config/www/my_photo.jpg
  matte_id: modern_black
```

#### `samsungtv_smart.art_delete`

Delete user-uploaded artwork (only `MY-*` content).

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content_id` | string | Yes | Artwork ID (must start with `MY`) |

```yaml
service: samsungtv_smart.art_delete
target:
  entity_id: media_player.samsung_frame
data:
  content_id: MY_F0015
```

---

## Thumbnail Management

### Single Thumbnail

#### `samsungtv_smart.art_get_thumbnail`

Download thumbnail for a specific artwork.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content_id` | string | Yes | Artwork ID |

```yaml
service: samsungtv_smart.art_get_thumbnail
target:
  entity_id: media_player.samsung_frame
data:
  content_id: SAM-S2701
```

### Batch Download

#### `samsungtv_smart.art_get_thumbnails_batch`

Download thumbnails for multiple artworks.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `category_id` | string | No | - | Filter by category |
| `favorites_only` | boolean | No | `false` | Only download favorites |
| `personal_only` | boolean | No | `false` | Only download personal photos |
| `force_download` | boolean | No | `false` | Re-download existing files |
| `cleanup_orphans` | boolean | No | `false` | **Remove local files not in artwork list** |

```yaml
# Download favorites with cleanup
service: samsungtv_smart.art_get_thumbnails_batch
target:
  entity_id: media_player.samsung_frame
data:
  favorites_only: true
  cleanup_orphans: true

# Download personal photos only
service: samsungtv_smart.art_get_thumbnails_batch
target:
  entity_id: media_player.samsung_frame
data:
  personal_only: true

# Force re-download all
service: samsungtv_smart.art_get_thumbnails_batch
target:
  entity_id: media_player.samsung_frame
data:
  force_download: true
```

### Storage Locations

Thumbnails are saved to a **per-TV** directory keyed by the config-entry ID
(since v7), which the integration creates for you:

```
/config/www/frame_art/<entry_id>/
├── current.jpg          # Currently displayed artwork
├── personal/            # User-uploaded images (MY_F*)
│   ├── MY_F0001.jpg
│   └── MY_F0002.jpg
├── store/               # Samsung Art Store (SAM-S*)
│   ├── SAM-S1234567.jpg
│   └── SAM-S7654321.jpg
└── other/               # Other content types
```

**Finding your `<entry_id>`:** open the `sensor.<tv_name>_frame_art` entity
(Developer Tools → States). Its `entry_id` attribute is the value, and
`thumbnail_folder` gives you the ready-to-use `/local/...` base — so you rarely
need to type the path by hand. (It looks like `01KVZ1PYM5B1K2Q1YJWC22K929`.)

**Access URLs** (replace `<entry_id>` with yours):
- Current: `/local/frame_art/<entry_id>/current.jpg`
- Store: `/local/frame_art/<entry_id>/store/SAM-S1234567.jpg`
- Personal: `/local/frame_art/<entry_id>/personal/MY_F0001.jpg`

### Show the current artwork on a dashboard (Local File camera)

A plain Picture card pointed at `current.jpg` will look stuck: browsers cache
the URL, so it won't refresh when the artwork changes. The reliable way to show
what's on the Frame right now is Home Assistant's built-in **Local File**
integration, which wraps the file as a `camera` entity that updates on its own.

**Local File is an integration you add, not a YAML sensor.** Set it up in the UI:

1. **Settings → Devices & Services → Add Integration**, search for **Local File**.
2. Give it a name (e.g. *Frame art current*).
3. For **File path**, enter your per-TV `current.jpg`:

   ```
   /config/www/frame_art/<entry_id>/current.jpg
   ```

   Replace `<entry_id>` with your TV's — see **Finding your `<entry_id>`** under
   [Storage Locations](#storage-locations) above. The full path looks like
   `/config/www/frame_art/01KVZ1PYM5B1K2Q1YJWC22K929/current.jpg`.

That creates a camera entity (e.g. `camera.frame_art_current`, the name is
yours) that always shows the current artwork. Use it in any card that takes a
camera — for example a `picture-glance`:

```yaml
type: picture-glance
title: Currently displayed
camera_image: camera.frame_art_current
camera_view: auto
fit_mode: cover
entities: []
```

> **Notes**
> - The file must exist before the Local File integration will accept the path.
>   Run `art_get_thumbnails_batch` once first (or let your sync automation run),
>   so `current.jpg` is present.
> - `/config/www/...` may need to be in `allowlist_external_dirs` for the Local
>   File integration to read it.

### Folder Sensor Setup

The integration already registers `sensor.<tv_name>_personal`, `…_store` and
`…_other` for each Frame (since v7), so you usually don't need to declare any
folder sensor yourself — point the gallery card's `folder_sensor` at one of
those. Only add a manual one to watch a directory the integration doesn't
manage, and use your per-TV `<entry_id>` path:

```yaml
# configuration.yaml — optional / legacy only
sensor:
  - platform: folder
    folder: /config/www/frame_art/<entry_id>/personal
    filter: "*.jpg"
    scan_interval: 30

  - platform: folder
    folder: /config/www/frame_art/<entry_id>/store
    filter: "*.jpg"
    scan_interval: 30
```

---

## Configuration Examples

### Input Helpers

```yaml
# configuration.yaml
input_number:
  frame_brightness:
    name: Frame TV Brightness
    min: 0
    max: 100
    step: 10
    initial: 50

input_select:
  frame_matte_type:
    name: Frame Matte Type
    # These options are examples — actual options depend on your TV model/firmware.
    # Check select.samsung_*_matte_type for the real list populated from your TV.
    options:
      - none
      - modernthin
      - modern
      - modernwide
      - flexible
      - shadowbox
      - panoramic
      - triptych
      - mix
      - squares
    initial: shadowbox

  frame_matte_color:
    name: Frame Matte Color
    # These options are examples — actual options depend on your TV model/firmware.
    # Check select.samsung_*_matte_color for the real list populated from your TV.
    options:
      - black
      - neutral
      - antique
      - warm
      - polar
      - sand
      - seafoam
      - sage
      - burgandy
      - navy
      - apricot
      - byzantine
      - lavender
      - redorange
      - skyblue
      - turquoise
    initial: polar

  frame_slideshow_duration:
    name: Slideshow Duration
    options:
      - 3min
      - 15min
      - 1h
      - 12h
      - 1d
      - 7d
    initial: 15min

  frame_category:
    name: Frame Category
    options:
      - Personal
      - Favorites
    initial: Favorites
```

---

## Automation Examples

### Sync Favorites Thumbnails

```yaml
alias: "Frame Art: Sync Favorites"
triggers:
  - trigger: time_pattern
    hours: "/6"
actions:
  - action: samsungtv_smart.art_get_thumbnails_batch
    target:
      entity_id: media_player.samsung_frame
    data:
      favorites_only: true
      cleanup_orphans: true
  - delay:
      seconds: 2
  - action: homeassistant.update_entity
    target:
      entity_id: sensor.store
mode: single
```

### Weekend Slideshow

```yaml
alias: "Frame Art: Weekend Slideshow"
triggers:
  - trigger: time
    at: "09:00:00"
conditions:
  - condition: time
    weekday:
      - sat
      - sun
actions:
  - action: samsungtv_smart.art_set_slideshow
    target:
      entity_id: media_player.samsung_frame
    data:
      duration: "15min"
      shuffle: true
      category_id: 4
mode: single
```

### Sync Matte from TV

```yaml
alias: "Frame Art: Sync Matte from TV"
triggers:
  - trigger: state
    entity_id: sensor.samsung_frame_frame_art
    attribute: current_matte_id
actions:
  - variables:
      matte_id: >-
        {{ state_attr('sensor.samsung_frame_frame_art', 'current_matte_id') |
        default('none', true) | lower }}
      matte_type: |
        {% if matte_id in ['none', '', None] or '_' not in matte_id %}
          none
        {% else %}
          {{ matte_id.split('_')[0] | lower }}
        {% endif %}
      matte_color: |
        {% if matte_id in ['none', '', None] or '_' not in matte_id %}
          {{ states('input_select.frame_matte_color') }}
        {% else %}
          {{ matte_id.split('_')[1] | lower }}
        {% endif %}
  - action: input_select.select_option
    target:
      entity_id: input_select.frame_matte_type
    data:
      option: "{{ matte_type | trim }}"
  - action: input_select.select_option
    target:
      entity_id: input_select.frame_matte_color
    data:
      option: "{{ matte_color | trim }}"
mode: queued
max: 5
```

### Adaptive Brightness

```yaml
alias: "Frame Art: Adaptive Brightness"
triggers:
  - trigger: numeric_state
    entity_id: sun.sun
    attribute: elevation
actions:
  - action: samsungtv_smart.art_set_brightness
    target:
      entity_id: media_player.samsung_frame
    data:
      brightness: >
        {% if state_attr('sun.sun', 'elevation') > 30 %}
          80
        {% elif state_attr('sun.sun', 'elevation') > 0 %}
          50
        {% else %}
          20
        {% endif %}
mode: single
```

### Night Mode

```yaml
alias: "Frame Art: Night Mode"
triggers:
  - trigger: time
    at: "22:00:00"
conditions:
  - condition: state
    entity_id: media_player.samsung_frame
    state: "on"
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.samsung_frame_frame_art_mode
  - action: samsungtv_smart.art_set_brightness
    target:
      entity_id: media_player.samsung_frame
    data:
      brightness: 20
mode: single
```

---

## Troubleshooting

### Art Mode Commands Fail

**Symptoms:** "Failed to turn Art Mode ON" warning

**Solutions:**
1. Enable debug logging:
   ```yaml
   logger:
     logs:
       custom_components.samsungtv_smart: debug
       custom_components.samsungtv_smart.api.art: debug
   ```

2. Check for WebSocket reconnection messages in logs
3. The switch now has automatic retry (3 attempts)
4. Restart Home Assistant if issues persist

### Thumbnails Not Downloading

**Symptoms:** Batch download fails or returns 0 bytes

**Solutions:**
1. Verify TV is on and in Art Mode
2. Check network connection
3. Some Art Store content may be DRM-protected
4. Try single thumbnail first:
   ```yaml
   service: samsungtv_smart.art_get_thumbnail
   target:
     entity_id: media_player.samsung_frame
   data:
     content_id: MY_F0001
   ```

### Gallery Not Updating After Removing Favorites

**Issue:** Deleted favorites still appear in gallery

**Solution:** Use `cleanup_orphans` and refresh folder sensor:
```yaml
- action: samsungtv_smart.art_get_thumbnails_batch
  target:
    entity_id: media_player.samsung_frame
  data:
    favorites_only: true
    cleanup_orphans: true
- delay:
    seconds: 2
- action: homeassistant.update_entity
  target:
    entity_id: sensor.store
```

### Matte Case Sensitivity

**Error:** `Invalid option: SHADOWBOX (possible options: shadowbox, ...)`

**Cause:** TV returns uppercase matte IDs

**Solution:** Use `| lower` in templates:
```yaml
matte_id: >-
  {{ state_attr('sensor.samsung_frame_frame_art', 'current_matte_id') | lower }}
```

### OAuth Token Issues

See main [README.md](README.md) for OAuth2 setup and troubleshooting.

---

## See Also

- **[Frame Art Gallery Guide](Frame_Art_Gallery.md)** - Interactive Lovelace galleries
- **[Main README](README.md)** - Integration overview and OAuth2 setup

---

Enjoy your Frame Art! 🎨✨
