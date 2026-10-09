# Art app 3.50.106 (EU Frame 2025 firmware)

- Firmware: `T-PTMDDEUC-0090-REL-202605141701` (Tizen 9.0.0, Linux 5.4.249 armv7l)
- Package: `org.tizen.art-app` **3.50.106** (the QN55LS03FAFXZA notes cover 3.50.104)
- Also dumped: `ipcontrol` libraries (`libmde-protocol-ipcontrol.so`, `libmde-service-tvcontrol.so`, …) and
  the websocket side (`msf-server`, `remote-server`, `MultiScreen.dll`). Neither shows a JSON-RPC method or
  REST/WS endpoint missing from `notes/QN55LS03FAFXZA/`; the extra symbol names they contain
  (`streamControl`, `wallBoxStates`, `setOptionControl`, …) are internal functions or SmartThings-thing code.

Read this alongside `notes/QN55LS03FAFXZA/ART_MODE_DECOMPILED.md`; only what is new or corrected is here.
The assemblies were read as IL (`dnfile` + `dncil`); no decompiler output.

## Request set: unchanged

`MobileCommandFactory` maps 47 `request` names, all already documented for 3.50.104. The command classes
`MobileDoneCossCommand`, `MobileSendReadyToUseCommand` and `MobileImageOfListAddedCommand` exist but are
internal (COSS transfer plumbing), not `request` names.

`get_api_version`, `get_brightness`, `get_color_temperature`, `get_auto_rotation_status`,
`set_auto_rotation_status` and `get_thumbnail` are **not** in the map: on this firmware they are answered by
the unknown-request path only. `api_version` answers the constant `5.0.1.0` (the Ambient API version).

## `get_content_list` reads `category_id`

```text
categoryID = JObject["category_id"]?.ToString()
if empty:            recents(flag 0) + MY-C0002 + MY-C0004 + MY-C0001
elif MY-C0008/9:     recents(flag 1)
else:                GetContentList(categoryID)
```

- `category` (what samsungtvws and this integration sent) is ignored.
- The recents list is built from MY-C0009. With flag 0 (unfiltered call) **every recent item is labelled
  `category_id: "MY-C0008"`**; with flag 1 it carries its real category (`AffiliationID`).
- Consequences before the fix: the unfiltered list counted every recently set artwork twice (once as
  MY-C0008), and any category other than MY-C0001/2/4/8 came back empty after client-side filtering —
  which `art_get_thumbnails_batch` with `cleanup_orphans` then treated as "delete every cached thumbnail".
- Categories referenced by the app: `MY-C0001` default artworks, `MY-C0002` My Photos, `MY-C0004`
  Favorites, `MY-C0008` / `MY-C0009` recently set. Store collections pass their own category id through
  `GetContentList`. MY-C0008 is **not** "All".

## `set_artmode_status`: when the TV does not answer

`GetUIStatus()`: `0` off, `1` nav (art-app UI open), anything else on.

| value | UI status | result |
|---|---|---|
| `on` | off | launch `mode=fullscreen` (if `IsPossibleToLaunch`, else error) |
| `on` | nav | launch `mode=fullscreen-forced` (same check) |
| `on` | on | **returns without any reply** |
| `off` | off | **returns without any reply** |
| `off` | nav / on | launch `mode=exit` |
| other | — | error |

So a redundant write is never acknowledged: a client waiting on the request id waits the whole timeout.
That is the "no-op, no response" case the Art Mode switch already short-circuits.

## Response routing

`MobileProtocolManager` stores each incoming `request_id` (synthesised from ticks when absent) and `Send()`
replies only to the client that sent it; a reply whose id is no longer pending is **broadcast** instead.
`SendError` sends `{"event":"error","error_code":"<int as string>","request_data":…,"request_id":…}`.

## `tv_information` (Ambient)

Fields: `lang`, `countrycode`, `modelid`, `firmcode`, `resolution`, `smartTVclient`, `DUID`, `color_sensor`,
`lifeStyleType`, plus a `serif` flag (`Y`/`N`).

## Matte ids and their on-screen names

From `MatteControl` (`matteTypeDic`, `matteColorDic`, `dicMatteTypeSID`, `dicMatteColorSID`):

| matte type id | enum | label (en-US) |
|---|---:|---|
| `none` | 0 | No Mat |
| `modernthin` | 1 | Modern Mat - Thin |
| `modern` | 2 | Modern Mat |
| `modernwide` | 3 | Modern Mat - Wide |
| `flexible` | 4 | Modern Mat - Auto |
| `panoramic` | 5 | Panoramic Mat |
| `triptych` | 6 | Triptych Mat |
| `mix` | 7 | Mixed Mat |
| `squares` | 8 | Squares Mat |
| `shadowbox` | 9 | Shadowbox Mat |

| matte color id | enum | label (en-US) |
|---|---:|---|
| `antique` | 1 | Antique |
| `black` | 2 | Black |
| `burgandy` | 3 | Burgundy |
| `navy` | 4 | Navy |
| `neutral` | 5 | Neutral |
| `polar` | 6 | Polar |
| `sage` | 7 | Sage |
| `sand` | 8 | Sand |
| `seafoam` | 9 | **Slate** |
| `warm` | 10 | Warm |
| `apricot` | 11 | Apricot |
| `byzantine` | 12 | Byzantine |
| `lavender` | 13 | Lavender |
| `redorange` | 14 | **Orange** |
| `skyblue` | 15 | Sky Blue |
| `turquoise` | 16 | Turquoise |

Defaults: `shadowbox_polar` (landscape) and `flexible_polar`.

## Text resources

`bin/<locale>/ArtAppTextResources.resources.dll`: 91 locales × 676 strings (`ArtAppTextResources.Resources.<locale>.resources`).
The entity translations for the matte selects, the motion-timer durations and the Art Mode switch are
generated from them by `tools/art_app_resources/gen_translations.py` (see the README there).
