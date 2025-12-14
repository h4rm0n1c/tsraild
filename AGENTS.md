# OVERLAY.md — TS Rail Overlay (Browser / OBS)

The overlay is a browser-based UI that renders TS Rail state into a visual rail of avatars and talking indicators.

---

## Quickstart

1. Ensure tsraild is running and `status` reports `link_ok=1 auth=1`.

    SOCK="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/tsrail.sock"
    printf 'status\n' | socat - UNIX-CONNECT:$SOCK

2. Check `/state.json`:

    curl -s http://127.0.0.1:17891/state.json | jq .

   Confirm that `users[]` has entries for approved users.

3. Create an OBS Browser Source:
   - URL: `http://127.0.0.1:17891/overlay/`
   - Size: e.g. 400×1080 for a left-hand vertical rail.
   - Enable transparency (for transparent backgrounds).

4. Place avatar and frame assets into `assets/` as per DESIGN.md.
5. Talk on TS and confirm:
   - Your avatar “hops and illuminates” when speaking.
   - Returns to idle state when silent.

---

## 1. Purpose

The overlay:

- Shows a vertical list of approved users currently in the monitored TS channel.
- Displays per-user avatars and monitor frames.
- Animates on speaking using CSS class toggles.
- Stays visually consistent with cyberpunk/tech themes while remaining legible.

The overlay is thin: all authoritative state comes from `state.json`.

---

## 2. `/state.json` Contract

### 2.1 Expected structure

The overlay expects a JSON document of the form:

    {
      "ts": 1734160000.123,
      "server": {
        "schandlerid": 1,
        "channel_id": 7,
        "channel_name": "OBS Audio"
      },
      "counts": {
        "approved_total": 7,
        "present_approved": 2,
        "present_unknown": 1,
        "present_ignored": 1
      },
      "users": [
        {
          "uid": "uid1",
          "nickname": "Glytcho",
          "talking": true,
          "approved": true,
          "ignored": false,
          "assets": {
            "avatar_idle": "assets/users/uid1/avatar.png",
            "avatar_talk": "assets/users/uid1/avatar.gif",
            "frame_idle": "assets/frames/monitor_idle.png",
            "frame_talk": "assets/frames/monitor_talk.apng"
          }
        }
      ]
    }

### 2.2 Sorting and filtering

- `users[]` is filtered by the daemon to contain:
  - Only approved users.
  - Only non-ignored users.
  - Only users present in the monitored channel.
- Overlay may assume `users[]` is pre-sorted by nickname, but can re-sort if needed.

---

## 3. Rendering Model

### 3.1 Core layout

DOM structure:

- Root container:

    <div id="tsrail-rail"></div>

- For each user:

    <div class="tsrail-user" data-uid="uid1">
      <div class="tsrail-frame">
        <img class="frame frame-idle" src="assets/frames/monitor_idle.png" />
        <img class="frame frame-talk" src="assets/frames/monitor_talk.apng" />
      </div>
      <div class="tsrail-avatar">
        <img class="avatar avatar-idle" src="assets/users/uid1/avatar.png" />
        <img class="avatar avatar-talk" src="assets/users/uid1/avatar.gif" />
      </div>
      <div class="tsrail-nickname">Glytcho</div>
    </div>

Rules:

- Both idle and talk variants are always present in the DOM.
- Visibility is controlled via CSS class toggles.
- If a talk variant is missing (`null` in JSON), overlay:
  - May omit that `<img>`, or
  - Reuse the idle asset as talk.

### 3.2 CSS layout

Example layout (to be adapted in real CSS file):

- `#tsrail-rail`:
  - Positioned at left edge.
  - Vertical flex column.
  - Transparent background.

- `.tsrail-user`:
  - Relative positioning.
  - Margin between entries.
  - Transition for transform and brightness.

---

## 4. Talk / Idle Behaviour

### 4.1 Mapping state to classes

On each state update:

- For each user in `users[]`:
  - Ensure a `.tsrail-user` element exists.
  - Apply class:
    - `.talking` if `talking == true`.
    - `.idle` if `talking == false`.

### 4.2 Suggested CSS behaviour

Idle vs talking (example):

- Visibility:

    .tsrail-user.idle .frame-talk,
    .tsrail-user.idle .avatar-talk {
      opacity: 0;
    }

    .tsrail-user.idle .frame-idle,
    .tsrail-user.idle .avatar-idle {
      opacity: 1;
    }

    .tsrail-user.talking .frame-talk,
    .tsrail-user.talking .avatar-talk {
      opacity: 1;
    }

    .tsrail-user.talking .frame-idle,
    .tsrail-user.talking .avatar-idle {
      opacity: 0;
    }

