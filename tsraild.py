#!/usr/bin/env python3
import asyncio
import json
import os
import pathlib
import shutil
import signal
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

CONFIG_DIR = pathlib.Path(os.path.expanduser("~/.config/tsrail"))
DATA_DIR = pathlib.Path(os.path.expanduser("~/.local/share/tsrail"))
ASSETS_DIR = DATA_DIR / "assets"
DEFAULT_OVERLAY_DIR = pathlib.Path(__file__).resolve().parent / "overlay"
DEFAULT_ASSETS_DIR = DEFAULT_OVERLAY_DIR.parent / "assets"
ALLOWED_AVATAR_EXTS = (".svg", ".png", ".apng", ".gif", ".webp", ".avif")
SOCKET_PATH = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "tsrail.sock"
KEY_FILE = CONFIG_DIR / "clientquery.key"
CONFIG_FILE = CONFIG_DIR / "config.json"
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 17891
CLIENTQUERY_HOST = "127.0.0.1"
CLIENTQUERY_PORT = 25639


def ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

def ensure_user_assets(uid: str) -> None:
    if not uid:
        return
    user_dir = ASSETS_DIR / "users" / uid
    user_dir_existing = user_dir.exists()
    user_dir.mkdir(parents=True, exist_ok=True)
    if user_dir_existing:
        return
    default_dirs = [ASSETS_DIR / "users" / "example", DEFAULT_ASSETS_DIR / "users" / "example"]
    source_dir = next((path for path in default_dirs if path.exists()), None)
    if not source_dir:
        return
    has_avatar = any((user_dir / f"avatar{ext}").exists() for ext in ALLOWED_AVATAR_EXTS)
    has_avatar_talk = any((user_dir / f"avatar_talk{ext}").exists() for ext in ALLOWED_AVATAR_EXTS)
    defaults = {"avatar": has_avatar, "avatar_talk": has_avatar_talk}
    for stem, exists in defaults.items():
        if exists:
            continue
        src = source_dir / f"{stem}.svg"
        dst = user_dir / f"{stem}.svg"
        if src.is_file():
            shutil.copy2(src, dst)


@dataclass
class Policies:
    auto_mute_unknown: bool = True
    require_approved: bool = True
    target_channel: Optional[int] = None
    target_channel_name: Optional[str] = None
    show_ignored: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Policies":
        return cls(
            auto_mute_unknown=bool(data.get("auto-mute-unknown", data.get("auto_mute_unknown", True))),
            require_approved=bool(data.get("require-approved", data.get("require_approved", True))),
            target_channel=data.get("target-channel") or data.get("target_channel"),
            target_channel_name=data.get("target-channel-name") or data.get("target_channel_name"),
            show_ignored=bool(data.get("show-ignored", data.get("show_ignored", False))),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "auto-mute-unknown": self.auto_mute_unknown,
            "require-approved": self.require_approved,
            "target-channel": self.target_channel,
            "target-channel-name": self.target_channel_name,
            "show-ignored": self.show_ignored,
        }


@dataclass
class Client:
    clid: str
    uid: str
    nickname: str
    channel_id: Optional[int]
    talking: bool = False
    approved: bool = False
    ignored: bool = False
    muted_by_us: bool = False


class PersistentConfig:
    def __init__(self) -> None:
        self.approved_uids: Set[str] = set()
        self.ignore_uids: Set[str] = set()
        self.policies = Policies()
        self.load()

    def load(self) -> None:
        ensure_dirs()
        if CONFIG_FILE.exists():
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self.approved_uids = set(data.get("approved", []))
            self.ignore_uids = set(data.get("ignored", []))
            self.policies = Policies.from_dict(data.get("policies", {}))

    def save(self) -> None:
        ensure_dirs()
        data = {
            "approved": sorted(self.approved_uids),
            "ignored": sorted(self.ignore_uids),
            "policies": self.policies.to_dict(),
        }
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


