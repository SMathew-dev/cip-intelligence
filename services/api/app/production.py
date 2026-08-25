from __future__ import annotations

import hmac, json, os, sqlite3, threading, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException


@dataclass(frozen=True)
class Settings:
    runtime_dir: Path
    auth_enabled: bool
    api_keys: dict[str, tuple[str, str]]
    allowed_origins: tuple[str, ...]

    @classmethod
    def load(cls, repo_root: Path) -> "Settings":
        runtime = Path(os.getenv("CIP_RUNTIME_DIR", repo_root / "runtime"))
        runtime.mkdir(parents=True, exist_ok=True)
        enabled = os.getenv("CIP_AUTH_ENABLED", "false").lower() in {"1", "true", "yes"}
        keys: dict[str, tuple[str, str]] = {}
        # Format: key_id:secret:role,key_id2:secret2:role
        for item in os.getenv("CIP_API_KEYS", "").split(","):
            if not item.strip():
                continue
            parts = item.split(":", 2)
            if len(parts) == 3:
                keys[parts[0]] = (parts[1], parts[2])
        origins = tuple(x.strip() for x in os.getenv("CIP_ALLOWED_ORIGINS", "").split(",") if x.strip())
        return cls(runtime, enabled, keys, origins)


class ProductionDB:
    """Durable operational metadata store.

    SQLite is the local/demo backend; the repository schema remains
    PostgreSQL-oriented for production deployment.  ``connect`` is deliberately
    self-healing if the runtime directory/database is removed between requests
    (for example by container volume recreation or test cleanup).
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS audit_events(
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            role TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            detail TEXT
        );
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            payload TEXT NOT NULL,
            result TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS connector_configs(
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode='read_only'),
            enabled INTEGER NOT NULL DEFAULT 1,
            config TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._init()

    def connect(self):
        # A local runtime volume can be recreated while the process remains
        # alive. Re-establish both the parent and tables on every connection;
        # CREATE TABLE IF NOT EXISTS is idempotent and keeps reads from failing
        # because an operational metadata file disappeared.
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(self.path, timeout=5)
            c.row_factory = sqlite3.Row
            c.executescript(self._SCHEMA)
            return c

    def _init(self):
        with self.connect():
            pass

    def audit(self, **kw):
        with self.connect() as c:
            c.execute(
                "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    kw["id"], kw["ts"], kw["actor"], kw["role"], kw["method"],
                    kw["path"], kw["status"], kw["request_id"], kw.get("detail"),
                ),
            )

    def audits(self, limit=100):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM audit_events ORDER BY ts DESC LIMIT ?", (limit,))]

    def create_job(self, kind, payload):
        jid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            c.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
                (jid, kind, "QUEUED", now, now, 0, json.dumps(payload), None, None),
            )
        return jid

    def update_job(self, jid, state, *, result=None, error=None, attempt=False):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            c.execute(
                "UPDATE jobs SET state=?,updated_at=?,attempts=attempts+?,result=?,error=? WHERE id=?",
                (state, now, 1 if attempt else 0, json.dumps(result) if result is not None else None, error, jid),
            )

    def jobs(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def save_connector(self, name, kind, config):
        cid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            c.execute(
                "INSERT INTO connector_configs VALUES(?,?,?,?,?,?,?)",
                (cid, name, kind, "read_only", 1, json.dumps(config), now),
            )
        return cid

    def connectors(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM connector_configs ORDER BY name")]


class JobRunner:
    def __init__(self, db: ProductionDB):
        self.db = db

    def submit(self, kind: str, payload: dict, fn: Callable[[], dict]):
        jid = self.db.create_job(kind, payload)

        def run():
            self.db.update_job(jid, "RUNNING", attempt=True)
            try:
                self.db.update_job(jid, "SUCCEEDED", result=fn())
            except Exception as exc:
                self.db.update_job(jid, "FAILED", error=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=run, daemon=True, name=f"cip-job-{jid[:8]}").start()
        return jid


def identity(settings: Settings, authorization: str | None) -> tuple[str, str]:
    if not settings.auth_enabled:
        return ("local-demo", "admin")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer API key required")
    token = authorization[7:]
    for key_id, (secret, role) in settings.api_keys.items():
        if hmac.compare_digest(token, secret):
            return key_id, role
    raise HTTPException(401, "Invalid API key")


ROLE_RANK = {"viewer": 0, "engineer": 1, "qa": 2, "admin": 3}


def require_role(settings: Settings, authorization: str | None, minimum: str):
    actor, role = identity(settings, authorization)
    if ROLE_RANK.get(role, -1) < ROLE_RANK[minimum]:
        raise HTTPException(403, f"{minimum} role required")
    return actor, role
