# TQ65S95HFTXXC (2026 S95H QD-OLED) — IP Control Live Evidence

Measured on a TQ65S95HFTXXC (65" S95H, Tizen 10, firmware `T-RSMFDEUC-0090-1301.0`,
model string `25_RSM_QD`) over IP Control on port 1516, in normal viewing with an
SDR source on HDMI 1 and Energy Saving Solution off unless noted. No firmware
decompile for this model: every row below is a live call, so a method that is
absent here was not tried, not shown to be missing. Same JSON-RPC envelope and
`createAccessToken` pairing as `../QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md`.

Not a Frame: no `artModeControl`, and `FrameTVSupport` is `false` in the REST
device info. The picture controls are therefore never gated by Art Mode here,
only by the TV being on.

## Method Reference And Live Evidence

| Method | Params / Values | Live Evidence | Notes |
| --- | --- | --- | --- |
| `pictureModeControl` | `pictureMode`: `Dynamic`, `Standard`, `Eco`, `Movie`, `FilmmakerMode` | ✅ accepted and echoed; `Natural`, `Filmmaker`, `FILMMAKER MODE` returned `-32002` | Getter reports `StandardHDR` while HDR content plays. |
| `backlightControl` | `backlight`: int | ✅ accepted `0..50` | Write refused with `-32002` while `energySavingSolution` is `On` (see below). |
| `brightnessOptimizationControl` | `brightnessOptimization`: `Off`, `On` | ✅ getter; setter `Off` accepted | Distinct from Energy Saving Solution: reading `Off` here while that one was `On`. |
| `energySavingSolutionControl` | `energySavingSolution`: `Off`, `On` | ✅ `Off` and `On` accepted and echoed; `Low`, `Medium`, `High`, `Auto` returned `-32002` | The quick-settings "Energy Saving Solution" tile. Turning it `On` lowered the backlight (35 → 17) and made `backlightControl` writes return `-32002` until it was `Off` again; `Off` restored the previous backlight on its own. |
| `peakBrightnessControl` | `peakBrightness`: `Off`, `Medium`, `High` | ✅ getter returned `Medium`; `Off`, `Medium`, `High` accepted and echoed; `Low` returned `-32002` | Matches the three values of the TV menu. The 2025 Frame answered `-32002` to the getter, so this one is model dependent. |
| `contrastEnhancerControl` | `contrastEnhancer`: `Off`, `Low`, `High` | ✅ accepted and echoed; `Medium` returned `-32002` | Same set as the 2025 Frame. |
| `motionLightingControl` | `motionLighting`: `Off`, `On` | ✅ getter returned `Off` | Setter not tried here. |
| `autoPowerSavingControl` | `autoPowerSaving`: `Off`, `On` | ✅ getter returned `Off` | Setter not tried here. |
| `gameModeControl` | `gameMode` | ✅ getter returned `Off` | Setter not tried here. |
| `autoPowerOffControl` | `autoPowerOff` | ✅ getter returned `4H` | A duration, not `Off`/`On`. Setter not tried here. |
| `colorToneControl` | `colorTone` | ✅ getter returned `Standard` |  |
| `localDimmingControl` | `localDimming` | ❌ getter returned `-32002` | OLED: no local dimming. |
| `getTVStates` | — | ✅ `speakerSelect`, `volume`, `mute`, `pictureSize`, `pictureMode`, `soundMode`, `inputSource` |  |
| `getVideoStates` | — | ✅ `contrast`, `sharpness`, `brightness`, `color`, `tint` |  |

### Methods that returned `-32601` (not implemented on this model, normal viewing)

`ecoSolutionControl`, `ecoModeControl`, `ecoSensorControl`, `energySavingControl`,
`energySavingModeControl`, `brightnessReductionControl`, `minimumBrightnessControl`,
`adaptiveBrightnessControl`, `adaptivePictureControl`, `intelligentModeControl`,
`ambientLightDetectionControl`, `lightSensorControl`, `powerSavingControl`,
`powerSavingModeControl`, `screenSaverControl`, `pictureClarityControl`,
`getAudioStates`, `getDeviceInfo`, `getPictureSettings`, `getEcoSettings`,
`getSupportedMethods`, `listMethods`, `getCapabilities`, `getMethodList`, `help`.