class ClientQueryConnection:
    def __init__(self, state: "TSRailState") -> None:
        self.state = state
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.pending: Optional[asyncio.Future] = None
        self.pending_buffer: List[str] = []
        self.lock = asyncio.Lock()
        self.running = True
        self.link_ok = False
        self.auth_ok = False
        self.reader_task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        while self.running:
            try:
                self.reader, self.writer = await asyncio.open_connection(CLIENTQUERY_HOST, CLIENTQUERY_PORT)
                self.link_ok = True
                self.reader_task = asyncio.create_task(self._reader_loop())
                await self._post_connect()
                await self.reader_task
            except (ConnectionError, OSError):
                self.link_ok = False
                self.auth_ok = False
                self.state.clear_clients()
                await asyncio.sleep(2.0)
            finally:
                if self.writer:
                    self.writer.close()
                    await self.writer.wait_closed()
                self.reader = None
                self.writer = None

    async def _post_connect(self) -> None:
        key = self.state.load_api_key()
        if not key:
            self.auth_ok = False
            return
        resp = await self.send_command(f"auth apikey={key}")
        if not self._is_ok(resp):
            self.auth_ok = False
            return
        self.auth_ok = True
        await self._select_schandler()
        await self.send_command(f"clientnotifyregister schandlerid={self.state.schandlerid or 1} event=any")
        await self.send_command("servernotifyregister event=any")
        await self.sync_state()

    async def sync_state(self) -> None:
        await self._refresh_identity()
        await self._refresh_channels()
        await self._refresh_channel_name()
        await self._refresh_clients()

    async def stop(self) -> None:
        self.running = False
        if self.reader_task:
            self.reader_task.cancel()

    async def reauthenticate(self) -> None:
        if not self.writer:
            return
        key = self.state.load_api_key()
        if not key:
            return
        resp = await self.send_command(f"auth apikey={key}")
        if self._is_ok(resp):
            self.auth_ok = True
            await self.sync_state()

    async def on_server_change(self) -> None:
        if not self.auth_ok:
            return
        await self._select_schandler()
        await self.send_command(f"clientnotifyregister schandlerid={self.state.schandlerid or 1} event=any")
        await self.send_command("servernotifyregister event=any")
        await self.sync_state()

    async def send_command(self, cmd: str) -> List[str]:
        if not self.writer or not self.reader:
            return ["error id=2569 msg=not\\sconnected"]
        async with self.lock:
            self.pending = asyncio.get_event_loop().create_future()
            self.pending_buffer = []
            self.writer.write((cmd + "\n").encode("utf-8"))
            await self.writer.drain()
            resp: List[str] = await self.pending
            self.pending = None
            self.pending_buffer = []
            return resp

    async def _reader_loop(self) -> None:
        assert self.reader
        while not self.reader.at_eof():
            raw = await self.reader.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            if line.startswith("notify"):
                self.state.handle_notification(line)
            elif line.startswith("error id=1796"):
                if self.writer:
                    self.writer.write(b"\n")
                    await self.writer.drain()
            elif self.pending is not None:
                self.pending_buffer.append(line)
                if line.startswith("error "):
                    if not self.pending.done():
                        self.pending.set_result(list(self.pending_buffer))
            else:
                # Unsolicited non-notify; ignore.
                pass

    @staticmethod
    def _is_ok(lines: List[str]) -> bool:
        return any(line.startswith("error id=0") for line in lines)

    async def _refresh_identity(self) -> None:
        resp = await self.send_command("whoami")
        self._update_identity(resp)

    async def _select_schandler(self) -> int:
        resp = await self.send_command("whoami")
        schandlerid = self._update_identity(resp)
        await self.send_command(f"use schandlerid={schandlerid}")
        return schandlerid

    def _update_identity(self, resp: List[str]) -> int:
        schandlerid = self.state.schandlerid or 1
        for line in resp:
            if line.startswith("clid"):
                data = parse_kv(line)
                if data.get("schandlerid"):
                    schandlerid = int(data["schandlerid"])
                if data.get("cid"):
                    self.state.server_channel_id = int(data["cid"])
                if data.get("clid"):
                    self.state.own_clid = data["clid"]
        self.state.schandlerid = schandlerid
        return schandlerid

    async def _refresh_channel_name(self) -> None:
        channel_ids: List[int] = []
        if self.state.config.policies.target_channel:
            channel_ids.append(self.state.config.policies.target_channel)
        if self.state.server_channel_id and self.state.server_channel_id not in channel_ids:
            channel_ids.append(self.state.server_channel_id)
        for channel_id in channel_ids:
            resp = await self.send_command(f"channelinfo cid={channel_id}")
            for line in resp:
                if line.startswith("cid"):
                    data = parse_kv(line)
                    if data.get("channel_name"):
                        name = decode_ts(data["channel_name"])
                        if channel_id == self.state.server_channel_id:
                            self.state.server_channel_name = name
                        self.state.channel_names[channel_id] = name

    async def _refresh_channels(self) -> None:
        resp = await self.send_command("channellist")
        if not resp:
            return
        for line in resp:
            if not line or line.startswith("error "):
                continue
            for entry in parse_multi_kv(line):
                cid_raw = entry.get("cid")
                name_raw = entry.get("channel_name")
                if not cid_raw or name_raw is None:
                    continue
                cid = int(cid_raw)
                self.state.channel_names[cid] = decode_ts(name_raw)
        self.state.refresh_target_from_name()

    async def _refresh_clients(self) -> None:
        resp = await self.send_command("clientlist -voice -uid")
        if not resp:
            return
        new_clients: Dict[str, Client] = {}
        for line in resp:
            if not line or line.startswith("error "):
                continue
            for entry in parse_multi_kv(line):
                clid = entry.get("clid")
                uid = entry.get("client_unique_identifier", "")
                nickname = decode_ts(entry.get("client_nickname", ""))
                cid_raw = entry.get("cid")
                cid = int(cid_raw) if cid_raw else None
                if not clid:
                    continue
                if clid == self.state.own_clid:
                    self.state.own_uid = uid
                    self.state.own_nickname = nickname
                    self.state.server_channel_id = cid
                    continue
                client = Client(
                    clid=clid,
                    uid=uid,
                    nickname=nickname,
                    channel_id=cid,
                    approved=uid in self.state.config.approved_uids,
                    ignored=uid in self.state.config.ignore_uids,
                )
                new_clients[clid] = client
        self.state.clients = new_clients
        for client in self.state.clients.values():
            self.state._apply_policies(client)


