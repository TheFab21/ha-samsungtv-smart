# Release notes — 9.6.0

If this project is useful to you, you can support its development:

# <a href="https://buymeacoffee.com/thefab21" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-black.png" alt="Buy Me A Coffee" height="41" width="174"></a>

# ⚠️ BREAKING CHANGE — Frame Art pictures have left `www`

> **Read this before you update if you own a Frame.** The pictures the
> integration downloads from the TV (the artwork on screen, the Art Store
> thumbnails and **your own uploaded photos**) are no longer in
> `/config/www/frame_art/`. They are moved automatically, but **anything you
> set up yourself on the old path or the old `/local/frame_art/...` URL stops
> showing the picture**: hand-made cameras, dashboard cards, `folder` sensors,
> automations. The [checklist](#what-you-must-check) below lists each case and
> its replacement.
>
> If you do not own a Frame, nothing changes for you.

## Why

Home Assistant serves everything under `/config/www`, **subfolders included**,
at `/local/...` **without asking anyone to log in**. The only thing it refuses
is listing a folder's contents. A file whose path is known is handed to whoever
asks for it.

The integration wrote its pictures there:
`www/frame_art/<entry_id>/personal/MY_F0045.jpg`, and so on. That path is easy
to guess. The entry id is not secret: it is in the Frame Art sensor's
attributes, in browser history and in screenshots, and the `MY_F` numbers are
sequential. So anyone who could reach your Home Assistant could download your
private photos. That includes Home Assistant Cloud remote access, a reverse
proxy or a tunnel, as well as guests and IoT devices on your network.

Spook 5.12 started flagging it as *"Files anyone can download"* (#472). It was
right. The pictures now live where Home Assistant does not serve them.

## What happens on its own

On the first start of 9.6.0, for each Frame:

- **Files are moved** to
  `/config/.storage/samsungtv_smart/frame_art/<entry_id>/`, keeping the same
  layout: `current.jpg`, `personal/`, `store/`, `other/`. `.storage` is part of
  every Home Assistant backup, as before.
- **The old folders are removed**: `www/frame_art/<entry_id>/`, the gallery's
  resize cache `www/frame_art/.thumb_cache/`, and `www/frame_art/` once it is
  empty. If a second TV has not started yet, its folder is left until it does.
- **Spook's warning goes away by itself.**
- **The integration's own pictures keep working**: the media player in Art
  Mode, `folder-gallery-card` with `folder_sensor:`, the upload card, and the
  attributes listed below. They now go through the integration's endpoint
  `/api/samsungtv_smart/art/...`. It needs either a logged-in request or a
  random per-TV token that changes at every restart, the same way Home
  Assistant protects camera and media player images.

## What you must check

| You set up… | After 9.6.0 | Do this |
|---|---|---|
| A **Local File** camera on `/config/www/frame_art/<entry_id>/current.jpg` | Shows nothing | Use the new **`camera.<tv>_current_artwork`**, or change the camera's file path to `/config/.storage/samsungtv_smart/frame_art/<entry_id>/current.jpg` |
| A **Generic Camera** on `/local/frame_art/<entry_id>/current.jpg` | Shows nothing | Use the new **`camera.<tv>_current_artwork`**. The new URL carries a token that changes at each restart, so it cannot be typed into a Generic Camera |
| A dashboard card (picture, markdown, mushroom…) with a hard-coded `/local/frame_art/...` URL | Broken image | Use an attribute that gives the URL: `current_thumbnail_url` on `sensor.<tv>_frame_art`, or `file_urls` on the folder sensors. A **picture-entity** card on `camera.<tv>_current_artwork` is the simplest option |
| `folder-gallery-card` with only `folder: /local/frame_art/...` | Empty or broken | Set `folder_sensor: sensor.<tv>_personal` (or `_store`, `_other`). The card then uses the sensor's `file_urls` |
| A core **`folder`** sensor (`platform: folder`) on `/config/www/frame_art/...` | Folder gone | Use the integration's own folder sensors, `sensor.<tv>_personal`, `_store` and `_other`. They have the same attributes, plus `file_urls` |
| An automation or script that reads a file path under `/config/www/frame_art/...`, for example an LLM Vision `image_file`, or a notification attachment | File not found | Point it at `/config/.storage/samsungtv_smart/frame_art/<entry_id>/...`. For pictures, prefer the camera: LLM Vision `image_entity: camera.<tv>_current_artwork`, or a notification `image: /api/camera_proxy/camera.<tv>_current_artwork` |
| The **curated selection as a folder of symlinks** recipe from `Frame_Art_Automations.md` | Stops working: its folder is moved and its links point at the old path | Remove its `shell_command`, `folder` sensor and allowlist entries, and switch to the [new version of the recipe](Frame_Art_Automations.md#a-curated-selection). It keeps the list in a template sensor, with nothing on disk and no shell. Re-add your artworks to it |
| A **Folder Watcher** on `/config/www/frame_art/...` | Watches a folder that is gone; its folder cannot be edited | If it only refreshed a gallery, delete it: the integration's folder sensors follow the new folder. Otherwise delete it and add it again under `/config/.storage/samsungtv_smart/frame_art/`, after adding that path to `allowlist_external_dirs`. A repair names it from 9.6.2 |
| `allowlist_external_dirs: /config/www/frame_art` | No longer needed | You can remove it |

**Settings → Repairs helps you find the cameras.** At startup, the integration
looks for Local File and Generic Camera entries that still point at the old
file. If it finds any, it raises a repair that names them and gives both
fixes. The repair clears itself once none is left. It cannot see dashboard
cards or YAML automations, so check those with the table above.

**Finding your `<entry_id>`:** in **Developer tools → Template**, enter
`{{ state_attr('sensor.<tv>_store', 'path') }}`. The id is the folder name
after `frame_art/`.

## New

- **`camera.<tv>_current_artwork`**: the artwork on screen, as a real camera
  entity. Home Assistant's camera proxy serves it, so it works in picture
  cards, in notifications and in HomeKit. It is created for every Frame,
  without any setup.
- **`file_urls`** on the folder sensors: one URL per file, in the order of
  `file_list`. Like `file_list`, it is kept out of the recorder.
- **A repair** for cameras still pointing at the old file (see above).
- **`{{file_path}}` in `folder-gallery-card` actions** is the file's real path
  for the integration's folder sensors. It used to be derived from the URL,
  which has no `/local/` part any more.
- **The Frame Art guides and examples are updated**:
  [Frame_Art.md](Frame_Art.md), [Frame_Art_Gallery.md](Frame_Art_Gallery.md),
  [Frame_Art_Automations.md](Frame_Art_Automations.md) and
  [examples/frame_art](examples/frame_art/). Their template sensors take URLs
  from `file_urls`, so if you copied one, compare it with the new version.

## Under the hood

- **New endpoint** `/api/samsungtv_smart/art/<entry_id>/<file>`:
  - it needs a logged-in request or this run's random token for that TV;
  - the token is compared in constant time;
  - the requested path cannot leave the TV's folder;
  - every refusal answers 404, so a stranger cannot tell which files exist;
  - responses are cached by the browser only (`Cache-Control: private`), and
    URLs change when the picture changes (`&v=<mtime>`).
- **The gallery's resize cache** moved to `.storage` too.
- **`current_thumbnail_url`**, the media player's Art Mode image and
  `art_get_thumbnail`'s `thumbnail_url` now give the new URLs.

## Upgrading

- **From 9.5.x:** update, restart, then go through
  [the checklist](#what-you-must-check). You do not need to reconfigure
  anything in the integration.
- **From 9.6.0b1 or 9.6.0b2:** nothing to do. The files were already moved.
  9.6.0b2 added the camera and the repair.

## Thanks

To @PrestonMcAfee for reporting the Spook warning instead of just clicking
Ignore (#472).