- Hop and illuminate:

    .tsrail-user.talking {
      transform: translateY(-4px);
      filter: brightness(1.2);
    }

    .tsrail-user.idle {
      transform: translateY(0);
      filter: brightness(0.9);
    }

These are guidelines; actual values should be tuned for legibility and aesthetic.

---

## 5. Asset Handling

### 5.1 Paths and origins

- Overlay page (`/overlay/`) and `state.json` share the same origin:
  - `http://127.0.0.1:17891/`
- All asset paths in `assets` objects are relative to this origin, e.g.:
  - `assets/users/uid1/avatar.png`
  - `assets/frames/monitor_idle.png`

Overlay must not assume any hardcoded path; it uses the paths provided in JSON.

### 5.2 Placeholders

If `state.json` provides `null` or missing paths:

- Overlay may fall back to default placeholders, e.g.:

    assets/placeholder/avatar.png
    assets/placeholder/frame_idle.png
    assets/placeholder/frame_talk.apng

Exact fallback resolution is primarily handled by the daemon; overlay only needs to:
- Use the provided path directly.
- Optionally define a final fallback for broken URLs.

---

## 6. Polling and Updates

### 6.1 Polling loop

Standard polling loop:

- Poll `state.json` every 200–500 ms (2–5 Hz).
- Use `Cache-Control: no-store` / `fetch(..., { cache: "no-store" })` to avoid caching.
- Compare `ts` (timestamp) with the previous value and only re-render when changed.

Pseudo-flow:

- Maintain `lastTs` (initially 0).
- On each fetch:
  - Parse JSON.
  - If `state.ts != lastTs`:
    - `lastTs = state.ts`.
    - Call `renderState(state)`.

### 6.2 Render strategy

Simplest:

- Clear `#tsrail-rail` contents.
- Rebuild `tsrail-user` elements from scratch each update.

More advanced (optional):

- Maintain map `uid -> DOM node`.
- On update:
  - Add nodes for new users.
  - Update `talking` class for existing nodes.
  - Remove nodes for users no longer present.
  - Reorder nodes to match sorted nickname order.

Given typical small user counts, full rebuild is acceptable.

---

## 7. Styling and Theming

### 7.1 Overall aesthetic

- Vertical rail on left-hand side of stream.
- Transparent background.
- Cyberpunk / tech vibe:
  - Neon accent edges.
  - Soft glow for speaking users.
  - Slight dimming for idle.

### 7.2 Nicknames

- Show `nickname` under each monitor.
- Styling example:

    .tsrail-nickname {
      position: relative;
      margin-top: 3.5rem;
      text-align: center;
      font-size: 0.8rem;
      color: #ffffff;
      text-shadow: 0 0 4px rgba(0, 0, 0, 0.8);
    }

- Truncate or wrap as needed:
  - Optional: `text-overflow: ellipsis;` and `max-width` constraints.

---

## 8. “Don’t Show Me but Still Count Me”

The daemon enforces the “don’t show me” rule:

- Ignored UIDs are not included in `users[]`.
- They may still contribute to `present_ignored` in `counts`.

Overlay responsibilities:

- Simply render `users[]` as-is.
- Optionally show aggregate counts (e.g., total present) if desired.

---

## 9. OBS Integration Notes

### 9.1 Browser Source config

- URL: `http://127.0.0.1:17891/overlay/`
- Dimensions: choose height to cover expected user count.
- Options:
  - Enable transparent background.
  - Optionally disable “Shutdown source when not visible” if you want continuous polling.

### 9.2 Multiple monitors / layouts

- For more complex layouts:
  - Duplicate Browser Source with different CSS overrides.
  - Use scene collections to show/hide rails depending on stream content.

---

## 10. Error / Offline States

Overlay should handle:

- `state.json` fetch failure:
  - Show “offline” indicator or hide rail.
- `link_ok=0` or `auth=0` in JSON:
  - Show a small “TS disconnected” or “TS auth failed” badge.
- Empty `users[]`:
  - Show a placeholder, or render nothing.

Example small indicator text:

- “TS Rail: Disconnected”
- “TS Rail: No approved users present”

---

## 11. Future Enhancements

- Switch from polling to Server-Sent Events or WebSocket for lower latency.
- Add “recently active” state (third status between idle and active).
- Per-user color accents matching avatar/identity.
- Hover tooltips for debug info (UID, mute status).
- Animated entry/exit transitions when users join/leave.

