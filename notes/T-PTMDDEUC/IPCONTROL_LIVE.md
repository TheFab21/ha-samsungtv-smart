# IP Control, from the live T-PTMDDEUC firmware

Source: `libmde-protocol-ipcontrol.so` pulled off a **developer-mode** TV running
Tizen 9.0.0, build `T-PTMDDEUC-0090-REL-202605141701` (ARM 32-bit, stripped).
This is the real implementation behind the `:1516` JSON-RPC server (the "mde" =
Multi-Device Experience framework), so where it disagrees with
`notes/QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md` (a decompile of a different
model), this file is the ground truth for this firmware.

Extraction was read-only (`strings`/`readelf`), via `sdb` as the unprivileged
`sdk` user. The sensor/ambient-light part below also draws on an exhaustive
on-TV vconf sweep (`buxton2ctl list-keys system|memory`, ~4759 keys).

## Server-error codes (the important part)

Every server error carries `message: "Server error"`; the **`data`** string is
the real reason. Verbatim from the library:

```
{"code": -32000, "message": "Server error", "data": "Unknown"}
{"code": -32001, "message": "Server error", "data": "Not supported model"}
{"code": -32002, "message": "Server error", "data": "Failed"}
{"code": -32003, "message": "Server error", "data": "Invalid operation"}
{"code": -32004, "message": "Server error", "data": "No support for batch request"}
{"code": -32010, "message": "Unauthorized access"}
{"code": -32601, "message": "Method not found"}
{"code": -32602, "message": "Invalid params"}
{"code": -32700, "message": "Parse error"}
```

(`-32011` also appears in the code near `-32010` but has no message string; treat
it as another auth-family code, unclassified.)

How the integration classifies them (`api/ipcontrol.py`):

| code | data | meaning | handling |
| --- | --- | --- | --- |
| -32000 | Unknown | generic server error | **ModeLocked** (state, retry) |
| -32001 | Not supported model | model lacks this control | **Unsupported** (latch) |
| -32002 | Failed | refused in current state | ModeLocked |
| -32003 | Invalid operation | op invalid in current state | ModeLocked |
| -32010 | Unauthorized access | token missing/stale | Auth (re-pair) |
| -32601 | Method not found | not available **right now** (also art mode) | Unsupported, **not latched** |
| -32700 | Parse error | malformed; also a stale token in practice | Auth when a token was sent |

**#409:** `directVolumeControl` answers **-32003 "Invalid operation"** when the
TV's sound goes to an external speaker output (eARC/optical) — it has no volume
of its own to report there. Before this decode the integration saw -32003 as a
generic error and fell back to UPnP **every poll forever**; it now maps to
ModeLocked so the external-output learning kicks in and it stops hammering.

## Methods (`*Control`)

```
powerControl muteControl volumeControl directVolumeControl volumeUpDnControl
channelUpDnControl directChannelControl inputSourceControl inputSelectControl
remoteKeyControl artModeControl ambientControl multiviewControl
displayRotatorControl directAccessControl firstScreenAppControl processUPnPControl
soundModeControl speakerSelectControl externalSpeakerControl
pictureModeControl pictureSizeControl pictureSizeIPControl pictureCalibrationModeControl
backlightControl brightnessControl contrastControl sharpnessControl colorControl
tintControl colorToneControl colorSpaceControl colorSpaceGamutControl
contrastEnhancerControl colorBoosterControl digitalCleanViewControl filmModeControl
localDimmingControl autoMotionPlusControl motionLightingControl gammaModeControl
RGBOnlyModeControl WB2PointControl WB20PointModeControl HDRToneMappingControl
peakBrightnessControl autoHDRRemasteringControl brightnessOptimizationControl
applyPictureSettingsControl pixelShiftMenuControl
gameModeControl energySavingSolutionControl autoPowerOffControl autoPowerSavingControl
softAPSSIDControl softAPStatusControl softAPSignalLevelControl softAPWiFiChannelControl
duplicateHTVConfigControl HTVFactoryLockControl HTVRoomStatusControl IsSupportIPControl
```

Getters: `getTVStates getVideoStates getVolume getMute getAmbientMode getCodec
getIdentifier getIPControlState getBasicInfo getDeviceInformation
getPanelResolution getProductCode getProductType getSerialNumber getSwVersion
getAccessToken getDeviceName getDeviceType getBoxStates getCabinetStates
getCabinetGroupIds getHTVInformation getPrivateConfigFlags getSetupId
getThingDeviceId getStringFromSystemInfo`.

## remoteKey

`remoteKeyControl` maps lowercase aliases to Samsung `KEY_*`. Aliases present:

```
power powerOn powerOff cursorUp cursorDn cursorLeft cursorRight enter return exit
menu firstScreen ambient volumeUp volumeDn channelUp channelDn play pause stop
fastforward rewind red green blue yellow caption dash number0..number9
```

