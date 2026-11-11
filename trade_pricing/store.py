"""Transactional SQLite storage with scoped idempotency and an audit hash chain."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
from typing import Callable, Any
from .core.money import digest, canonical
from .errors import DomainError

SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS cases(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, revision INTEGER NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 hash TEXT NOT NULL, confirmed_by TEXT NOT NULL, created_at TEXT NOT NULL,
 revoked_at TEXT, revoke_reason TEXT);
CREATE TABLE IF NOT EXISTS quotes(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, case_id TEXT NOT NULL REFERENCES cases(id),
 case_revision INTEGER NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL,
 state TEXT NOT NULL, version INTEGER NOT NULL, created_by TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL,
 quote_id TEXT NOT NULL REFERENCES quotes(id), actor TEXT NOT NULL,
 decision TEXT NOT NULL, content_hash TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(
 tenant TEXT NOT NULL, actor TEXT NOT NULL, request_id TEXT NOT NULL,
 request_hash TEXT NOT NULL, response TEXT NOT NULL,
 PRIMARY KEY(tenant,actor,request_id));
CREATE TABLE IF NOT EXISTS audit(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, payload TEXT NOT NULL,
 previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_case_tenant ON cases(tenant,updated_at);
CREATE INDEX IF NOT EXISTS idx_snapshot_tenant ON snapshots(tenant,kind);
CREATE INDEX IF NOT EXISTS idx_quote_case ON quotes(tenant,case_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit(tenant,seq);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.executescript(SCHEMA)
            versions = [r[0] for r in con.execute("SELECT version FROM schema_version")]
            if versions != [1]:
                raise DomainError("SCHEMA_VERSION", "Unsupported database schema", 503)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        try:
            yield con
        finally:
            con.close()

    def transact(self, tenant: str, actor: str, request_id: str, action: str,
                 data: dict, now: str, operation: Callable[[sqlite3.Connection], Any]):
        request_hash = digest({"action": action, "input": data})
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                old = con.execute("SELECT * FROM idempotency WHERE tenant=? AND actor=? AND request_id=?",
                                  (tenant, actor, request_id)).fetchone()
                if old:
                    if old["request_hash"] != request_hash:
                        raise DomainError("IDEMPOTENCY_CONFLICT", "request_id was already used for different input", 409)
                    con.execute("COMMIT")
                    return json.loads(old["response"])
                result = operation(con)
                con.execute("INSERT INTO idempotency VALUES(?,?,?,?,?)",
                            (tenant, actor, request_id, request_hash, canonical(result)))
                prior = con.execute("SELECT hash FROM audit WHERE tenant=? ORDER BY seq DESC LIMIT 1", (tenant,)).fetchone()
                previous = prior["hash"] if prior else "0" * 64
                event = {"actor": actor, "action": action, "request_id": request_id,
                         "request_hash": request_hash, "response_hash": digest(result), "at": now}
                event_hash = digest({"previous_hash": previous, "event": event})
                con.execute("INSERT INTO audit(tenant,payload,previous_hash,hash) VALUES(?,?,?,?)",
                            (tenant, canonical(event), previous, event_hash))
                con.execute("COMMIT")
                return result
            except Exception:
                con.execute("ROLLBACK")
                raise

    def verify_audit(self, tenant: str) -> dict:
        previous = "0" * 64
        count = 0
        with self.connection() as con:
            for row in con.execute("SELECT * FROM audit WHERE tenant=? ORDER BY seq", (tenant,)):
                if row["previous_hash"] != previous or row["hash"] != digest({
                    "previous_hash": previous, "event": json.loads(row["payload"])
                }):
                    return {"valid": False, "first_invalid_sequence": row["seq"], "checked": count}
                previous = row["hash"]
                count += 1
        return {"valid": True, "checked": count, "head_hash": previous,
                "boundary": "hash chain is not external notarization; a database owner can rewrite it"}
