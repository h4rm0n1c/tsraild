# TS Rail Daemon (tsraild)

TS Rail is a small TeamSpeak activity rail daemon for stream overlays. It connects to the TeamSpeak ClientQuery interface, tracks who is present in a target channel, and exposes an HTTP endpoint (`/state.json`) plus a ready-made overlay at `/overlay/` that you can drop into OBS as a browser source.

## What it does

- **ClientQuery bridge** to the TeamSpeak client (auth via API key).
- **State tracking** for clients in a channel: nickname, UID, talking state, approved/ignored flags.
- **Policy controls** (auto-mute unknowns, approval gating, ignored users, target channel).
- **HTTP service** for overlay data and static assets.
- **Control socket** for a lightweight CLI workflow.

For protocol and overlay details, see [`DESIGN.md`](DESIGN.md) and [`OVERLAY.md`](OVERLAY.md).

## Requirements

- Python 3.8+ (asyncio-based daemon).
- A TeamSpeak client with **ClientQuery** enabled (default on `127.0.0.1:25639`).
- `socat` for `scripts/tsrailctl` convenience commands.

## Quick start

1. Clone the repo and run the daemon:

   ```bash
   ./tsraild.py
   ```

2. Set the ClientQuery API key (from the TeamSpeak client):

   ```bash
   scripts/tsrailctl setkey YOUR_API_KEY
   ```

3. Check status:

   ```bash
   scripts/tsrailctl status
   ```

4. Open the overlay in a browser (or OBS browser source):

   ```
   http://127.0.0.1:17891/overlay/
   ```

## Configuration

Config and data are stored in the user’s home directory:

- `~/.config/tsrail/config.json`
- `~/.config/tsrail/clientquery.key`
- `~/.local/share/tsrail/assets/`

A minimal `config.json` looks like this:

```json
{
  "approved": ["UID1", "UID2"],
  "ignored": ["UID3"],
  "policies": {
    "auto-mute-unknown": true,
    "require-approved": true,
    "target-channel": 7,
    "show-ignored": false
  },
  "http": { "host": "127.0.0.1", "port": 17891 },
  "clientquery": { "host": "127.0.0.1", "port": 25639 }
}
```

You can update these values via the control socket (`tsrailctl policy`, `approve-uid`, etc.) and they will persist.

## Control socket commands

`scripts/tsrailctl` is a helper that talks to the UNIX control socket:

```bash
scripts/tsrailctl status
scripts/tsrailctl key-status
scripts/tsrailctl approve-uid <uid>
scripts/tsrailctl approve-nick <nickname>
scripts/tsrailctl ignore-uid <uid>
scripts/tsrailctl policy target-channel 7
scripts/tsrailctl dump-state
```

There are also service helpers for a basic user-space daemon workflow:

```bash
scripts/tsrailctl service-start
scripts/tsrailctl service-stop
scripts/tsrailctl service-status
```

These expect the repo to live at `~/tsraild` and log to `~/.local/share/tsrail/tsraild.log`.

## Overlay assets

Assets are served from `~/.local/share/tsrail/assets/` and can be overridden per user. The daemon accepts avatar assets in `svg`, `png`, `apng`, `gif`, `webp`, and `avif` formats, so animated GIF/APNG avatars are supported. Recommended layout:

```
assets/frames/tv_idle.png
assets/frames/tv_talk.png
assets/users/<uid>/avatar.png
assets/users/<uid>/avatar_talk.gif
```

If a talk asset is missing, the idle asset is reused. Default example assets live in the repo’s `assets/` directory.

## Development notes

- The overlay contract is defined in [`OVERLAY.md`](OVERLAY.md).
- The daemon behavior and policy hooks are described in [`DESIGN.md`](DESIGN.md).
- This repo is pure Python + static web assets; no binary files should be added.

## License

TBD.