`KEY_*` referenced: `KEY_POWER KEY_UP/DOWN/LEFT/RIGHT KEY_OK KEY_RETURN KEY_EXIT
KEY_MENU KEY_HOME KEY_AMBIENT KEY_MULTI_VIEW KEY_VOLUP/VOLDOWN KEY_CHUP/CHDOWN
KEY_MINUS KEY_PLAY/PAUSE/STOP KEY_FF/REWIND KEY_RED/GREEN/YELLOW/BLUE KEY_CAPTION
KEY_0..KEY_9`.

This is the **TV** vocabulary. The HW-Q990D soundbar (#419) uses a different,
UPPER_SNAKE set (`VOL_UP`, `WOOFER_PLUS`, `TONE_CONTROL`…) via its own AV
firmware — confirming the AV path does not share this table.

## Ambient light sensor — not reachable without SmartThings

The library knows the sensor exists: `SystemInfoUtil::IsSupportLightSensor()`,
the feature flag `com.samsung/featureconf/iot_sensor.light_sensor_function_support`,
and `brightnessOptimizationControl` (toggles ambient-light-based auto-brightness).
But there is **no method that reads the illuminance value** over IP Control.

Cross-checked on the TV:

- The only live light value the TV reports (SmartThings "illuminance", ~level 4)
  is **not in any vconf key** — proven by value-diffing full `memory`+`system`
  snapshots (4759 keys) while covering the sensor: nothing tracked the light.
  `memory/ave/powerconsumption`'s `LUXL` field stays fixed (it is a model
  constant, not the sensor).
- `grep illuminance` across the firmware hits only the **kernel driver**
  `system_drv.ko`. The value is read by `sensord` from the driver and bridged to
  SmartThings via OCF — there is no vconf/mobilebff/IP-Control path to it.

Where it *does* live is now pinned exactly. `libmde-protocol-stthing.so`
implements the light sensor as a **SmartThings thing / OCF resource**:
`ThingProtocol::CLightSensorPropertyInfo` (source `CLightSensorPropertyInfo.cpp`)
serves the resource type **`x.com.st.d.sensor.light`** with property
**`brightness.lux`**, via `_GetBrightnessLux` (raw lux, logged as "optional") and
`_GetBrigtnessLevel` (the 0-20 level that SmartThings shows, e.g. "level 4"). It
is produced only through the SmartThings thing stack (`smartthings_payload_s`),
so reading it even locally means speaking the TV's SmartThings/OCF protocol
(which needs ST onboarding) — not something the integration's channels reach.

**Conclusion:** a SmartThings-free ambient-light reading is not available through
any channel the integration uses. We can at most *detect* that a TV has a light
sensor; the value is the ST resource `x.com.st.d.sensor.light` / `brightness.lux`
and comes only via SmartThings.

## SmartThings thing model and the mobilebff backend

- **`libmde-service-tvcontrol.so`** serves the mobilebff channel the integration
  talks to (`/sec/tv/appdata/mobilebff`): `tvcontrol::MobileBFFProvider` /
  `MobileBFFRPCPort`, with `getEdenData`, `GetMediaOutput` / `ChangeMediaOutput`,
  `GetFirstScreenAppList`, `GetMultiviewModeList`, `appendMultiHdmiList`, etc. No
  "confirm" / "may increase" string is in it, which confirms the #404
  on-screen confirmation is a settings-UI gate, not a backend step.
- **`libmde-protocol-stthing.so`** declares the OCF properties the TV exposes to
  SmartThings. Highlights: `x.com.st.d.sensor.light`, `x.com.st.d.sensor.sound`,
  `x.com.samsung.vd.sensor` (presence), `x.com.samsung.tv.paneltime` (the panel
  on-hours, same value as `db/sysman/panel_display_time`), identity
  (`sn`, `duid`, `productcode`, `swversion`, `swmodel`, `year`, `category`),
  `input` / `channel` / `keyvalue` / `keystatus`, `artsupport` / `ambientsupport`
  / `onsupport`, and the `x.com.samsung.vd.camera.*` set. There is **no simple
  power/energy property** here (TV energy rides the SmartThings energy
  capability / `db/smartthing/energy/*`, not a thing property).

## Side finding: reading vconf remotely over mobilebff

The mobilebff channel (`/sec/tv/appdata/mobilebff`) exposes
`request.vconf.info.{int,string,bool}` with `params.key`, which returns any
vconf key's value to a network client (no SmartThings). Example live read of
`memory/ave/powerconsumption` (`.string`) returns the backlight/power model
inputs, whose `APL` (average picture level) and `TEMP` (~68 °C panel
temperature) update continuously — candidates for ST-free diagnostic sensors.
This is the bridge for any future vconf-backed sensor; the ambient light value
above is simply not one of the vconf-backed values.
