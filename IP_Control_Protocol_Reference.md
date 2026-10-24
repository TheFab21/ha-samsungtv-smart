# Samsung IP Control (JSON-RPC) — Protocol Reference

Consolidated reference for the undocumented JSON-RPC IP Control interface on recent
Samsung TVs, synthesised from four sources:

- **Empirical testing** on a Frame 2024 (`QE55LS03D`) and 2025 (`GQ50LS03F`).
- **On-TV method enumeration** run against the QE55LS03D (the source of truth for
  what is *actually* implemented on a consumer Frame — see "Confirmed on Frame
  2024" column below).
- The **`py-samsungtv`** clean-room library (method/param surface — note: this
  documents a broader API than what consumer Frames implement, see notes).
- Samsung's official **Wall PRO IP Command List** (API Wall v1.2), which exposes
  many of the same JSON-RPC method names for LED-wall displays.

**Important model-scope caveat:** Wall PRO (commercial LED panels) and consumer
Frame TVs share the **protocol** (JSON-RPC on port 1516) and method **names**, but
the **set of implemented methods is much smaller on Frame TVs**. Most picture /
sound / input setters documented in py-samsungtv and the Wall PRO PDF do not
exist on a Frame 2024 — they respond with `-32601 "Method not found"`. The table
below marks each method's status on the QE55LS03D.

---

## Transport

- **URL:** `https://<tv_ip>:<port>/` — HTTPS, POST, JSON-RPC 2.0 envelope.
- **Port:** `1516` on **2020 and later** models, `1515` on **2019 and earlier**.
  Samsung moved it once; the RTI and Allonis control drivers document the same
  split, and it was confirmed empirically on a 2018 UE50NU8005 (#206), which
  answers only on 1515. The port opens only when **IP Remote** is enabled on
  the TV. The integration tries 1516 then 1515 at pairing time and stores
  whichever answered.
- **TLS:** self-signed panel certificate → verification disabled
  (`check_hostname = False`, `verify_mode = CERT_NONE`).
- **Headers:** only `Accept: application/json` and `Content-Type: application/json`
  are required. Note: `py-samsungtv` uses aiohttp and lets it add its default
  `User-Agent`/`Accept-Encoding`, and it still works — so the breaking header the
  reporter saw from Postman was a Postman-specific one, not the standard transport
  headers. Either a header-minimal `http.client` request or an aiohttp request with
  the two headers above is fine.
- **Old-TV TLS fallback:** `py-samsungtv` retries with `DEFAULT:@SECLEVEL=1` ciphers
  on a "dh key too small" SSL error — pre-2016 panels negotiate a weak DH group.
  Relevant when testing older Frames: a plain `curl` may fail the TLS handshake and
  need `--ciphers DEFAULT@SECLEVEL=1`.

### Request envelope

```json
{ "jsonrpc": "2.0", "id": 1, "method": "<method>", "params": { "AccessToken": "...", ... } }
```

- `params` is omitted entirely for `createAccessToken`.
- For every other method the `AccessToken` lives inside `params`.
- `id` may be any value (the library increments it per request; a constant works too).

---

## Authentication flow

1. **Pair** — call `createAccessToken` (no token). The TV shows an on-screen
   authorization prompt and returns `{"result": {"AccessToken": "<token>"}}`.
2. **Use** — pass that token in `params.AccessToken` on every subsequent call.

**Confirmed behaviours (Frame 2024):**

- Pairing **only works when the TV is in normal viewing — NOT Art Mode.** In Art
  Mode the endpoint does not respond and the call times out. This is the #1 pairing
  gotcha.
- The token **persists across power cycles**: `powerOn` succeeds against a fully-off
  TV with a stored token and no new prompt, so we pair once and keep the token.
- **TV setting required:** *Settings → All Settings → Connections → Network →
  Expert Settings → Enable IP Remote*. (Menu path may differ on older firmware.)

---

## Error codes

Returned as `{"error": {"code": <int>, "message": "..."}}`:

| Code | Meaning | Suggested handling |
|---|---|---|
| `-32000` | Unknown error | log, surface generic failure |
| `-32001` | Not supported | method/feature absent on this model → disable that capability |
| `-32002` | Failed | transient failure → retry / fall back |
| `-32003` | Invalid operation | bad state for this command — **but see below** |
| `-32010` | Unauthorized | token invalid/expired → trigger re-pairing |

> **`-32003` is not only a state error.** On the 2018 UE50NU8005 of #206 it is
> returned *permanently*, with the message text `"Server error"`, for
> `backlightControl`, `colorToneControl` and `getDeviceInformation` — while
> `getTVStates` and `getVideoStates` answer normally on the same TV, in the same
> state. So on older models it also stands in for "this method is not usable
> here", where a Frame 2024 would answer `-32601 Method not found`. Treat a
> repeated `-32003` on one method as a capability signal, not a transient
> failure. Note also that the code the TV sends and the message it attaches do
> not line up with this table: `-32002` is our `ERROR_SERVER` constant, yet
> `-32003` is the one whose message reads "Server error".

---

## Methods

`AccessToken` is implied in `params` for all methods except `createAccessToken`.

Status column refers to behaviour observed on a Frame 2024 (QE55LS03D, Tizen 9)
when calling each method with only the `AccessToken` (no extra params):

- ✅ **OK** — returns a `result` with the field(s) shown.
- ⚠️ **needs param** — method exists but `-32602 "Invalid params"` without the
  setter-style argument.
- ❌ **not found** — `-32601 "Method not found"` on this model. Documented by
  py-samsungtv / Wall PRO but not implemented on consumer Frames.

| Method | Frame 2024 status | Setter param (when applicable) | Read returns | Notes |
|---|---|---|---|---|
| `createAccessToken` | ✅ (no token needed) | — | `AccessToken` | Pairing. Requires on-screen prompt; TV must be ON and NOT in Art Mode. |
| `getTVStates` | ✅ | — | `speakerSelect, volume, mute, pictureSize, pictureMode, soundMode, inputSource` | **7 fields, NOT the 10 py-samsungtv suggests.** No `power`, no `artMode`, no `atvDtv/airCable/channelNum` on Frame 2024. |
| `getVideoStates` | ✅ | — | `contrast, sharpness, brightness, color, tint` | **5 fields only.** Volume/pictureSize/soundMode/speakerSelect are in `getTVStates`, not here. |
| `powerControl` | ✅ getter/setter | `power` ∈ `powerOn` / `powerOff` / `reboot` | `power` | Read returns `powerOn` in Art Mode (Art Mode = physically on). Read-and-set via the same method. |
| `artModeControl` | ✅ getter/setter | `artMode` ∈ `artModeOn` / `artModeOff` | `artMode` | Case-sensitive: only `artModeControl` (capital M) works. `artmodeControl` / `ArtModeControl` → `-32601`. **Authoritative read-source for Art Mode state.** |
| `muteControl` | ✅ getter/setter | `mute` ∈ `muteOn` / `muteOff` | `mute` | |
| `directAccessControl` | ✅ getter/setter | `applicationName` (+ optional `url`) | `applicationName` | Launches an app. Read returns `"unknown"` when no app launched via this method. On a 2020 QE65Q80TATXZT, setting `applicationName: "webBrowser"` with `url` opens the Samsung browser at the requested URL; a subsequent getter returns `{"applicationName":"webBrowser"}` but does not expose the current URL. |
| `remoteKeyControl` | ⚠️ needs param | `remoteKey` (see list below) | — | Virtual remote key sender. |
| `volumeUpDnControl` | ⚠️ needs param | `control` ∈ `volumeUp` / `volumeDn` | `control` | Relative volume. |
| `channelUpDnControl` | ⚠️ needs param | `control` ∈ `channelUp` / `channelDn` | `control` | Relative channel. |
| `directVolumeControl` | ✅ | `volume` (0–100) | `volume` | Absolute volume. **Measured working on two Frame generations.** A 2023 QE32LS03CBUXXN (`23_KSUE_FTV_T09`, fw `T-KSU2ECDEUC-0090-2230.0`) answered the getter with `{"volume": 11}` in normal viewing, matching its volume sensor; the 2025 QN55LS03FAFXZA decompile declares `volume` int `0..100` and live-accepted the full range; a 2013 UE27F6000 accepted 50/60/80/100 ([#238](https://github.com/TheFab21/ha-samsungtv-smart/pull/238)). The earlier "not implemented on Frame 2024" entry was a `-32601` whose **display mode was not recorded**, and this method sits in the none-ambient dispatch map, which answers `-32601` while art/ambient is on screen — so treat `-32601` here as a mode, not a model. Whether a 2024 panel genuinely differs is still unmeasured. |
| `inputSourceControl` | ❌ | `inputSource` | — | **Not implemented on Frame 2024.** Read-only via `getTVStates.inputSource`. To set input on a Frame: WebSocket `KEY_HDMIx` or SmartThings. |
| `pictureModeControl` | ❌ on Frame 2024 / ✅ on S95H 2026 | `pictureMode` | `pictureMode` | **Model-dependent.** `-32601` on the Frame 2024 (read-only there via `getTVStates.pictureMode`). A 2026 QD-OLED (TQ65S95HFTXXC, Tizen 10) implements getter and setter: `Dynamic`, `Standard`, `Eco`, `Movie`, `FilmmakerMode` all accepted and echoed; `Natural`, `Filmmaker`, `FILMMAKER MODE` refused with `-32002` (unknown name, not a mode gate). The getter reports HDR variants (`StandardHDR`) while HDR content plays. Internal names, the same in every menu language. |
| `pictureSizeControl` | ❌ | `pictureSize` | — | Read-only via `getTVStates.pictureSize`. |
| `soundModeControl` | ❌ | `soundMode` | — | Read-only via `getTVStates.soundMode`. |
| `speakerSelectControl` | ✅ | `speakerSelect` | `speakerSelect` | **Writable** (earlier "read-only" was wrong). Get with no params → capitalized value (`Internal`/`External`), unlike lowercase `getTVStates.speakerSelect`. Set accepts `Internal`, `AudioOut/Optical`; bare `External` is rejected — use `externalSpeakerControl`. |
| `externalSpeakerControl` | ✅ | `deviceName` + `deviceId` | JSON **array** of `{deviceName, deviceId}` | Get with no params lists reachable external audio devices (e.g. `[{"deviceName":"CINEMA 60(HDMI-eARC)","deviceId":"RCV-1"}]`; `{}` when the receiver is off). Set with a listed device switches output to it; unknown/unreachable device → `-32002`. |
| `contrastControl` / `brightnessControl` / `sharpnessControl` / `colorControl` / `tintControl` | ✅ | `<field>`: int | `<field>` | **Writable** (earlier "read-only" was wrong). Get with no params; set with `{"<field>": n}`. Ranges (Frame 2024/2025): contrast 0–50, color 0–50, sharpness 0–20, brightness −5…5, tint −15…15. Write is picture-mode gated → `-32002` in Dynamic/HDR-dynamic; Standard/Movie/Filmmaker accept it. |
| `directChannelControl` | ✅ getter | `atvDtv` + `airCable` + `channelNum` | `atvDtv`, `airCable`, `channelNum` | Earlier "not implemented on Frame, Frames have no tuner" was **wrong on both counts**. The getter answered on a 2025 Frame (QN55LS03FAFXZA): `{"airCable":"cable","atvDtv":"tvplus","channelNum":"1001"}`; a set attempt (DTV/AIR/1) returned `-32002`. The decompile puts it in the **none-ambient** dispatch map, and that is confirmed on a Frame: with `pictureMode: "Ambient"` the same getter answered `{"atvDtv":"tvplus","airCable":"air","channelNum":"4747"}` once and `-32601` seconds later. So `-32601` is **not** a model-capability verdict here — note also that a Frame in art mode still reports `inputSource: "TV"`, so the tuner looks active while no channel is displayed. |
| `USBSourceControl` / `RVUSourceControl` / `ambientModeControl` | ❌ | — | — | Not implemented on Frame 2024. May exist on other models. |

### Methods used by this integration but absent from the enumeration above

Added here because the integration calls them and their behaviour differs by
model generation:

| Method | Setter param | Read returns | Notes |
|---|---|---|---|
| `backlightControl` | `backlight`: int (0–50) | `backlight` | Panel backlight. `-32003` on the 2018 set of #206. |
| `colorToneControl` | `colorTone` | `colorTone` | White balance preset. `-32003` on the same TV. |
| `getDeviceInformation` | — | `modelID`, `FWVersion` | Read once after pairing to gate model-dependent features. `-32003` on the same TV, so those installs keep their last known model/firmware. |

---

## Pre-2020 models (2019 and earlier)

Samsung publishes no method list for consumer TVs of any generation — its own
IP command lists cover the commercial Wall PRO / Wall LUX panels only, and the
integrator drivers (RTI, Crestron, AMX, Allonis) document the port, the TV
settings and a variable list, but never the JSON-RPC methods.

There is, however, **one method-level source for this generation**, and it is
already in this repository: `Savant_MU6070_IP_Profile_Catalogue.md`, extracted
from a shipping Savant driver for a **2017 MU6070**. Read it alongside this
section — but note what it does *not* cover:

| the 2017 profile documents | it says nothing about |
|---|---|
| `createAccessToken`, `powerControl`, `muteControl`, `directVolumeControl`, `volumeUpDnControl`, `channelUpDnControl`, `inputSourceControl`, `directAccessControl`, `remoteKeyControl` | every picture setting — no `contrastControl`, `colorControl`, `tintControl`, `backlightControl`, `colorToneControl`, and no `getVideoStates` at all |

So it cannot settle whether Color and Tint are readable on a pre-2020 set: a
control driver choosing not to expose picture calibration is not evidence that
the TV lacks it.

What it *does* establish is that **the method set diverges in both directions**.
`directVolumeControl` and `inputSourceControl`
(`HDMI1`–`HDMI4`, `TV`, `AV1`, `AV2`, `COMPONENT1`, `USB`) both work on the 2017
TV. Absence on a Frame would therefore say nothing about an older model — the
mistake that kept pre-2020 TVs unsupported here for so long.

> **The 0–50 volume range is Savant's, not Samsung's.** The catalogue lists a
> *profile range* of 0–50 for `directVolumeControl` — the bounds that control
> driver declares for its own slider. Every measurement we have says the
> protocol takes **0–100**: the QN55LS03FAFXZA firmware declares `volume` int
> `0..100` and accepted the full range live, and a 2013 UE27F6000 accepted
> 50/60/80/100. Do not clamp to 50.

> **No longer unused.** `inputSourceControl` was wired up in
> [#228](https://github.com/TheFab21/ha-samsungtv-smart/pull/228), and
> `directVolumeControl` in
> [#238](https://github.com/TheFab21/ha-samsungtv-smart/pull/238) — the latter
> probes the capability per TV rather than assuming it from the model, and does
> not latch "unsupported" on a `-32601` raised while ambient mode is active.

The measurements below come from the single 2018 TV in
[#206](https://github.com/TheFab21/ha-samsungtv-smart/issues/206) — one data
point, not a specification.

**UE50NU8005 (2018, Tizen ~4.0)**

| | |
|---|---|
| Port | **1515** — nothing answers on 1516 |
| TLS | Negotiates a **weak DH group**; the handshake fails at OpenSSL's default security level and needs the `@SECLEVEL=0` retry. Both this and the port had to be right for pairing to succeed. |
| `createAccessToken` | ✅ — same flow, same on-screen prompt |
| `getTVStates` | ✅ |
| `getVideoStates` | ✅ — **all five fields**, values as numeric strings. `color` and `tint` are dropped from the reply on inputs classified as PC; see below. |
| `backlightControl` | ❌ `-32003` |
| `colorToneControl` | ❌ `-32003` |
| `getDeviceInformation` | ❌ `-32003` |

**Answered: Color and Tint work — but not on inputs classified as PC.** Two
readings from the same 2018 TV, two minutes apart:

```text
18:42:15  {'brightness': '0', 'color': '20', 'contrast': '50', 'sharpness': '0', 'tint': '0'}
          (absent: nothing | present but not an integer: nothing)

18:44:10  {'brightness': '0', 'contrast': '50', 'sharpness': '0'}
          (absent: color, tint | present but not an integer: nothing)
```

So the fields are **conditional, not missing**. The condition was established by
direct test: with a games console on HDMI, Color and Tint are present in every
picture mode; switching to an HDMI input whose connected device is classified as
**PC**, both vanish. The TV switches to a PC/graphics pipeline there — it also
cuts the picture mode list down (four modes on the console input, two on PC) and
stops reporting a current picture mode at all.

Note the reading order in the log above is misleading on its own: the fields
disappear at 18:44:10 while the input is only *reported* as PC at 18:44:46. The
input in that log comes from SmartThings, whose lag is the subject of the same
report — the panel had already switched.

Practical consequence: those two sliders legitimately come and go with the input,
not with anything the integration does. Re-classifying the input on the TV
(Source → device type → anything other than PC) restores them.

> **Three hypotheses this disproved**, recorded so they are not re-tried:
>
> 1. *"The TV omits the fields on this generation."* It does not — the first
>    reading has all five.
> 2. *"Tint comes back as an `R15`/`G15` token that our integer entity cannot
>    parse."* It does not, on this TV: tint reads `'0'`. Values arrive as
>    **strings**, but they parse as integers. The token format documented by RTI
>    for 2024/2025 panels did not appear here.
> 3. *"Reads are picture-mode gated, like writes are."* They are not. Cycling
>    picture modes on a non-PC input changes nothing; the input classification
>    is what matters.

Note that `-32003` is *not* how this surfaces: the fields simply vanish from an
otherwise successful `getVideoStates` reply.

**Scales still unverified.** Samsung's own Wall PRO / Wall LUX command lists give
`colorControl` 0–100, `tintControl` −50…50 and contrast/brightness/sharpness
0–100, against the Frame-2024-measured 0–50 / −5…5 / 0–20 / 0–50 / −15…15
hardcoded in `IP_CONTROL_PICTURE_SETTINGS`. The 2018 readings above
(contrast 50, color 20, tint 0, brightness 0, sharpness 0) sit inside both, so
they discriminate nothing. Those lists also cover commercial LED panels, so they
are not proof for a consumer set.

What is separately measured: `backlightControl` and `colorToneControl` are
distinct methods this TV rejects outright with `-32003`, so Backlight and Colour
Tone stay permanently unavailable on it — unlike Color and Tint, which are only
intermittently so.

### Input classification changes what the TV reports

Beyond `getVideoStates` dropping `color`/`tint`, an HDMI input classified as
**PC** on a pre-2020 set also:

- restricts the picture mode list (four modes on a console input, two on PC), and
- reports **no current picture mode at all**, which reaches Home Assistant as
  unknown.

None of this is signalled as an error; the fields simply stop being there. Any
capability probe on this protocol therefore has to account for the active input,
not just the model and the picture mode.

> **Unused local value:** `getTVStates.inputSource` is read every few seconds
> over IP Control and is already surfaced as the *Input Source* diagnostic
> sensor. `media_player.source` still comes from SmartThings, which lags by tens
> of seconds and misses input changes made with the TV's own remote (#206). The
> faster value is therefore already in hand for any paired TV; switching the
> media player to prefer it is unexplored.

### Picture mode ids are not standard on this generation

The same log shows this TV reporting **`modeEntertainment`** as the id for its
"Dynamisk" (Dynamic) mode, not the `modeDynamic` seen on Frames:

```text
Picture mode 'Dynamisk' sent via custom.picturemode (id form: modeEntertainment)
Picture mode 'Dynamisk' also sent via WS key KEY_DYNAMIC
```

So a lookup table keyed on the four canonical ids is not sufficient. The
remote-key fallback resolves the *display name* by stem (`dynam` → `KEY_DYNAMIC`)
precisely for cases like this, and it is what worked here.

### RemoteKey values (`remoteKeyControl`)

`power`, `cursorUp`, `cursorDn`, `cursorLeft`, `cursorRight`, `menu`, `firstScreen`,
`enter`, `fastforward`, `rewind`, `play`, `stop`, `pause`, `return`, `exit`,
`number0`–`number9`, `caption`, `dash`, `red`, `green`, `yellow`, `blue`, `ambient`.

(Per py-samsungtv enums — exact validity per key TBD on Frame 2024.)

---

## Why this matters for the integration

Several methods map directly onto current pain points:

- **`powerControl`** — the explicit power-off (works from Art Mode) that the WebSocket
  `KEY_POWER` toggle can't deliver. Solves the no-SmartThings power-off issue.
  *Phase 1, shipped.*
- **`artModeControl`** — explicit `artModeOn`/`artModeOff` setter AND **authoritative
  read source** (call without `artMode` param → returns `{artMode: "artModeOn"}`).
  This is the **clean fix for the stale art-mode-after-power-off bug** on the Power
  switch / Frame Art Mode switch — instead of inferring art mode from `device_info`'s
  `PowerState`, we can query the TV directly. Also lets us implement a deterministic
  "wake to normal viewing" via `powerOn` → `artModeOff`.
- **`getTVStates` / `getVideoStates`** — read-only snapshots of input source, mute,
  picture/sound modes and levels, on Frame 2024. Useful for sensor entities or as a
  cross-check; **not** the source for `power` or `artMode` state (those fields are
  absent — use `powerControl`/`artModeControl` instead).
- **`muteControl`** — the only mute setter that works via IP Control on Frame 2024.

### What we cannot do via IP Control on Frame 2024 (per empirical enumeration)

- Set input source (`inputSourceControl` → not found). Use WS `KEY_HDMIx` or
  SmartThings.
- Set picture mode / picture size / sound mode / speaker / picture levels — all
  `-32601`. Read-only via `getTVStates`/`getVideoStates`. Use SmartThings for setting.
  Picture mode is the exception on newer sets: see the `pictureModeControl` row.
- ~~Set absolute volume (`directVolumeControl` → not found).~~ **Retracted**: the
  2025 Frame firmware declares and accepts `0..100`, and the original `-32601`
  has no recorded display mode. See the `directVolumeControl` row above.

So IP Control on a consumer Frame is essentially a **power + art-mode + virtual
remote + app launcher + readout** API. Not a full replacement for SmartThings or
WebSocket — but exactly what we needed for the painful gaps (power off / art mode
state).

### Phasing

1. **Phase 1 (shipped):** `powerControl` on/off behind opt-in pairing.
2. **Phase 2 (next):** `artModeControl` (read) as the authoritative art-mode source —
   replaces the `device_info.PowerState='standby'` workaround we shipped in the
   media_player `art_mode_status` calc. Plus `artModeControl` (write) for
   deterministic art-mode toggling.
3. **Phase 3:** `remoteKeyControl` virtual remote as a WS-port fallback should
   Samsung ever disable 8001/8002. Volume up/down too if relevant.

Each phase is independently shippable and degrades gracefully (no token → current
behaviour unchanged).