Summary

    The repository currently only contains the overlay design guidance in AGENTS.md; it specifies the expected /state.json payload and overlay layout but there is no accompanying design/implementation to produce that endpoint or the browser overlay referenced (e.g., /overlay/, asset directories).

Issues

    Missing implementation and documentation for serving the /state.json contract and overlay UI described in the design notes; the repo lacks code, HTML/CSS/JS, or assets to fulfill the documented contract and entry points (e.g., http://127.0.0.1:17891/overlay/).

    There is no long-running daemon process that:
      - Connects to the TeamSpeak 3 ClientQuery interface.
      - Tracks channel membership, talking state, and approvals/ignores.
      - Exposes that state as the /state.json API consumed by the overlay.

    There is no control plane to:
      - Store and manage the ClientQuery API key.
      - Maintain an approved user list keyed by UID.
      - Maintain an ignore list (“don’t show me but still count me”).
      - Toggle policies like auto-mute for unknown users.

    There is no documented asset layout for:
      - Per-user avatar images (idle/talking variants).
      - Per-user or shared monitor frame images (idle/talking variants).
      - Placeholder assets when user-specific art is missing.

    There is no integration story with Devuan/runit:
      - No guidance or scripts for running the daemon as a supervised user service.
      - No clear logging strategy or runtime directory structure.

Proposed Solution

    1. Introduce a daemon component (tsraild.py) that:
       - Connects to the local TeamSpeak 3 ClientQuery interface (127.0.0.1:25639).
       - Implements a single-reader async adapter for ClientQuery, with:
         - A dedicated readline loop.
         - A response queue for command/response pairing.
         - A notification callback for notify* events.
       - Handles authentication via API key (auth apikey=...).
       - Tracks:
         - Current server connection handler (schandlerid).
         - Current channel ID and name.
         - Present clients (UID, clid, nickname).
         - Talking flags with debounce logic.

    2. Implement a UNIX control socket protocol:
       - Socket path: ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/tsrail.sock
       - Simple line-based commands for:
         - API key management (key-status, setkey).
         - Approvals and ignores (approve-uid, approve-nick, ignore-uid, etc.).
         - Policy toggles (auto-mute-unknown, require-approved, target-channel).
         - Diagnostics (status, dump-state).
       - All commands respond with single-line OK/ERR, plus optional data.

    3. Provide an HTTP microservice:
       - Bind to 127.0.0.1:17891.
       - Serve:
         - GET /state.json : current overlay state in the agreed contract format.
         - GET /overlay/ : a small HTML/JS/CSS app that renders the TS rail.
         - GET /assets/... : static assets (avatars, frames, placeholders).

    4. Define and document the asset hierarchy:
       - Under ~/.local/share/tsrail/:
         - assets/placeholder/ : default avatar and frame art.
         - assets/frames/ : shared monitor frames (idle/talk).
         - assets/users/<uid>/ : per-user avatar and frame variants.
         - overlay/ : index.html, overlay.js, overlay.css.
       - Document resolution rules so overlay code can treat asset paths as opaque.

    5. Write separate design docs:
       - DESIGN.md for the daemon side (ClientQuery adapter, control socket, HTTP API, asset resolution).
       - OVERLAY.md for the browser overlay (DOM structure, talk/idle behavior, CSS expectations, polling strategy).

Implementation Outline

    Daemon (tsraild.py)

    - Language: Python 3 (asyncio-based).
    - Core pieces:
      - ClientQuery client:
        - Connects to 127.0.0.1:25639.
        - Handles banner/greeting lines.
        - Uses CRLF line endings for commands.
        - Single async reader task (_rx_loop) that:
          - Reads lines via readline().
          - Routes notify* lines to a notification handler.
          - Pushes non-notify lines into an asyncio.Queue for send_cmd().
      - Auth flow:
        - On connect, read API key from ~/.config/tsrail/clientquery.key.
        - Send "auth apikey=<key>" and wait for error id=0 msg=ok.
        - On success, register notifications (clientnotifyregister schandlerid=1 event=any).
      - State tracking:
        - Maintain in-memory map of clients in the current channel:
          - UID, clid, nickname, talking, approved, ignored, muted_by_us.
        - Update on notifycliententerview, notifyclientleftview, notifyclientmoved.
        - Update talking flag on notifytalkstatuschange (with a short debounce).

      - Policies:
        - auto-mute-unknown:
          - For any client in the monitored channel who is not approved and not ignored, issue clientmute.
          - Optionally unmute when they become approved.
        - require-approved:
          - Ensures only approved users are treated as “visible” in overlay state.
        - ignore_uids:
          - Users whose presence is tracked but never emitted in users[].

      - HTTP server:
        - Exposes /state.json with a structure matching OVERLAY.md.
        - Optionally serves /overlay/ and /assets/ from ~/.local/share/tsrail.

    Control Socket

    - Listener:
      - UNIX socket at ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/tsrail.sock.
      - Mode 0700, owner user.
    - Commands:
      - key-status : check if API key file exists.
      - setkey <apikey> : write key file and trigger re-auth.
      - status : one-line status including link_ok, auth, schandlerid, channel_id, counts, and /state.json URL.
      - dump-state : JSON dump of current state (for debugging).
      - approve-uid <uid> / approve-clid <clid> / approve-nick <nickname> :
        - Add entry to approved list (persistent).
      - unapprove-uid <uid> : remove from approved list.
      - approved-list : print all approved entries.
      - ignore-uid <uid> / unignore-uid <uid> / ignore-list :
        - Manage ignore set.
      - policy <name> <value> :
        - Update policies (auto-mute-unknown, require-approved, target-channel, show-ignored).

    HTTP & Overlay

    - HTTP service:
      - Very small, e.g. aiohttp or builtin asyncio streams:
        - /state.json : returns JSON derived from in-memory state.
        - /overlay/ : returns overlay HTML.
        - /assets/ : static files rooted at ~/.local/share/tsrail/assets/.
    - Overlay HTML/JS:
      - Single-page minimal app:
        - Fetches /state.json every 200–500 ms.
        - Rebuilds or updates DOM for the vertical rail.
        - Toggles `talking` vs `idle` class per user.
      - CSS:
        - Positions rail at left side of screen.
        - Uses transparent background.
        - For each user:
          - Stacks avatar and monitor frame with absolute positioning.
          - Uses CSS transitions (transform, brightness) for “hop and illuminate”.
          - Shows nickname below the monitor.

    Storage

    - Config directory:
      - ~/.config/tsrail/clientquery.key
      - ~/.config/tsrail/config.json (approved list, ignore list, policy flags).
    - Data / assets directory:
      - ~/.local/share/tsrail/assets/...
      - ~/.local/share/tsrail/log (if using svlogd).

Tasks

    - [ ] Add DESIGN.md describing:
          - Daemon responsibilities and architecture.
          - ClientQuery adapter design (single-reader, notify handling).
          - Control socket protocol (commands and responses).
          - HTTP endpoints and JSON contract.
          - Asset resolution and directory layout.
    - [ ] Add OVERLAY.md describing:
          - Expected /state.json format.
          - DOM structure and CSS classes.
          - Talk/idle behavior and animation semantics.
          - Polling strategy and error handling in the overlay.
    - [ ] Implement tsraild.py with:
          - Async ClientQuery adapter and reconnection.
          - API key handling and auth logic.
          - Channel/user/talking tracking.
          - Policy hooks (auto-mute, require-approved, ignore_uids).
          - Control socket server with the defined command set.
          - HTTP server with /state.json and base /overlay/ endpoint.
    - [ ] Add overlay assets:
          - Placeholder avatars and monitor frames.
          - Example user assets folder layout.
          - Basic overlay index.html, overlay.css, overlay.js wired to /state.json.
    - [ ] Add Devuan/runit service docs:
          - Example run scripts for supervising tsraild as a user service.
          - Notes on logs and troubleshooting.

Acceptance Criteria

    - DESIGN.md and OVERLAY.md exist and describe:
      - The daemon behavior, control socket protocol, HTTP endpoints, and overlay contract.
      - The DOM structure, CSS, and expected /state.json payload for the overlay.

    - Running tsraild.py on Devuan with TS3 ClientQuery enabled allows:
      - Setting API key via setkey over the UNIX socket.
      - status returning link_ok=1 and auth=1 once TS is reachable and auth succeeds.
      - /state.json returning valid JSON with:
        - server info,
        - counts,
        - an empty users[] list when no approved users are present.

    - Approving a user while they are in the TS channel (via approve-nick or approve-uid):
      - Causes that user to appear in /state.json.users[] with the correct nickname and asset paths.
      - Causes the overlay page at /overlay/ to render that user’s monitor + avatar + nickname.

    - When the user speaks in TS:
      - /state.json toggles the talking flag accordingly.
      - The overlay toggles CSS classes so the user “hops and illuminates” and shows the animated frame/avatar if configured.

    - When an unknown (unapproved, non-ignored) user joins the monitored channel and auto-mute-unknown is enabled:
      - The daemon mutes that user in TS (clientmute) only once.
      - The unknown user does not appear in users[].


