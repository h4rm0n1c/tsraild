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

