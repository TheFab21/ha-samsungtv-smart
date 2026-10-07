# 🖼️ Frame Art Gallery - Lovelace Guide

Create interactive artwork galleries in Home Assistant with click-to-display functionality.

---

## Table of Contents

- [Overview](#overview)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Folder Gallery Card](#folder-gallery-card-recommended)
- [Media Gallery (Upload from HA)](#media-gallery-upload-from-ha)
- [Auto-Entities Gallery](#auto-entities-gallery)
- [Template Sensors](#template-sensors)
- [Advanced Layouts](#advanced-layouts)
- [Customization](#customization)
- [Troubleshooting](#troubleshooting)

---

## Overview

Frame Art Gallery transforms your Home Assistant dashboard into an interactive art gallery:

- 📸 Click thumbnails to display artwork on Frame TV
- ⬆️ Upload your own images directly from HA to the Frame TV
- 🖼️ Upload to a chosen Frame — or to **all** Frames at once — in multi-Frame setups
- 🎨 Separate galleries for personal photos, favorites, and Art Store
- 🔄 Auto-generated from actual TV content
- 📱 Fully responsive layouts
- ⚡ Fast loading with cached thumbnails
- 🖱️ Lightbox preview with smart context actions
- 🧹 Automatic cleanup of orphaned thumbnails

> ### ⚠️ v7 path change — read this first
>
> As of **v7**, thumbnails live in a **per-TV** folder keyed by the config-entry
> ID, not the old flat `/local/frame_art/...` path:
>
> ```
> /local/frame_art/{entry_id}/personal   (was: /local/frame_art/personal)
> /local/frame_art/{entry_id}/store       (was: /local/frame_art/store)
> /local/frame_art/{entry_id}/other       (was: /local/frame_art/other)
> ```
>
> The examples below use the **old flat paths** for readability. When you copy
> them, insert your TV's `{entry_id}` after `frame_art/`. Find it on the
> `sensor.<tv_name>_frame_art` entity (Developer Tools → States): the `entry_id`
> attribute is the value, and `thumbnail_folder` gives you the ready-to-use
> `/local/...` base. The same applies to the `folder:` paths in the `folder`
> sensors below and any `allowlist_external_dirs` entry.

---

## Requirements

### Required Components

1. **SamsungTV Smart integration** with Frame Art support

2. **Downloaded thumbnails** (see [Quick Start](#quick-start))

3. **Folder sensor** for gallery updates — **auto-created in v7**. The
   integration registers `sensor.<tv_name>_personal`, `…_store`, and `…_other`
   for each Frame TV (folder size in MB, with a `file_list` attribute). Point the
   gallery card's `folder_sensor` at the relevant one. You only need to declare a
   manual `folder` sensor if you want to watch a directory the integration
   doesn't manage:
   ```yaml
   # configuration.yaml — optional / legacy only
   sensor:
     - platform: folder
       folder: /config/www/frame_art/YOUR_ENTRY_ID/store
       filter: "*.jpg"
       scan_interval: 30
   ```

4. **Gallery Card** (choose one):

   **Option A: folder-gallery-card** ⭐ **RECOMMENDED**
   - **Bundled with this integration** — no installation needed
   - Registered automatically as a Lovelace resource on startup
   - Faster and cleaner than auto-entities
   - Built-in lightbox with smart context actions

   **Option B: auto-entities**
   - Install via HACS: Search "auto-entities"
   - More flexible but more complex

### Optional But Recommended

- **card-mod** for custom styling
- **Browser Mod** for popups/modals

---

## Quick Start

### Step 1: Create Directory Structure

```bash
mkdir -p /config/www/frame_art/personal
mkdir -p /config/www/frame_art/store
mkdir -p /config/www/frame_art/other
```

### Step 2: Download Thumbnails

```yaml
service: samsungtv_smart.art_get_thumbnails_batch
target:
  entity_id: media_player.samsung_frame
data:
  favorites_only: true
  cleanup_orphans: true
```

**First run:** 2-5 minutes  
**Subsequent runs:** 2-5 seconds (skips existing)

### Step 3: Folder Sensor

**v7 creates these for you automatically.** For every Frame TV, the integration
registers three folder sensors that track each thumbnail subdirectory:

| Entity | Tracks | State | Key attribute |
|---|---|---|---|
| `sensor.<tv_name>_personal` | `…/{entry_id}/personal/` | folder size (MB) | `file_list` |
| `sensor.<tv_name>_store` | `…/{entry_id}/store/` | folder size (MB) | `file_list` |
| `sensor.<tv_name>_other` | `…/{entry_id}/other/` | folder size (MB) | `file_list` |

These expose a `file_list` attribute (plus `path`, `filter`, `number_of_files`,
`bytes`), which is exactly what the gallery card's `folder_sensor` option reads —
so you can point the card straight at them with **no manual configuration**:

```yaml
type: custom:folder-gallery-card
folder_sensor: sensor.mastertv_store
```

(No `folder:` line needed — see [the note below](#basic-configuration) on what
`folder:` is actually for.)

**Legacy / optional:** if you prefer the built-in Home Assistant `folder`
platform (for example to watch a custom directory the integration doesn't
manage), you can still declare one manually — it also exposes `file_list`:

```yaml
sensor:
  - platform: folder
    folder: /config/www/frame_art/YOUR_ENTRY_ID/store
    filter: "*.jpg"
    scan_interval: 30
```

### Step 4: Use the Gallery Card

The `folder-gallery-card` is already available — no installation needed. Jump straight to the [configuration examples](#basic-configuration) below.

---

## Folder Gallery Card (Recommended)

The custom `folder-gallery-card` provides the best experience for Frame Art galleries.

### Installation

**No installation required.** The card is bundled with the SamsungTV Smart integration and registered automatically as a Lovelace resource on startup.

The card is served from `/api/samsungtv_smart/folder-gallery-card.js` — you don't need to copy any files or add any resources manually.

> If for some reason the card doesn't appear after installing the integration, restart Home Assistant once.

### Lightbox Smart Actions

When you click an image and open the lightbox, the card automatically shows context-appropriate action buttons based on the content type:

| Content type | Select | ★ Unfavourite | ⬆ Upload | 🗑 Delete |
|-------------|--------|--------------|---------|---------|
| `SAM-xxxxx` (Art Store favorites) | ✓ | ✓ | | |
| `MY-xxxxx` (personal uploaded) | ✓ | | | ✓ |
| `MY_xxxxx` (personal uploaded) | ✓ | | | ✓ |
| Other (local images not on TV) | | | ✓ | |

- **Select** — display the artwork on the Frame TV immediately
- **Unfavourite** — remove a SAM Art Store artwork from your favorites
- **Upload** — upload a local image file to the Frame TV
- **Delete** — permanently delete a user-uploaded artwork from the TV

### Basic Configuration

```yaml
type: custom:folder-gallery-card
title: Frame TV Favorites
folder_sensor: sensor.store
columns: 4
image_height: 160px
aspect_ratio: "1"
tap_action: lightbox
action:
  service: samsungtv_smart.art_select_image
  target:
    entity_id: media_player.samsung_frame
  data:
    content_id: "{{content_id}}"
```

> **What does `folder:` actually do? (usually: nothing you need to set)**
>
> The **image list** always comes from the `folder_sensor` / `sensor` (a
> `platform: folder` sensor) or from `image_list`. A browser can't list a
> directory from a URL, so `folder:` is *only* the base URL prepended to each
> filename to build the thumbnail `<img>` src — it never produces the list
> itself.
>
> - **Folder sensor under `/config/www/`** (the normal case): the card derives
>   the `/local/...` URL automatically from the sensor's `path`, so you **don't
>   need `folder:` at all**. That's why the examples omit it.
> - **Set `folder:` only when the card can't derive the URL** — files served
>   from outside `/config/www/`, or a custom/template sensor whose `file_list`
>   has bare filenames and no `path`. Give a `/local/...` URL (a
>   `/config/www/...` path is also accepted and mapped to `/local/...`).
> - **`folder:` with no sensor and no `image_list` → empty gallery.** On its own
>   it can't list any files.
>
> The action also accepts the modern syntax — `perform_action:` instead of
> `service:`, or an object-form `tap_action:` — in addition to the legacy
> `action: { service: ... }` shown above.

### Editing in the UI (visual editor)

You don't have to write YAML: add the card from the dashboard and use the
visual editor. Besides the basic fields (title, sensor, folder, columns, image
height) it exposes:

- **Gallery type** — forces the fullscreen-preview buttons for the whole
  gallery: *Personal* (Select + Delete), *Favorites* (Select + Unfavourite),
  *Upload* (Upload), or *Auto* (detect per image).
- **Thumbnails** — a *Server-side thumbnails* checkbox and a *Thumbnail width*
  field.
- **Actions** — dropdowns for **single tap / double tap / long press**
  (*Open preview / Display on TV / Nothing*) plus a *Frame TV entity* field.
  Picking "Display on TV" builds the `samsungtv_smart.art_select_image` action
  for you. Anything more advanced can still be done in the code editor.

### Large folders of original photos

Pointing the card at a folder of full-size originals (several MB each, many of
them) would make the browser download and decode every original just to draw
the grid. By default the card requests small **server-side resized thumbnails**
instead (`server_thumbnails: true`), so only kilobytes per tile are sent; the
full-resolution image is still used for the lightbox and tap/hold actions.

```yaml
type: custom:folder-gallery-card
title: Photos
folder_sensor: sensor.my_photos
columns: 4
thumbnail_width: 500        # sharper tiles; lower for less bandwidth
# server_thumbnails: false  # load full originals instead
```

Thumbnails are cached on disk under `/config/www/frame_art/.thumb_cache/` and
regenerated only when the source file changes (keyed by path + width + mtime).
Requires Pillow (bundled with Home Assistant); if it's unavailable the endpoint
transparently falls back to the original image.

### Switching between folders (folder selector)

When the card has more than one folder, it shows a **dropdown** above the grid
so you can switch between them — no `input_select` + conditional cards needed
(added in 8.11). There are two ways to set it up.

**1. An explicit list of folders** — one `platform: folder` sensor per theme
(the integration auto-creates `sensor.<tv>_personal` / `_store` / `_other`;
add your own for custom themes):

```yaml
type: custom:folder-gallery-card
title: My Art
folders:
  - name: Landscapes
    folder_sensor: sensor.frame_landscapes
  - name: Abstract
    folder_sensor: sensor.frame_abstract
```

Each entry takes the same source keys as the card itself (`folder_sensor`,
`sensor`, `folder`, `image_list`); `name` is the dropdown label.

**2. Auto-detected sub-folders of one sensor** — point a single folder sensor
at a tree and let it recurse (set the sensor's `folder` filter to `**/*`), then:

```yaml
type: custom:folder-gallery-card
title: My Art
folder_sensor: sensor.frame_personal
group_by_subfolder: true
```

The dropdown then lists **All** plus one entry per sub-folder, built from the
files' paths. With neither option set the card shows a single folder and no
dropdown, exactly as before.

### All Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `title` | string | - | Card title |
| `folder_sensor` | string | - | Folder sensor entity ID |
| `folder` | string | *(auto)* | Base **URL** for thumbnails (e.g., `/local/frame_art/store`). Optional — auto-derived from `folder_sensor`'s `path` when under `/config/www/`. Does **not** provide the image list (a sensor or `image_list` does). A `/config/www/...` value is accepted and mapped to `/local/...`. |
| `folders` | list | - | A list of selectable folders, shown as a dropdown. Each entry has a `name` (the label) and the same source keys as the card (`folder_sensor` / `sensor` / `folder` / `image_list`). |
| `group_by_subfolder` | boolean | `false` | With a single (recursive) `folder_sensor`, show a dropdown of its sub-folders (**All** + one per sub-folder) instead of a flat list. |
| `columns` | number | `4` | Number of columns |
| `image_height` | string | `150px` | Image height (ignored if `aspect_ratio` set) |
| `aspect_ratio` | string | - | Aspect ratio (e.g., `1`, `16/9`, `3/4`) |
| `gap` | string | `8px` | Gap between images |
| `border_radius` | string | `8px` | Image border radius |
| `show_filename` | boolean | `true` | Show filename on hover |
| `filter` | string | `*` | File filter pattern |
| `gallery_type` | string | *(auto)* | Forces which action buttons the fullscreen preview shows for the whole gallery: `personal` (Select + Delete), `favorites` (Select + Unfavourite), `upload` (Upload). Omit / `auto` = detect per image from the content-id prefix. |
| `server_thumbnails` | boolean | `true` | Serve small server-resized thumbnails for the grid instead of the full originals (recommended for folders of large photos). The full-resolution image is still used for the lightbox and actions. Set `false` to load originals. |
| `thumbnail_width` | number | `400` | Width (px) of the server-generated thumbnails. Raise for sharper tiles, lower for even less bandwidth. |
| `tap_action` | string/object | - | Action on single tap (e.g. `lightbox`, or a service/perform-action object) |
| `double_tap_action` | object | - | Action on double tap (e.g. select the artwork directly). When set, a single tap is delayed slightly to detect the double tap. |
| `hold_action` | object | - | Action on long press |
| `action` | object | - | Default action (used by lightbox Select button) |

### Tap Action Options

| Value | Description |
|-------|-------------|
| `lightbox` | Open image in fullscreen lightbox with smart action buttons |
| `action` | Execute the configured action directly |
| `more-info` | Show entity more-info dialog |

### Example: tap = preview, double-tap = select, hold = select

```yaml
type: custom:folder-gallery-card
title: Gallery Personal
folder_sensor: sensor.samsung_hacs_personal
columns: 4
aspect_ratio: "1"
tap_action: lightbox          # single tap → open the preview
double_tap_action:            # double tap → display the artwork on the TV
  perform_action: samsungtv_smart.art_select_image
  target:
    entity_id: media_player.samsung_hacs
  data:
    content_id: "{{content_id}}"
hold_action:                  # long press → display the artwork on the TV
  perform_action: samsungtv_smart.art_select_image
  target:
    entity_id: media_player.samsung_hacs
  data:
    content_id: "{{content_id}}"
```

> When `double_tap_action` is set, a single tap is held back briefly (~250 ms)
> to tell the two apart, so `tap_action` fires a touch later than usual.

### Template Variables

Use these in your action data:

| Variable | Description |
|----------|-------------|
| `{{content_id}}` | Artwork content ID (extracted from filename, e.g. `SAM-S1234`, `MY_F0001`) |
| `{{filename}}` | Full filename with extension |
| `{{image_path}}` | Web path to the image (e.g. `/local/frame_art/store/SAM-S1234.jpg`) |
| `{{file_path}}` | Filesystem path to the image (e.g. `/config/www/frame_art/store/SAM-S1234.jpg`) — use this for upload actions |
| `{{name}}` | Artwork name (filename without extension) |
| `{{index}}` | Image index in gallery |

> **Note:** `{{file_path}}` is automatically derived from `{{image_path}}` by replacing `/local/` with `/config/www/`. Use it whenever a service needs the actual file path on disk rather than the web URL.

### Example: Favorites Gallery (Art Store + Personal)

```yaml
type: custom:folder-gallery-card
title: ⭐ Favorites
folder_sensor: sensor.store
folder: /local/frame_art/store
columns: 4
aspect_ratio: "1"
gap: 10px
border_radius: 12px
tap_action: lightbox
action:
  service: samsungtv_smart.art_select_image
  target:
    entity_id: media_player.samsung_frame
  data:
    content_id: "{{content_id}}"
```

### Example: Personal Photos Gallery

First, create folder sensor:

```yaml
sensor:
  - platform: folder
    folder: /config/www/frame_art/personal
    filter: "*.jpg"
    scan_interval: 30
```

Then add card:

```yaml
type: custom:folder-gallery-card
title: 📷 Personal Photos
folder_sensor: sensor.personal
folder: /local/frame_art/personal
columns: 3
aspect_ratio: "4/3"
tap_action: lightbox
action:
  service: samsungtv_smart.art_select_image
  target:
    entity_id: media_player.samsung_frame
  data:
    content_id: "{{content_id}}"
```

### Example: Direct Action (No Lightbox)

```yaml
type: custom:folder-gallery-card
title: Quick Select
folder_sensor: sensor.store
folder: /local/frame_art/store
columns: 5
aspect_ratio: "1"
tap_action: action
action:
  service: samsungtv_smart.art_select_image
  target:
    entity_id: media_player.samsung_frame
  data:
    content_id: "{{content_id}}"
```

---

## Media Gallery (Upload from HA)

This feature lets you browse images stored on your Home Assistant server and upload them directly to the Frame TV from the Lovelace UI — no Samsung app needed.

> **Want to upload a photo straight from your phone instead?** The gallery card
> shows images that are *already on the HA server*. For picking an image on
> whatever device you are holding and pushing it to the Frame in one step, use
> the bundled **`samsung-art-upload-card`** — no folder sensor, no pre-placed
> file. It is documented in the [main README](README.md#entities). The two cards
> complement each other: this one for a curated library, that one for a
> spur-of-the-moment photo.
>
> Both go through the same upload path, so images are normalised for the panel
> the same way (baseline JPEG, fitted to your screen resolution, EXIF rotation
> applied, HEIC accepted) — see
> [What happens to your image](README.md#what-happens-to-your-image).

### How It Works

1. You store your images in `/config/media/<folder>/` (exposed via HA's Media Source)
2. A `folder` sensor watches each subfolder and lists the files
3. The `folder-gallery-card` displays them as thumbnails
4. Clicking an image opens the lightbox — since these images are not yet on the TV, the **⬆ Upload** button appears
5. Clicking Upload calls `samsungtv_smart.art_upload` with the filesystem path of the image

### Step 1: Store Your Images

Place your images under `/config/media/` (on your HA server). You can organize them in subfolders:

```
/config/media/
├── Monet/
│   ├── Monet_1872_Impression_Sunrise.jpg
│   └── Monet_1877_Gare_Saint-Lazare.jpg
├── Van_Gogh/
│   └── Van_Gogh_1889_Starry_Night.jpg
└── ...
```

> On a Synology NAS with Docker, this corresponds to `/volume1/docker/homeassistant/media/`.

### Step 2: Enable Media Directory (if needed)

If HA does not detect `/config/media/` automatically, add to `configuration.yaml`:

```yaml
homeassistant:
  media_dirs:
    local: /config/media
```

Restart HA. The folder will appear under **Media → My media** in the sidebar.

### Step 3: Create Folder Sensors

One sensor per subfolder. With many subfolders, use a dedicated `sensors/` directory:

```yaml
# sensors/gallery.yaml
- platform: folder
  folder: /config/www/media/Monet
  filter: "*.jpg"
- platform: folder
  folder: /config/www/media/Van_Gogh
  filter: "*.jpg"
```

> **Note:** Images must also be accessible via the web server, so copy or symlink them to `/config/www/media/` in addition to `/config/media/`.

For many subfolders, generate sensors automatically with a script — see the [automation section](#automate-with-a-script) below.

### Step 4: Configure the Card

```yaml
type: custom:folder-gallery-card
title: 🎨 Monet
folder_sensor: sensor.monet
folder: /local/media/Monet
columns: 4
aspect_ratio: "1"
tap_action: lightbox
action:
  service: samsungtv_smart.art_upload
  target:
    entity_id: media_player.samsung_frame
  data:
    file_path: "{{file_path}}"
    file_type: jpg
```

**Key points:**
- `folder` uses the `/local/media/` web path so thumbnails display correctly
- `action.data.file_path` uses `{{file_path}}` (not `{{image_path}}`) — this gives the filesystem path `/config/www/media/Monet/filename.jpg` that the upload service needs
- The lightbox will show the **⬆ Upload** button for these images since their filenames don't start with `SAM-` or `MY-`

### Step 5: Upload an Image

1. Click any thumbnail → lightbox opens
2. Click **⬆ Upload** → the image is sent to the Frame TV
3. Once uploaded, the TV assigns a `MY_Fxxxx` content ID — the artwork is now accessible from the Frame TV's personal gallery

### Uploading with Multiple Frame TVs

If you have **more than one Frame TV**, the card no longer uploads blindly to the
`entity_id` in your card config. When you click **⬆ Upload** on a local image, it
opens an *"Upload to which Frame?"* chooser:

- one button per Frame TV (by friendly name), which uploads to just that Frame
- an **⬆ All Frames (N)** button, which uploads to every Frame in turn
- **Cancel**

The chosen Frame overrides the `entity_id` in your `action` for that upload, so a
single card definition serves every Frame — you don't need a separate card per TV.

A few details worth knowing:

- **The chooser only appears for local-photo uploads.** It is shown when the
  action is an upload (service ending in `art_upload`, or any action carrying a
  `file_path`) **and** the gallery points at a local folder — i.e. the `folder`
  path does not end in `store`/`personal` and the image isn't already a
  `SAM-`/`MY_` artwork. Select/favourite/delete actions on TV-resident artwork are
  unaffected and keep using the configured `entity_id`.
- **With exactly one Frame detected, that Frame is used** — no prompt. Note it
  is the *discovered* Frame, not the one named in your card config. Those are
  normally the same TV, but they diverge when a second Frame is switched off at
  the mains or has been removed: discovery only lists TVs currently exposing
  `art_mode_status`, so an unreachable one drops out. Falling back to the
  configured entity in that situation used to send uploads to the very TV
  discovery had just excluded, silently and with no chooser to reveal it.
- **An upload to an unreachable TV fails immediately**, with
  *"&lt;host&gt; is unreachable — cannot upload"*, instead of waiting on a
  power-on that can never complete. A Frame in standby still answers its REST
  endpoint and is woken normally; one that is unplugged or faulty does not.
- **No configuration needed.** Frames are discovered automatically from the
  `art_mode_status` attribute that the integration adds to each Frame's
  `media_player`. Any Frame the integration manages shows up in the chooser.
- **All Frames is sequential.** Each upload is a heavy WebSocket transfer, so the
  card uploads to the Frames one after another rather than in parallel; a toast
  reports progress per TV.

You can keep the `entity_id` in your card's `action.target` — it remains the
fallback for single-Frame installs and is overridden per upload when the chooser
is used.

### Automate with a Script

With many subfolders (e.g. one per painter), generate all sensors and cards automatically:

```bash
# On your NAS/server — generates gallery_sensors.yaml and gallery_cards.yaml
python3 gen_cards.py
```

Example `gen_cards.py`:

```python
import os

MEDIA_DIR = "/volume1/docker/homeassistant/www/media"
OUTPUT_DIR = "/volume1/docker/homeassistant"
TV_ENTITY = "media_player.samsung_frame"

folders = sorted([
    f for f in os.listdir(MEDIA_DIR)
    if os.path.isdir(os.path.join(MEDIA_DIR, f))
    and not f.startswith("@")  # exclude Synology system folders
])

# Sensors (save to sensors/gallery.yaml)
sensors = ""
for folder in folders:
    sensors += f"- platform: folder\n"
    sensors += f"  folder: /config/www/media/{folder}\n"
    sensors += f"  filter: \"*.jpg\"\n"

with open(f"{OUTPUT_DIR}/sensors/gallery.yaml", "w") as f:
    f.write(sensors)

# Cards
cards = ""
for folder in folders:
    safe_name = folder.lower().replace(" ", "_").replace("-", "_")
    cards += f"- type: custom:folder-gallery-card\n"
    cards += f"  title: \"{folder}\"\n"
    cards += f"  folder_sensor: sensor.{safe_name}\n"
    cards += f"  folder: /local/media/{folder}\n"
    cards += f"  columns: 4\n"
    cards += f"  aspect_ratio: \"1\"\n"
    cards += f"  tap_action: lightbox\n"
    cards += f"  action:\n"
    cards += f"    service: samsungtv_smart.art_upload\n"
    cards += f"    target:\n"
    cards += f"      entity_id: {TV_ENTITY}\n"
    cards += f"    data:\n"
    cards += f"      file_path: \"{{{{file_path}}}}\"\n"
    cards += f"      file_type: jpg\n"
    cards += "\n"

with open(f"{OUTPUT_DIR}/gallery_cards.yaml", "w") as f:
    f.write(cards)

print(f"Generated {len(folders)} sensors and cards")
```

In `configuration.yaml`, include the sensors directory:

```yaml
sensor: !include_dir_merge_list sensors
```

Then paste the contents of `gallery_cards.yaml` into your Lovelace dashboard under `cards:`.

---

## Auto-Entities Gallery

Alternative method using `auto-entities` card (more complex but flexible).

### Installation

Install via HACS: Search "auto-entities"

### Personal Photos Gallery

```yaml
type: custom:auto-entities
card:
  type: grid
  columns: 4
  square: true
  title: 📷 My Photos
filter:
  template: |
    {% for img in state_attr('sensor.frame_art_personal_gallery', 'images') or [] %}
      {{
        {
          'type': 'picture',
          'image': img.path,
          'tap_action': {
            'action': 'call-service',
            'service': 'samsungtv_smart.art_select_image',
            'target': {
              'entity_id': 'media_player.samsung_frame'
            },
            'data': {
              'content_id': img.content_id,
              'show': true
            }
          }
        }
      }},
    {% endfor %}
show_empty: true
card_param: cards
```

### Art Store Gallery

```yaml
type: custom:auto-entities
card:
  type: grid
  columns: 4
  square: true
  title: 🎨 Art Store
filter:
  template: |
    {% for img in state_attr('sensor.frame_art_store_gallery', 'images') or [] %}
      {{
        {
          'type': 'picture',
          'image': img.path,
          'tap_action': {
            'action': 'call-service',
            'service': 'samsungtv_smart.art_select_image',
            'target': {
              'entity_id': 'media_player.samsung_frame'
            },
            'data': {
              'content_id': img.content_id
            }
          }
        }
      }},
    {% endfor %}
show_empty: true
card_param: cards
```

---

## Template Sensors

If using auto-entities, create these template sensors:

```yaml
# configuration.yaml
template:
  - sensor:
      # Personal Photos
      - name: "Frame Art Personal Gallery"
        unique_id: frame_art_personal_gallery
        state: >
          {% set list = state_attr('sensor.samsung_frame_art_list', 'content_list') or [] %}
          {% set personal = list | selectattr('category_id', 'eq', 'MY-C0002') | list %}
          {{ personal | length }}
        attributes:
          images: >
            {% set ns = namespace(images=[]) %}
            {% for item in state_attr('sensor.samsung_frame_art_list', 'content_list') or [] %}
              {% if item.category_id == 'MY-C0002' %}
                {% set ns.images = ns.images + [{
                  'path': '/local/frame_art/personal/' ~ item.content_id ~ '.jpg',
                  'filename': item.content_id ~ '.jpg',
                  'name': item.content_id,
                  'content_id': item.content_id
                }] %}
              {% endif %}
            {% endfor %}
            {{ ns.images }}
      
      # Art Store
      - name: "Frame Art Store Gallery"
        unique_id: frame_art_store_gallery
        state: >
          {% set list = state_attr('sensor.samsung_frame_art_list', 'content_list') or [] %}
          {% set store = list | selectattr('content_id', 'match', '^SAM-') | list %}
          {{ store | length }}
        attributes:
          images: >
            {% set ns = namespace(images=[]) %}
            {% for item in state_attr('sensor.samsung_frame_art_list', 'content_list') or [] %}
              {% if item.content_id.startswith('SAM-') %}
                {% set ns.images = ns.images + [{
                  'path': '/local/frame_art/store/' ~ item.content_id ~ '.jpg',
                  'filename': item.content_id ~ '.jpg',
                  'name': item.content_id,
                  'content_id': item.content_id
                }] %}
              {% endif %}
            {% endfor %}
            {{ ns.images }}
      
      # Favorites
      - name: "Frame Art Favorites Gallery"
        unique_id: frame_art_favorites_gallery
        state: >
          {% set list = state_attr('sensor.samsung_frame_art_list', 'content_list') or [] %}
          {% set favorites = list | selectattr('category_id', 'eq', 'MY-C0004') | list %}
          {{ favorites | length }}
        attributes:
          images: >
            {% set ns = namespace(images=[]) %}
            {% for item in state_attr('sensor.samsung_frame_art_list', 'content_list') or [] %}
              {% if item.category_id == 'MY-C0004' %}
                {% set subdir = 'personal' if item.content_id.startswith('MY_F') else ('store' if item.content_id.startswith('SAM-') else 'other') %}
                {% set ns.images = ns.images + [{
                  'path': '/local/frame_art/' ~ subdir ~ '/' ~ item.content_id ~ '.jpg',
                  'filename': item.content_id ~ '.jpg',
                  'name': item.content_id,
                  'content_id': item.content_id,
                  'subdirectory': subdir
                }] %}
              {% endif %}
            {% endfor %}
            {{ ns.images }}
```

---

## Advanced Layouts

### Responsive Columns

Adjust `columns` value for different layouts:

| Columns | Best For |
|---------|----------|
| 2 | Large thumbnails, mobile |
| 3 | Medium thumbnails, tablet |
| 4 | Default, desktop |
| 5-6 | Compact view, many images |

### Non-Square Thumbnails

```yaml
# Portrait (3:4)
aspect_ratio: "3/4"

# Landscape (16:9)
aspect_ratio: "16/9"

# Square
aspect_ratio: "1"

# Use height instead
image_height: 200px
```

### Card Styling with card-mod

```yaml
type: custom:folder-gallery-card
title: Styled Gallery
folder_sensor: sensor.store
folder: /local/frame_art/store
columns: 4
aspect_ratio: "1"
card_mod:
  style: |
    ha-card {
      border: 2px solid var(--primary-color);
      border-radius: 16px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }
    .gallery-item:hover {
      transform: scale(1.05);
      box-shadow: 0 8px 24px rgba(0,0,0,0.3);
    }
```

---

## Customization

### Multiple Galleries Dashboard

```yaml
type: vertical-stack
cards:
  - type: custom:folder-gallery-card
    title: ⭐ Favorites
    folder_sensor: sensor.store
    folder: /local/frame_art/store
    columns: 4
    aspect_ratio: "1"
    tap_action: lightbox
    action:
      service: samsungtv_smart.art_select_image
      target:
        entity_id: media_player.samsung_frame
      data:
        content_id: "{{content_id}}"
  
  - type: custom:folder-gallery-card
    title: 📷 Personal
    folder_sensor: sensor.personal
    folder: /local/frame_art/personal
    columns: 4
    aspect_ratio: "1"
    tap_action: lightbox
    action:
      service: samsungtv_smart.art_select_image
      target:
        entity_id: media_player.samsung_frame
      data:
        content_id: "{{content_id}}"
```

### Gallery with Controls Panel

```yaml
type: vertical-stack
cards:
  # Controls
  - type: horizontal-stack
    cards:
      - type: button
        name: Sync Thumbnails
        icon: mdi:sync
        tap_action:
          action: call-service
          service: samsungtv_smart.art_get_thumbnails_batch
          target:
            entity_id: media_player.samsung_frame
          data:
            favorites_only: true
            cleanup_orphans: true
      
      - type: button
        name: Art Mode
        icon: mdi:image-frame
        tap_action:
          action: call-service
          service: switch.toggle
          target:
            entity_id: switch.samsung_frame_frame_art_mode
  
  # Gallery
  - type: custom:folder-gallery-card
    title: Gallery
    folder_sensor: sensor.store
    folder: /local/frame_art/store
    columns: 4
    aspect_ratio: "1"
    tap_action: lightbox
    action:
      service: samsungtv_smart.art_select_image
      target:
        entity_id: media_player.samsung_frame
      data:
        content_id: "{{content_id}}"
```

---

## Troubleshooting

### Gallery Shows No Images

**Causes:**
1. Thumbnails not downloaded
2. Folder sensor not configured
3. Wrong folder path

**Solutions:**

```yaml
# 1. Download thumbnails
service: samsungtv_smart.art_get_thumbnails_batch
target:
  entity_id: media_player.samsung_frame
data:
  favorites_only: true

# 2. Check folder sensor in Developer Tools > States
# Look for: sensor.store (or your sensor name)
# Verify file_list attribute contains files

# 3. Verify files exist
# Check: /config/www/frame_art/store/
```

### Thumbnails Not Loading

**Causes:**
1. Wrong file paths
2. Files don't exist
3. Permission issues

**Solutions:**

```bash
# Check files exist
ls -lh /config/www/frame_art/store/

# Check permissions
chmod -R 755 /config/www/frame_art/

# Test URL in browser
http://YOUR_HA_IP:8123/local/frame_art/store/SAM-S1234567.jpg
```

### Upload Fails — File Not Found

**Cause:** The `action.data.file_path` is pointing to the wrong path.

**Solution:** Make sure you use `{{file_path}}` (not `{{image_path}}`) in the upload action. `{{file_path}}` automatically maps the web path to the filesystem path:

```
/local/media/Monet/painting.jpg  →  /config/www/media/Monet/painting.jpg
```

Also verify the file actually exists at that filesystem path:

```bash
ls /config/www/media/Monet/
```

### Click Not Working

**Causes:**
1. TV off or not in Art Mode
2. Invalid content_id
3. Wrong entity_id

**Solutions:**

```yaml
# Enable Art Mode first
service: switch.turn_on
target:
  entity_id: switch.samsung_frame_frame_art_mode

# Test with Developer Tools > Services
service: samsungtv_smart.art_select_image
target:
  entity_id: media_player.samsung_frame
data:
  content_id: SAM-S1234567
```

### Gallery Not Updating After Changes

**Issue:** New favorites or deleted items not reflected

**Solution:** Use cleanup_orphans and refresh sensor:

```yaml
# Automation or script
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

### Slow Loading with Many Images

**Issue:** Gallery loads slowly with 100+ images

**Solutions:**

1. Reduce `scan_interval` on folder sensor
2. Use pagination (first 20 images):
   ```yaml
   # With auto-entities
   filter:
     template: |
       {% set images = state_attr('sensor.frame_art_store_gallery', 'images') or [] %}
       {% for img in images[:20] %}
         ...
       {% endfor %}
   ```

3. Optimize thumbnails:
   ```bash
   # Resize all thumbnails
   apt-get install imagemagick
   cd /config/www/frame_art/store
   mogrify -resize 400x400 -quality 85 *.jpg
   ```

---

## Automation: Keep Gallery Synced

```yaml
alias: "Frame Art: Auto Sync Gallery"
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
  - action: persistent_notification.create
    data:
      title: Frame Art
      message: Gallery synchronized
mode: single
```

---

## See Also

- **[Frame Art Guide](Frame_Art.md)** - Complete service documentation
- **[Main README](README.md)** - Integration overview and OAuth2 setup

---

Happy gallery building! 🖼️✨
