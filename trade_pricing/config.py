from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, model_validator
from .models import Contract, Id, Text, Hash
from .service import Actor
from .errors import DomainError


class Principal(Contract):
    name: Id
    role: Literal["admin", "approver", "agent", "viewer"]
    token_hash: Hash


class ServerConfig(Contract):
    tenant: Id
    seller_name: Text
    database: str
    origin: str = "http://127.0.0.1:8875"
    port: int = Field(default=8875, ge=1024, le=65535, strict=True)
    principals: list[Principal] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_config(self):
        u = urlsplit(self.origin)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError("origin must be an exact HTTP(S) origin without credentials or path")
        if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Non-loopback deployment requires HTTPS")
        if len({p.name for p in self.principals}) != len(self.principals):
            raise ValueError("principal names must be unique")
        if len({p.token_hash for p in self.principals}) != len(self.principals):
            raise ValueError("tokens must not be shared across roles")
        return self

    def authenticate(self, token: str) -> Actor:
        hashed = hashlib.sha256(token.encode()).hexdigest()
        for principal in self.principals:
            if secrets.compare_digest(principal.token_hash, hashed):
                return Actor(self.tenant, principal.name, principal.role)
        raise DomainError("UNAUTHORIZED", "Invalid authentication token", 401)


def read_config(path: str | Path) -> ServerConfig:
    p = Path(path).resolve()
    cfg = ServerConfig.model_validate_json(p.read_text(encoding="utf-8"))
    database = Path(cfg.database)
    if not database.is_absolute():
        cfg = cfg.model_copy(update={"database": str((p.parent / database).resolve())})
    return cfg


def initialize(directory: Path, tenant: str, seller_name: str, port: int = 8875) -> Path:
    """Never overwrite existing keys or databases. No business/test data are created."""
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise DomainError("SETUP_EXISTS", "Refusing to overwrite a non-empty setup directory", 409)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(directory, 0o700)
    names = {"admin": "cost-controller", "approver": "finance-reviewer", "agent": "otto-worker", "viewer": "internal-viewer"}
    token_pairs = [(role, secrets.token_urlsafe(40)) for role in names]
    principals = [Principal(name=names[role], role=role, token_hash=hashlib.sha256(token.encode()).hexdigest()) for role, token in token_pairs]
    cfg = ServerConfig(tenant=tenant, seller_name=seller_name, database="pricing.sqlite", port=port,
                       origin=f"http://127.0.0.1:{port}", principals=principals)
    for role, token in token_pairs:
        path = directory / f"{role}.token"
        with open(path, "x", encoding="utf-8") as f:
            f.write(token + "\n")
        if os.name != "nt":
            os.chmod(path, 0o600)
    config = directory / "server.json"
    with open(config, "x", encoding="utf-8") as f:
        f.write(json.dumps(cfg.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    if os.name != "nt":
        os.chmod(config, 0o600)
    return config
