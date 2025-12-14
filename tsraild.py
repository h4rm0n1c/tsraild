#!/usr/bin/env python3
import asyncio
import json
import os
import pathlib
import signal
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

CONFIG_DIR = pathlib.Path(os.path.expanduser("~/.config/tsrail"))
DATA_DIR = pathlib.Path(os.path.expanduser("~/.local/share/tsrail"))
ASSETS_DIR = DATA_DIR / "assets"
OVERLAY_DIR = DATA_DIR / "overlay"
DEFAULT_OVERLAY_DIR = pathlib.Path(__file__).resolve().parent / "overlay"
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
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Policies:
    auto_mute_unknown: bool = True
    require_approved: bool = True
    target_channel: Optional[int] = None
    show_ignored: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Policies":
        return cls(
            auto_mute_unknown=bool(data.get("auto-mute-unknown", data.get("auto_mute_unknown", True))),
            require_approved=bool(data.get("require-approved", data.get("require_approved", True))),
            target_channel=data.get("target-channel") or data.get("target_channel"),
            show_ignored=bool(data.get("show-ignored", data.get("show_ignored", False))),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "auto-mute-unknown": self.auto_mute_unknown,
            "require-approved": self.require_approved,
            "target-channel": self.target_channel,
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
        await self.sync_state()

    async def sync_state(self) -> None:
        await self._refresh_identity()
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
                if data.get("client_id"):
                    self.state.own_clid = data["client_id"]
        self.state.schandlerid = schandlerid
        return schandlerid

    async def _refresh_channel_name(self) -> None:
        channel_id = self.state.config.policies.target_channel or self.state.server_channel_id
        if not channel_id:
            return
        resp = await self.send_command(f"channelinfo cid={channel_id}")
        for line in resp:
            if line.startswith("cid"):
                data = parse_kv(line)
                if data.get("channel_name"):
                    self.state.server_channel_name = decode_ts(data["channel_name"])
                    # Align the tracked channel id with the monitored target when set so
                    # /state.json reflects the intended channel on first sync.
                    if self.state.config.policies.target_channel:
                        self.state.server_channel_id = channel_id

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
        self.server_channel_id: Optional[int] = config.policies.target_channel
        self.server_channel_name: Optional[str] = None
        self.schandlerid: Optional[int] = 1
        self.own_clid: Optional[str] = None
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
        self.last_ts = time.time()

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
            return
        if "client_nickname" in data:
            self.clients[clid].nickname = decode_ts(data["client_nickname"])

    def _talk_status(self, data: Dict[str, str]) -> None:
        clid = data.get("clid")
        status = data.get("status")
        if clid and clid in self.clients:
            self.clients[clid].talking = status == "1"

    def _apply_policies(self, client: Client) -> None:
        target_channel = self.config.policies.target_channel or self.server_channel_id
        in_channel = target_channel is None or client.channel_id == target_channel
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
        target_channel = self.config.policies.target_channel or self.server_channel_id
        approved_total = len(self.config.approved_uids)
        present_approved = 0
        present_unknown = 0
        present_ignored = 0
        for client in self.clients.values():
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
        target_channel = self.config.policies.target_channel or self.server_channel_id
        users: List[Client] = []
        for client in self.clients.values():
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

    def _build_assets(self, client: Client) -> Dict[str, Optional[str]]:
        avatar_idle = f"assets/users/{client.uid}/avatar.svg"
        avatar_talk = f"assets/users/{client.uid}/avatar_talk.svg"
        frame_idle = "assets/frames/monitor_idle.svg"
        frame_talk = "assets/frames/monitor_talk.svg"
        return {
            "avatar_idle": avatar_idle,
            "avatar_talk": avatar_talk,
            "frame_idle": frame_idle,
            "frame_talk": frame_talk,
        }

    def state_json(self) -> Dict[str, object]:
        return {
            "ts": time.time(),
            "server": {
                "schandlerid": self.schandlerid,
                "channel_id": self.config.policies.target_channel or self.server_channel_id,
                "channel_name": self.server_channel_name,
            },
            "counts": self.counts(),
            "users": self.build_users(),
        }


def parse_kv(line: str) -> Dict[str, str]:
    pairs = line.split()
    data: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        data[key] = value.replace("\\s", " ")
    return data


def decode_ts(value: str) -> str:
    return value.replace("\\s", " ").replace("\\p", "|")


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
            return (
                f"ok link_ok={link_ok} auth={auth} schandlerid={self.state.schandlerid} "
                f"channel_id={channel_id} counts={counts} url=http://{HTTP_HOST}:{HTTP_PORT}/state.json\n"
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
            value_raw = args[1]
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
                self.state.config.policies.target_channel = int(value) if value not in {None, ""} else None
            elif name == "show-ignored":
                self.state.config.policies.show_ignored = bool(value)
            else:
                return "error unknown policy\n"
            self.state.config.save()
            return "ok\n"
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
            base_candidates = [OVERLAY_DIR, DEFAULT_OVERLAY_DIR]
        else:
            base_candidates = [ASSETS_DIR, DEFAULT_OVERLAY_DIR.parent / "assets"]
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
