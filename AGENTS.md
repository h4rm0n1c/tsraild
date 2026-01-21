# Agent Notes

This repository contains the **TS Rail daemon** (`tsraild`), a TeamSpeak activity rail for streaming overlays.

## Project layout

- `tsraild.py`: Async daemon that connects to TeamSpeak ClientQuery and serves `/state.json` + overlay.
- `overlay/`: Static overlay HTML/CSS/JS.
- `assets/`: Default example assets.
- `scripts/tsrailctl`: CLI helper for the control socket.
- `DESIGN.md` / `OVERLAY.md`: Behavior and overlay contract references.

## Working conventions

- Keep changes focused and documented.
- Do **not** add binary files to the repo.
- Prefer updating `DESIGN.md` / `OVERLAY.md` when behavior or the overlay contract changes.
- If adding new CLI commands or config fields, also update `README.md`.
