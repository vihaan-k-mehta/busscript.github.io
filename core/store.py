"""SQLite store for channels, databases, workspaces and settings (schema.sql sits next to this file)."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import ChannelConfig

SCHEMA = Path(__file__).resolve().parent / "schema.sql"  # ships inside the package

DEFAULT_SETTINGS = {
    "mcp.enabled": "false",
    "mcp.allow_transmit": "false",
    "mcp.tools_disabled": "[]",
    "ring_buffer_size": "500000",
}


def default_data_dir() -> Path:
    return Path(os.environ.get("BUSSCRIPT_DATA", Path.home() / "busscript-data"))


class Store:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(exist_ok=True)
        (self.data_dir / "databases").mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.data_dir / "busscript.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA.read_text(encoding="utf-8"))
            for k, v in DEFAULT_SETTINGS.items():
                self.db.execute("insert or ignore into settings(key, value) values (?, ?)", (k, v))
            self.db.commit()

    # settings -------------------------------------------------------------
    def get_settings(self) -> dict:
        with self._lock:
            return {r["key"]: r["value"] for r in self.db.execute("select key, value from settings")}

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute("insert into settings(key, value) values (?, ?) "
                            "on conflict(key) do update set value = excluded.value", (key, str(value)))
            self.db.commit()

    def get_bool(self, key: str) -> bool:
        return self.get_settings().get(key, "false").lower() == "true"

    # channels and databases -----------------------------------------------
    def save_channel(self, cfg: ChannelConfig) -> None:
        with self._lock:
            row = self.db.execute("select id from channels where name = ?", (cfg.name,)).fetchone()
            vals = (cfg.interface, cfg.channel, cfg.bitrate, int(cfg.fd_enabled), cfg.fd_data_bitrate,
                    int(cfg.listen_only))
            if row:
                self.db.execute("update channels set interface=?, channel=?, bitrate=?, fd_enabled=?, "
                                "fd_data_bitrate=?, listen_only=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                                "where id=?", (*vals, row["id"]))
            else:
                self.db.execute("insert into channels(id, name, interface, channel, bitrate, fd_enabled, "
                                "fd_data_bitrate, listen_only) values (?,?,?,?,?,?,?,?)",
                                (secrets.token_hex(8), cfg.name, *vals))
            self.db.commit()

    def delete_channel(self, name: str) -> None:
        with self._lock:
            self.db.execute("delete from channels where name = ?", (name,))
            self.db.commit()

    def load_channels(self) -> list[tuple[ChannelConfig, list[str]]]:
        with self._lock:
            out = []
            for r in self.db.execute("select * from channels order by created_at, name"):
                cfg = ChannelConfig(name=r["name"], interface=r["interface"], channel=r["channel"],
                                    bitrate=r["bitrate"], fd_enabled=bool(r["fd_enabled"]),
                                    fd_data_bitrate=r["fd_data_bitrate"], listen_only=bool(r["listen_only"]))
                dbs = [d["path"] for d in self.db.execute(
                    "select path from databases where channel_id = ? order by created_at", (r["id"],))]
                out.append((cfg, dbs))
            return out

    def add_database(self, channel: str, path: str) -> None:
        with self._lock:
            row = self.db.execute("select id from channels where name = ?", (channel,)).fetchone()
            if row:
                self.db.execute("insert or ignore into databases(id, channel_id, path) values (?,?,?)",
                                (secrets.token_hex(8), row["id"], path))
                self.db.commit()

    def clear_databases(self, channel: str) -> None:
        with self._lock:
            self.db.execute("delete from databases where channel_id = (select id from channels where name = ?)",
                            (channel,))
            self.db.commit()

    # workspaces -----------------------------------------------------------
    def save_workspace(self, name: str, layout: dict) -> None:
        with self._lock:
            self.db.execute(
                "insert into workspaces(id, name, layout_json) values (?,?,?) "
                "on conflict(name) do update set layout_json=excluded.layout_json, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                (secrets.token_hex(8), name, json.dumps(layout)))
            self.db.commit()

    def get_workspace(self, name: str) -> Optional[dict]:
        with self._lock:
            r = self.db.execute("select layout_json from workspaces where name = ?", (name,)).fetchone()
            return json.loads(r["layout_json"]) if r else None

    def list_workspaces(self) -> list[str]:
        with self._lock:
            return [r["name"] for r in self.db.execute("select name from workspaces order by name")]

    # token ----------------------------------------------------------------
    def token(self) -> str:
        p = self.data_dir / "token"
        if p.exists():
            t = p.read_text().strip()
            if t:
                return t
        t = secrets.token_urlsafe(32)
        p.write_text(t)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return t
