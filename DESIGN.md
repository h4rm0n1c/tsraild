# TS Rail Daemon Design

This document captures the intended behavior and integration points for the TS Rail daemon (`tsraild`). It is meant to guide future implementation and usage based on the requirements in `AGENTS.md`.

## Responsibilities and Architecture

- **ClientQuery bridge:** Maintain a resilient connection to the TeamSpeak ClientQuery interface, authenticate using the stored API key, and register for notifications. Track the monitored server handler and channel.
- **State tracking:** Keep an in-memory map of clients in the target channel, including UID, clid, nickname, talking status, and flags (`approved`, `ignored`, `muted_by_us`). Derive overlay-ready user lists with filtering and sorting.
- **Policy enforcement:** Apply policies such as `auto-mute-unknown`, `require-approved`, `ignore_uids`, `target-channel`, and `show-ignored` when updating state and issuing ClientQuery commands.
- **Control socket:** Offer a UNIX control socket for configuration, inspection, and policy changes.
- **HTTP service:** Serve `/state.json` for overlay consumption, plus optional static overlay and asset hosting.
- **Storage:** Read and write configuration and assets in user-specific locations under `~/.config/tsrail/` and `~/.local/share/tsrail/`.

## ClientQuery Adapter

- **Connection model:** Use a single async reader with queued writers to the ClientQuery socket. Reconnect with backoff if the TS client is closed or the socket drops.
- **Authentication:** After connecting, send `auth apikey=<key>` and wait for `error id=0 msg=ok`. Retry on failure and re-read the key file if it changes.
- **Notification setup:** Register for all events with `clientnotifyregister schandlerid=1 event=any` (or the active handler). Handle channel movements, enters/exits, and talk status changes.
- **State updates:**
  - Update the channel roster on `notifycliententerview`, `notifyclientleftview`, and `notifyclientmoved` events.
  - Toggle talking flags on `notifytalkstatuschange` with a short debounce to smooth rapid transitions.
- **Reconnection logic:** Detect disconnections, clear stale state, and resume tracking and notification registration once reconnected.

## Policy Hooks

- **auto-mute-unknown:** For users in the monitored channel who are not approved and not ignored, issue `clientmute` once. Optionally unmute when they become approved.
- **require-approved:** Only approved users are surfaced in `users[]` unless `show-ignored` overrides behavior.
- **ignore_uids:** Users in this list are tracked but never emitted in `users[]`.
- **target-channel:** Limit tracking and policy enforcement to the configured channel ID.
- **show-ignored:** When enabled, may include ignored users in exported state; otherwise they remain hidden.

## Control Socket Protocol

- **Listener:** UNIX socket at `${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/tsrail.sock`, mode `0700`, owned by the user running the daemon.
- **Commands and responses:** Each command returns a single-line status or multi-line payload terminated by EOF. Typical responses are `ok`, `error <msg>`, or structured output as noted.
  - `key-status`: report whether the API key file exists.
  - `setkey <apikey>`: write the key file and trigger re-authentication; respond with success or error.
  - `status`: one-line snapshot including `link_ok`, `auth`, `schandlerid`, `channel_id`, counts, and the `/state.json` URL.
  - `dump-state`: emit the full in-memory state as JSON for debugging.
  - `approve-uid <uid>` / `approve-clid <clid>` / `approve-nick <nickname>`: add to the approved list (persistent).
  - `unapprove-uid <uid>`: remove an approved user.
  - `approved-list`: list all approved entries.
  - `ignore-uid <uid>` / `unignore-uid <uid>` / `ignore-list`: manage the ignore set.
  - `policy <name> <value>`: update runtime policies (`auto-mute-unknown`, `require-approved`, `target-channel`, `show-ignored`).

## Helper Script

For day-to-day control, `scripts/tsrailctl` provides a unified shell wrapper that sends
control socket commands over `socat` using the default socket path. Use it directly
for status checks, API key management, approvals, ignores, and policy changes. It also
includes read-only helpers (`state`, `users`, `unknowns`) that display daemon info and
optionally pretty-print JSON when `jq` is available.

## SysV-Style Service Management

For SysV-style user-space management, `scripts/tsrailctl` includes service commands
that start/stop the daemon in `~/tsraild/`, track a PID file under
`$XDG_RUNTIME_DIR` (or `/run/user/<uid>`), and log to
`~/.local/share/tsrail/tsraild.log`:

- `tsrailctl service-start`
- `tsrailctl service-stop`
- `tsrailctl service-restart`
- `tsrailctl service-status`

## HTTP Interface

- **Endpoints:**
  - `/state.json`: Returns overlay-ready JSON with server info, counts, and `users[]` filtered per policies. Empty `users[]` is valid when no approved users are present.
  - `/overlay/`: Serves the overlay HTML/JS/CSS bundle.
  - `/assets/`: Serves static assets rooted at `~/.local/share/tsrail/assets/`.
- **Behavior:**
  - Lightweight asyncio-based server (e.g., `aiohttp` or custom streams).
  - Caches short-lived responses but always reflects the latest in-memory state.
  - Returns HTTP errors on missing assets, with sensible defaults if configured.

## Asset Layout and Resolution

- **Configuration:**
  - API key: `~/.config/tsrail/clientquery.key`.
  - Persistent config: `~/.config/tsrail/config.json` (approved list, ignore list, policies).
- **Assets:**
  - Base path: `~/.local/share/tsrail/assets/`.
  - Suggested structure:
    - `assets/frames/` — shared monitor frames (idle/talk variants), e.g., `tv_idle.png`, `tv_talk.png`.
    - `assets/users/<uid>/` — per-user avatars, e.g., `avatar.png` (idle), `avatar.gif` or `avatar.apng` (talk).
  - If a talk asset is missing for a user, reuse the idle asset for both states.
- **Overlay defaults:** Provide placeholder assets for users lacking custom files, and ensure transparent backgrounds for compositing in OBS.