class TSRailState:
    def __init__(self, config: PersistentConfig):
        self.config = config
        self.clients: Dict[str, Client] = {}
        self.server_channel_id: Optional[int] = None
        self.server_channel_name: Optional[str] = None
        self.channel_names: Dict[int, str] = {}
        self.schandlerid: Optional[int] = 1
        self.own_clid: Optional[str] = None
        self.own_uid: Optional[str] = None
        self.own_nickname: Optional[str] = None
        self.last_ts: float = time.time()
        self.connection: Optional[ClientQueryConnection] = None

    def attach_connection(self, conn: ClientQueryConnection) -> None:
        self.connection = conn

    def load_api_key(self) -> Optional[str]:
        if KEY_FILE.exists():
            return KEY_FILE.read_text(encoding="utf-8").strip()
        return None

    def clear_clients(self) -> None:
        self.clients.clear()

    def handle_notification(self, line: str) -> None:
        data = parse_kv(line)
        event = line.split(" ", 1)[0]
        if event.startswith("notifycliententerview"):
            self._client_enter(data)
        elif event.startswith("notifyclientleftview"):
            self._client_left(data)
        elif event.startswith("notifyclientmoved"):
            self._client_moved(data)
        elif event.startswith("notifytalkstatuschange"):
            self._talk_status(data)
        elif event.startswith("notifyclientupdated"):
            self._client_updated(data)
        elif event.startswith("notifyconnectstatuschange"):
            self._connect_status_changed(data)
        elif event.startswith("notifycurrentserverconnectionchanged"):
            self._server_connection_changed(data)
        self.last_ts = time.time()

    def monitor_channel_id(self) -> Optional[int]:
        target = self.config.policies.target_channel
        if target:
            if self.server_channel_id == target:
                return target
            return None
        return self.server_channel_id

    def target_channel_active(self) -> bool:
        target = self.config.policies.target_channel
        current_channel = self.server_channel_id
        return target is not None and current_channel is not None and current_channel == target

    def bot_info(self) -> Dict[str, object]:
        return {
            "clid": self.own_clid,
            "uid": self.own_uid,
            "nickname": self.own_nickname,
            "channel_id": self.server_channel_id,
            "channel_name": self._resolve_channel_name(self.server_channel_id),
        }

    def refresh_target_from_name(self) -> None:
        name = self.config.policies.target_channel_name
        if not name:
            return
        cid = self._resolve_channel_id_by_name(name)
        if cid != self.config.policies.target_channel:
            self.config.policies.target_channel = cid
            self.config.save()

    def apply_target_channel(self, channel_id: Optional[int], channel_name: Optional[str]) -> None:
        self.config.policies.target_channel = channel_id
        self.config.policies.target_channel_name = channel_name
        if channel_id is None:
            self.server_channel_name = None
        for client in self.clients.values():
            self._apply_policies(client)
        self.config.save()

    def _resolve_channel_id_by_name(self, name: str) -> Optional[int]:
        needle = name.casefold()
        for cid, cname in self.channel_names.items():
            if cname.casefold() == needle:
                return cid
        return None

    def _client_enter(self, data: Dict[str, str]) -> None:
        uid = data.get("client_unique_identifier", "")
        clid = data.get("clid", "")
        nickname = decode_ts(data.get("client_nickname", ""))
        cid_raw = data.get("ctid") or data.get("cid")
        cid = int(cid_raw) if cid_raw else None
        adopted_channel = False
        if self.server_channel_id is None:
            self.server_channel_id = cid
            adopted_channel = cid is not None
        if clid == self.own_clid:
            self.own_uid = uid
            self.own_nickname = nickname
            return
        client = Client(
            clid=clid,
            uid=uid,
            nickname=nickname,
            channel_id=cid,
            approved=uid in self.config.approved_uids,
            ignored=uid in self.config.ignore_uids,
        )
        self.clients[clid] = client
        if adopted_channel and self.connection:
            asyncio.create_task(self.connection._refresh_channel_name())
        self._apply_policies(client)

    def _client_left(self, data: Dict[str, str]) -> None:
        clid = data.get("clid")
        if clid and clid == self.own_clid:
            self.server_channel_id = None
            self.server_channel_name = None
            return
        if clid and clid in self.clients:
            del self.clients[clid]

    def _client_moved(self, data: Dict[str, str]) -> None:
        clid = data.get("clid")
        cid_raw = data.get("ctid") or data.get("cid")
        cid = int(cid_raw) if cid_raw else None
        if clid and clid == self.own_clid:
            self.server_channel_id = cid
            if cid is None:
                self.server_channel_name = None
            if self.connection:
                asyncio.create_task(self.connection._refresh_channel_name())
            for client in self.clients.values():
                self._apply_policies(client)
        if clid and clid in self.clients:
            self.clients[clid].channel_id = cid
            self._apply_policies(self.clients[clid])

    def _client_updated(self, data: Dict[str, str]) -> None:
        clid = data.get("clid")
        if not clid or clid not in self.clients:
            if clid == self.own_clid and "client_nickname" in data:
                self.own_nickname = decode_ts(data["client_nickname"])
            return
        if "client_nickname" in data:
            self.clients[clid].nickname = decode_ts(data["client_nickname"])

    def _connect_status_changed(self, data: Dict[str, str]) -> None:
        status = data.get("status")
        schandlerid = data.get("schandlerid")
        if schandlerid:
            self.schandlerid = int(schandlerid)
        if status in {"0", "disconnected"}:
            return
        if self.connection:
            asyncio.create_task(self.connection.on_server_change())

    def _server_connection_changed(self, data: Dict[str, str]) -> None:
        schandlerid = data.get("schandlerid")
        if schandlerid:
            self.schandlerid = int(schandlerid)
        if self.connection:
            asyncio.create_task(self.connection.on_server_change())

    def _talk_status(self, data: Dict[str, str]) -> None:
        clid = data.get("clid")
        status = data.get("status")
        if clid and clid in self.clients:
            self.clients[clid].talking = status == "1"

    def _apply_policies(self, client: Client) -> None:
        monitor_channel = self.monitor_channel_id()
        in_channel = monitor_channel is None or client.channel_id == monitor_channel
        if self.config.policies.target_channel and monitor_channel is None:
            in_channel = False
        client.approved = client.uid in self.config.approved_uids
        client.ignored = client.uid in self.config.ignore_uids
        if in_channel and self.config.policies.auto_mute_unknown:
            if not client.approved and not client.ignored and not client.muted_by_us:
                asyncio.create_task(self._mute_client(client))

    async def _mute_client(self, client: Client) -> None:
        if not self.connection:
            return
        await self.connection.send_command(f"clientmute clid={client.clid}")
        client.muted_by_us = True

    def approve_uid(self, uid: str) -> None:
        self.config.approved_uids.add(uid)
        for client in self.clients.values():
            if client.uid == uid:
                client.approved = True
                client.muted_by_us = False
        self.config.save()

    def unapprove_uid(self, uid: str) -> None:
        self.config.approved_uids.discard(uid)
        for client in self.clients.values():
            if client.uid == uid:
                client.approved = False
        self.config.save()

    def ignore_uid(self, uid: str) -> None:
        self.config.ignore_uids.add(uid)
        for client in self.clients.values():
            if client.uid == uid:
                client.ignored = True
        self.config.save()

    def unignore_uid(self, uid: str) -> None:
        self.config.ignore_uids.discard(uid)
        for client in self.clients.values():
            if client.uid == uid:
                client.ignored = False
        self.config.save()

    def counts(self) -> Dict[str, int]:
        target_channel = self.monitor_channel_id()
        if self.config.policies.target_channel and target_channel is None:
            return {
                "approved_total": len(self.config.approved_uids),
                "present_approved": 0,
                "present_unknown": 0,
                "present_ignored": 0,
            }
        approved_total = len(self.config.approved_uids)
        present_approved = 0
        present_unknown = 0
        present_ignored = 0
        for client in self.clients.values():
            if client.uid and self.own_uid and client.uid == self.own_uid:
                continue
            if target_channel and client.channel_id != target_channel:
                continue
            if client.ignored:
                present_ignored += 1
            elif client.approved:
                present_approved += 1
            else:
                present_unknown += 1
        return {
            "approved_total": approved_total,
            "present_approved": present_approved,
            "present_unknown": present_unknown,
            "present_ignored": present_ignored,
        }

    def build_users(self) -> List[Dict[str, object]]:
        target_channel = self.monitor_channel_id()
        if self.config.policies.target_channel and target_channel is None:
            return []
        users: List[Client] = []
        for client in self.clients.values():
            if client.uid and self.own_uid and client.uid == self.own_uid:
                continue
            if target_channel and client.channel_id != target_channel:
                continue
            if client.ignored and not self.config.policies.show_ignored:
                continue
            if self.config.policies.require_approved and not client.approved:
                continue
            users.append(client)
        users.sort(key=lambda c: c.nickname.lower())
        result = []
        for client in users:
            assets = self._build_assets(client)
            result.append(
                {
                    "uid": client.uid,
                    "nickname": client.nickname,
                    "talking": client.talking,
                    "approved": client.approved,
                    "ignored": client.ignored,
                    "assets": assets,
                }
            )
        return result

    def build_unknown_users(self) -> List[Dict[str, object]]:
        target_channel = self.monitor_channel_id()
        if self.config.policies.target_channel and target_channel is None:
            return []
        unknowns: List[Client] = []
        for client in self.clients.values():
            if client.uid and self.own_uid and client.uid == self.own_uid:
                continue
            if target_channel and client.channel_id != target_channel:
                continue
            if client.approved or client.ignored:
                continue
            unknowns.append(client)
        unknowns.sort(key=lambda c: c.nickname.lower())
        return [
            {
                "uid": client.uid,
                "nickname": client.nickname,
                "channel_id": client.channel_id,
            }
            for client in unknowns
        ]

    def build_channels(self) -> List[Dict[str, object]]:
        return [
            {"id": cid, "name": name}
            for cid, name in sorted(self.channel_names.items(), key=lambda kv: kv[1].lower())
        ]

    def _resolve_channel_name(self, cid: Optional[int]) -> Optional[str]:
        if cid is None:
            return None
        if cid in self.channel_names:
            return self.channel_names[cid]
        if cid == self.server_channel_id:
            return self.server_channel_name
        return None

    def _resolve_user_asset(self, uid: str, stem: str) -> Optional[str]:
        user_dir = ASSETS_DIR / "users" / uid
        for ext in ALLOWED_AVATAR_EXTS:
            candidate = user_dir / f"{stem}{ext}"
            if candidate.is_file():
                return f"assets/users/{uid}/{candidate.name}"
        return None

    def _build_assets(self, client: Client) -> Dict[str, Optional[str]]:
        ensure_user_assets(client.uid)
        avatar_idle = self._resolve_user_asset(client.uid, "avatar")
        avatar_talk = self._resolve_user_asset(client.uid, "avatar_talk") or avatar_idle
        frame_idle = "assets/frames/tv_idle.png"
        frame_talk = "assets/frames/tv_talk.png"
        return {
            "avatar_idle": avatar_idle,
            "avatar_talk": avatar_talk,
            "frame_idle": frame_idle,
            "frame_talk": frame_talk,
        }

    def state_json(self) -> Dict[str, object]:
        monitor_channel = self.monitor_channel_id()
        target_channel = self.config.policies.target_channel
        target_channel_name = self._resolve_channel_name(target_channel) or self.config.policies.target_channel_name
        return {
            "ts": time.time(),
            "server": {
                "schandlerid": self.schandlerid,
                "current_channel_id": self.server_channel_id,
                "current_channel_name": self._resolve_channel_name(self.server_channel_id),
                "target_channel_id": target_channel,
                "target_channel_name": target_channel_name,
                "target_channel_active": self.target_channel_active(),
            },
            "bot": self.bot_info(),
            "counts": self.counts(),
            "users": self.build_users(),
            "unknown_users": self.build_unknown_users(),
            "channels": self.build_channels(),
        }


