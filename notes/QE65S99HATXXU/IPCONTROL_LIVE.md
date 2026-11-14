# QE65S99HATXXU (2026 S99H) — IP Control Live Evidence

Measured on a QE65S99HATXXU (65" S99H, model string `26_RSM_QD_ATA`, firmware
`T-RSMHDEUC-0100-1245.5`) over IP Control on port 1516, in normal viewing with an
HDR source on HDMI 1 (`pictureMode` `MovieHDR`). Same JSON-RPC envelope and
`createAccessToken` pairing as `../QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md`.
Every row is a live call; a method absent here was not tried.

## Method Reference And Live Evidence

| Method | Params / Values | Live Evidence | Notes |
| --- | --- | --- | --- |
| `HDRToneMappingControl` | `HDRToneMapping`: `Active`, `Static` | ✅ getter returned `Active`; `Static` and `Active` accepted, echoed, and read back; `Off`, `On`, `Dynamic` returned `-32002` | The two values of the TV menu. The setter also accepts `0` and `1` and echoes them verbatim, but the getter then reports `Static` and `Active` respectively, so `0` = Static and `1` = Active on this set. Only measured with HDR content playing. |
| `getDeviceInformation` | — | ✅ `modelID`, `serialNumber`, `FWVersion` |  |

The method name is case-sensitive: `hdrToneMappingControl`, `HdrToneMappingControl`
and `toneMappingControl` return `-32601`.
