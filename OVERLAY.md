# TS Rail Overlay Contract

This document describes the overlay expectations for `/overlay/`, the polling strategy, and the `/state.json` schema the daemon exposes. It is intended as a guide for building and using the overlay per `AGENTS.md`.

## `/state.json` Schema

```json
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
```

### Contract

- `users[]` contains only approved and non-ignored users in the monitored channel unless a policy explicitly includes ignored users.
- Sorting is pre-applied by the daemon (nickname), but overlay may re-sort.
- A missing talk asset (`null`) should result in omitting the element or reusing the idle asset.
- Empty `users[]` is valid and should render an empty rail.

## DOM Structure

Root container and per-user elements:

```html
<div id="tsrail-rail">
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
</div>
```

### CSS Class Semantics

- `.tsrail-user`: Wrapper for one user; may receive `talking`/`idle` classes.
- `.tsrail-frame` and `.tsrail-avatar`: Containers for frame and avatar layers.
- `.frame-idle` / `.frame-talk`: Idle and talk variants of the monitor frame; visibility toggled via classes on the parent.
- `.avatar-idle` / `.avatar-talk`: Idle and talk variants of the avatar; also toggled via parent classes.
- `.tsrail-nickname`: Label under the monitor.
- Visibility toggling: apply `.talking` on `.tsrail-user` when `talking=true`; otherwise `.idle`. CSS should hide the opposite-state assets and drive animations (e.g., `transform` and brightness for “hop and illuminate”).

## Polling and Error Handling

- Poll `/state.json` every 200–500 ms.
- On fetch failure, optionally back off and display a lightweight error indicator without breaking existing DOM.
- Rebuild or patch DOM from the latest payload; keep both idle and talk elements present for each user.

## Talk and Idle Behavior

- When `talking=true` for a user:
  - Add `.talking` and remove `.idle` on `.tsrail-user`.
  - Show `frame-talk` and `avatar-talk` (or reuse idle if talk assets are missing).
  - Trigger hop/illumination animations via CSS transitions.
- When `talking=false`:
  - Add `.idle` and remove `.talking`.
  - Show `frame-idle` and `avatar-idle`.
  - Return to the steady idle pose.

## Asset Resolution

- Assets resolve relative to `/assets/` served by the daemon, defaulting to `~/.local/share/tsrail/assets/`.
- Recommended layout:
  - `assets/frames/monitor_idle.png`
  - `assets/frames/monitor_talk.apng`
  - `assets/users/<uid>/avatar.png` (idle)
  - `assets/users/<uid>/avatar.gif` or `avatar.apng` (talk)
- If a talk asset is missing or null in the JSON, reuse the idle asset.

## Overlay Behavior Summary

- The overlay is transparent-friendly for OBS browser sources.
- Renders a vertical rail (e.g., left side of the canvas) with stacked frame + avatar per user and nickname labels.
- Depends entirely on `/state.json` for authority; no client-side voice detection.