def parse_kv(line: str) -> Dict[str, str]:
    pairs = line.split()
    data: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        data[key] = decode_ts(value)
    return data


def decode_ts(value: str) -> str:
    mapping = {
        "s": " ",
        "p": "|",
        "/": "/",
        "\\": "\\",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    result_chars: List[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            i += 1
            escaped = value[i]
            result_chars.append(mapping.get(escaped, escaped))
        else:
            result_chars.append(ch)
        i += 1
    return "".join(result_chars)


def parse_multi_kv(line: str) -> List[Dict[str, str]]:
    return [parse_kv(block) for block in line.split("|") if block]


class ControlSocket:
    def __init__(self, state: TSRailState, conn: ClientQueryConnection) -> None:
        self.state = state
        self.conn = conn
        self.server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.server = await asyncio.start_unix_server(self.handle_client, path=str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o700)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while not reader.at_eof():
                data = await reader.readline()
                if not data:
                    break
                response = await self.dispatch(data.decode().strip())
                writer.write(response.encode("utf-8"))
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def dispatch(self, line: str) -> str:
        if not line:
            return "error empty\n"
        parts = line.split()
        cmd = parts[0]
        args = parts[1:]
        if cmd == "status":
            counts = self.state.counts()
            link_ok = int(self.conn.link_ok)
            auth = int(self.conn.auth_ok)
            channel_id = self.state.config.policies.target_channel or self.state.server_channel_id
            channel_name = self.state._resolve_channel_name(channel_id) or ""
            return (
                f"ok link_ok={link_ok} auth={auth} schandlerid={self.state.schandlerid} "
                f"channel_id={channel_id} channel_name={channel_name} counts={counts} "
                f"url=http://{HTTP_HOST}:{HTTP_PORT}/state.json\n"
            )
        if cmd == "key-status":
            exists = int(KEY_FILE.exists())
            return f"ok key_present={exists}\n"
        if cmd == "setkey" and args:
            ensure_dirs()
            KEY_FILE.write_text(args[0], encoding="utf-8")
            await self.conn.reauthenticate()
            return "ok\n"
        if cmd == "dump-state":
            return json.dumps(self.state.state_json(), indent=2) + "\n"
        if cmd == "approve-uid" and args:
            self.state.approve_uid(args[0])
            return "ok\n"
        if cmd == "approve-clid" and args:
            client = self.state.clients.get(args[0])
            if client:
                self.state.approve_uid(client.uid)
                return "ok\n"
            return "error unknown clid\n"
        if cmd == "approve-nick" and args:
            nick = " ".join(args)
            for client in self.state.clients.values():
                if client.nickname == nick:
                    self.state.approve_uid(client.uid)
                    return "ok\n"
            return "error unknown nick\n"
        if cmd == "unapprove-uid" and args:
            self.state.unapprove_uid(args[0])
            return "ok\n"
        if cmd == "approved-list":
            return "\n".join(sorted(self.state.config.approved_uids)) + "\n"
        if cmd == "ignore-uid" and args:
            self.state.ignore_uid(args[0])
            return "ok\n"
        if cmd == "unignore-uid" and args:
            self.state.unignore_uid(args[0])
            return "ok\n"
        if cmd == "ignore-list":
            return "\n".join(sorted(self.state.config.ignore_uids)) + "\n"
        if cmd == "policy" and len(args) >= 2:
            name = args[0]
            value_raw = " ".join(args[1:])
            value: object
            if value_raw.lower() in {"1", "true", "yes", "on"}:
                value = True
            elif value_raw.lower() in {"0", "false", "no", "off"}:
                value = False
            else:
                try:
                    value = int(value_raw)
                except ValueError:
                    value = value_raw
            if name == "auto-mute-unknown":
                self.state.config.policies.auto_mute_unknown = bool(value)
            elif name == "require-approved":
                self.state.config.policies.require_approved = bool(value)
            elif name == "target-channel":
                if not value_raw:
                    self.state.apply_target_channel(None, None)
                    if self.conn:
                        await self.conn._refresh_channel_name()
                    return "ok\n"
                if self.conn:
                    await self.conn._refresh_channels()
                channel_id: Optional[int]
                channel_name: Optional[str] = None
                try:
                    channel_id = int(value_raw)
                    channel_name = self.state._resolve_channel_name(channel_id)
                except ValueError:
                    channel_name = value_raw
                    channel_id = self.state._resolve_channel_id_by_name(value_raw)
                if channel_id is None:
                    return "error unknown channel\n"
                if channel_name is None:
                    channel_name = value_raw
                self.state.apply_target_channel(channel_id, channel_name)
                if self.conn:
                    await self.conn._refresh_channel_name()
                    await self.conn._refresh_clients()
            elif name == "show-ignored":
                self.state.config.policies.show_ignored = bool(value)
            else:
                return "error unknown policy\n"
            self.state.config.save()
            return "ok\n"
        if cmd == "channels":
            lines = [f"{cid}\t{self.state.channel_names[cid]}" for cid in sorted(self.state.channel_names)]
            return "\n".join(lines) + "\n"
        return "error unknown\n"


class HttpServer:
    def __init__(self, state: TSRailState):
        self.state = state
        self.server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.handle_client, HTTP_HOST, HTTP_PORT)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            await writer.wait_closed()
            return
        parts = request_line.decode().strip().split()
        if len(parts) < 2:
            writer.close()
            await writer.wait_closed()
            return
        method, path = parts[0], parts[1]
        if method != "GET":
            await self._send_response(writer, 405, b"Method Not Allowed", content_type="text/plain")
            return
        if path == "/state.json":
            body = json.dumps(self.state.state_json(), indent=2).encode("utf-8")
            await self._send_response(writer, 200, body, content_type="application/json")
            return
        if path.startswith("/overlay"):
            rel = path[len("/overlay"):].lstrip("/") or "index.html"
            body, ctype, status = await self._read_static(rel, overlay=True)
            await self._send_response(writer, status, body, content_type=ctype)
            return
        if path.startswith("/assets"):
            rel = path[len("/assets"):].lstrip("/")
            body, ctype, status = await self._read_static(rel, overlay=False)
            await self._send_response(writer, status, body, content_type=ctype)
            return
        await self._send_response(writer, 404, b"Not Found", content_type="text/plain")

    async def _read_static(self, rel: str, overlay: bool) -> tuple[bytes, str, int]:
        if overlay:
            base_candidates = [DEFAULT_OVERLAY_DIR]
        else:
            base_candidates = [DEFAULT_ASSETS_DIR, ASSETS_DIR]
        for base in base_candidates:
            candidate = base / rel
            if candidate.is_file():
                content_type = guess_type(candidate.suffix)
                return candidate.read_bytes(), content_type, 200
        return b"Not Found", "text/plain", 404

    async def _send_response(self, writer: asyncio.StreamWriter, status: int, body: bytes, *, content_type: str) -> None:
        headers = [
            f"HTTP/1.1 {status} OK" if status == 200 else f"HTTP/1.1 {status} ERROR",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(headers).encode("utf-8") + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()


def guess_type(ext: str) -> str:
    ext = ext.lower()
    if ext in {".html", ".htm"}:
        return "text/html"
    if ext == ".css":
        return "text/css"
    if ext == ".js":
        return "application/javascript"
    if ext == ".svg":
        return "image/svg+xml"
    if ext == ".json":
        return "application/json"
    if ext in {".png", ".apng"}:
        return "image/png"
    if ext == ".gif":
        return "image/gif"
    if ext == ".webp":
        return "image/webp"
    if ext == ".avif":
        return "image/avif"
    return "application/octet-stream"


async def main() -> None:
    ensure_dirs()
    config = PersistentConfig()
    state = TSRailState(config)
    conn = ClientQueryConnection(state)
    state.attach_connection(conn)
    http = HttpServer(state)
    control = ControlSocket(state, conn)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for signame in {signal.SIGINT, signal.SIGTERM}:
        loop.add_signal_handler(signame, stop_event.set)

    await control.start()
    await http.start()
    tasks = [
        asyncio.create_task(conn.run()),
    ]

    await stop_event.wait()
    conn.running = False
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
