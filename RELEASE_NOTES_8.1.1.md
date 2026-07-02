# Release notes — 8.1.1 (since 8.1.0)

> **Status: pre-release (beta).**

## Picture mode select — no longer reverts to the old mode after a change

- **Setting the picture mode (e.g. via `select.select_option` or the Picture
  Mode select) no longer snaps back to the previous value.** SmartThings lags
  ~30-45s before it reports the new mode, but the select only skipped polling
  for 5s — so the next poll read the *old* mode from the cloud and reverted the
  selection (issue #116). The select now **holds the mode you set until the
  cloud confirms it** (up to a 60s grace window); if the cloud still hasn't
  confirmed after that, it accepts the cloud's value (so a change made on the TV
  itself is still reflected).
