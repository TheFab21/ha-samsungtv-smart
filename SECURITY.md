# Security policy

## Supported versions

Only the **latest release** is supported. Fixes are not backported: if you
run an older version, update first and check whether the problem is still
there.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub instead: on this repository, open the
**Security** tab and choose **Report a vulnerability**. Only the maintainer
sees the report, and we can discuss and fix it there before anything is made
public.

Useful things to include:

- the integration version and the Home Assistant version;
- what an attacker needs (network access to Home Assistant, to the TV, a
  Home Assistant account, ...) and what they can do;
- steps to reproduce, or a proof of concept.

This project is maintained by one person in their spare time. You will get an
acknowledgement as soon as possible, usually within a few days, and an
estimate of when a fix can ship. Credit is given in the release notes unless
you prefer otherwise.

## Scope

In scope, for example:

- the credentials the integration stores: the TV's local WebSocket token, the
  IP Control access token, SmartThings OAuth tokens;
- the HTTP endpoints the integration registers in Home Assistant (artwork
  upload and thumbnails), and the bundled Lovelace cards;
- anything that lets a Home Assistant user, or someone on the network, do more
  than they should through this integration.

Out of scope:

- vulnerabilities in **Home Assistant Core**, **SmartThings** or the **TV's
  firmware** themselves: please report those to Home Assistant or Samsung;
- the TV's local protocols being unauthenticated or weakly protected by
  design (that is how Samsung built them), unless the integration makes it
  worse;
- Dependabot alerts on the CI and test toolchain (`requirements*.txt`),
  which is not shipped to users.
