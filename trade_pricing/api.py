from __future__ import annotations
from pathlib import Path
from urllib.parse import urlsplit
import json
import sqlite3
from typing import Literal
from fastapi import FastAPI, Depends, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import __version__
from .config import ServerConfig
from .errors import DomainError
from .store import Store
from .service import Actor, PricingService
from .models import (NewCase, ReviseCase, SaveCostBook, SaveFxBook, SavePolicy,
                     RevokeSnapshot, Calculation, QuoteAction, Approval, CompareInput, CashPlan,
                     PageQuery, SnapshotQuery, PromoteScenario, WithdrawQuote)
from .documents import render_quote

WEB = Path(__file__).parent / "web"
MAX_BODY = 1_048_576


class BodyLimit:
    """Bound both declared and chunked request bodies before JSON parsing."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        content_length = dict(scope.get("headers", [])).get(b"content-length")
        try:
            declared = int(content_length) if content_length else 0
        except ValueError:
            declared = MAX_BODY + 1
        if declared > MAX_BODY or declared < 0:
            return await JSONResponse({"error": {"code": "BODY_TOO_LARGE"}}, 413)(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message)
            total += len(message.get("body", b""))
            if total > MAX_BODY:
                return await JSONResponse({"error": {"code": "BODY_TOO_LARGE"}}, 413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        i = 0
        async def replay():
            nonlocal i
            if i < len(chunks):
                m = chunks[i]
                i += 1
                return m
            return await receive()
        await self.app(scope, replay, send)


def create_app(config: ServerConfig, service: PricingService | None = None) -> FastAPI:
    svc = service or PricingService(Store(config.database), config.seller_name)
    app = FastAPI(title="Trade Pricing Engine", version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.state.service = svc
    app.add_middleware(BodyLimit)
    hostname = urlsplit(config.origin).hostname
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list({"127.0.0.1", "localhost", "[::1]", hostname}))
    security = HTTPBearer(auto_error=False)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Avoid echoing credentials, full source documents or input data into responses/logs.
        errors = [{"location": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": "VALIDATION", "details": errors}}, 422)

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_error(request, exc):
        return JSONResponse({"error": {"code": "STORAGE_UNAVAILABLE", "message": "Storage unavailable; retry with the same request_id"}}, 503)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") != config.origin.rstrip("/"):
            return JSONResponse({"error": {"code": "ORIGIN_DENIED"}}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        return response

    def actor(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> Actor:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise DomainError("UNAUTHORIZED", "Bearer token required", 401)
        return config.authenticate(credentials.credentials)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "business_data_seeded": False}

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (WEB / "index.html").read_text(encoding="utf-8")

    @app.get("/assets/{name}")
    def assets(name: str):
        if name not in ("app.js", "style.css"):
            raise DomainError("NOT_FOUND", "Asset not found", 404)
        return FileResponse(WEB / name, media_type="text/javascript" if name.endswith(".js") else "text/css")

    @app.get("/v1/me")
    def me(a: Actor = Depends(actor)):
        return {"name": a.name, "role": a.role, "tenant": a.tenant, "seller": config.seller_name}

    @app.get("/v1/openapi")
    def openapi(a: Actor = Depends(actor)):
        return app.openapi()

    @app.get("/v1/cases")
    def cases(a: Actor = Depends(actor), limit: int = Query(50, ge=1, le=100), before: int | None = Query(None, ge=1)):
        return svc.case_page(a, PageQuery(limit=limit, before=before))

    @app.post("/v1/cases", status_code=201)
    def new_case(req: NewCase, a: Actor = Depends(actor)):
        return svc.create_case(a, req)

    @app.get("/v1/cases/{case_id}")
    def get_case(case_id: str, a: Actor = Depends(actor)):
        return svc.get_case(a, case_id)

    @app.put("/v1/cases/{case_id}")
    def revise(case_id: str, req: ReviseCase, a: Actor = Depends(actor)):
        return svc.revise_case(a, case_id, req)

    @app.get("/v1/snapshots")
    def snapshots(a: Actor = Depends(actor), limit: int = Query(50, ge=1, le=100),
                  before: int | None = Query(None, ge=1), kind: Literal["cost", "fx", "policy"] | None = None,
                  active_only: bool = False):
        return svc.snapshot_page(a, SnapshotQuery(limit=limit, before=before, kind=kind, active_only=active_only))

    @app.get("/v1/snapshots/{snapshot_id}")
    def get_snapshot(snapshot_id: str, a: Actor = Depends(actor)):
        return svc.get_snapshot(a, snapshot_id)

    @app.get("/v1/policies/current")
    def current_policy(a: Actor = Depends(actor)):
        return svc.current_policy(a)

    @app.post("/v1/cost-snapshots", status_code=201)
    def cost(req: SaveCostBook, a: Actor = Depends(actor)):
        return svc.save_snapshot(a, "cost", req)

    @app.post("/v1/fx-snapshots", status_code=201)
    def fx(req: SaveFxBook, a: Actor = Depends(actor)):
        return svc.save_snapshot(a, "fx", req)

    @app.post("/v1/policies", status_code=201)
    def policy(req: SavePolicy, a: Actor = Depends(actor)):
        return svc.save_snapshot(a, "policy", req)

    @app.post("/v1/snapshots/{snapshot_id}/revoke")
    def revoke(snapshot_id: str, req: RevokeSnapshot, a: Actor = Depends(actor)):
        return svc.revoke_snapshot(a, snapshot_id, req)

    @app.post("/v1/cases/{case_id}/calculate", status_code=201)
    def calc(case_id: str, req: Calculation, a: Actor = Depends(actor)):
        return svc.calculate(a, case_id, req)

    @app.post("/v1/cases/{case_id}/compare")
    def compare(case_id: str, req: CompareInput, a: Actor = Depends(actor)):
        return svc.compare(a, case_id, req)

    @app.post("/v1/cases/{case_id}/promote-scenario", status_code=201)
    def promote(case_id: str, req: PromoteScenario, a: Actor = Depends(actor)):
        return svc.promote_scenario(a, case_id, req)

    @app.get("/v1/quotes/{quote_id}")
    def get_quote(quote_id: str, a: Actor = Depends(actor)):
        return svc.get_quote(a, quote_id)

    @app.post("/v1/quotes/{quote_id}/submit")
    def submit(quote_id: str, req: QuoteAction, a: Actor = Depends(actor)):
        return svc.submit(a, quote_id, req)

    @app.post("/v1/quotes/{quote_id}/approval")
    def approve(quote_id: str, req: Approval, a: Actor = Depends(actor)):
        return svc.approve(a, quote_id, req)

    @app.post("/v1/quotes/{quote_id}/finalize")
    def finalize(quote_id: str, req: QuoteAction, a: Actor = Depends(actor)):
        return svc.finalize(a, quote_id, req)

    @app.post("/v1/quotes/{quote_id}/withdraw")
    def withdraw(quote_id: str, req: WithdrawQuote, a: Actor = Depends(actor)):
        return svc.withdraw(a, quote_id, req)

    @app.get("/v1/quotes/{quote_id}/replay")
    def replay(quote_id: str, a: Actor = Depends(actor)):
        return svc.replay_quote(a, quote_id)

    @app.get("/v1/quotes/{quote_id}/customer")
    def public(quote_id: str, a: Actor = Depends(actor)):
        return svc.public_quote(a, quote_id)

    @app.get("/v1/quotes/{quote_id}/document", response_class=HTMLResponse)
    def document(quote_id: str, a: Actor = Depends(actor)):
        return render_quote(svc.public_quote(a, quote_id))

    @app.post("/v1/cashflow")
    def cashflow(req: CashPlan, a: Actor = Depends(actor)):
        return svc.cashflow(a, req)

    @app.get("/v1/audit/verify")
    def audit(a: Actor = Depends(actor)):
        return svc.store.verify_audit(a.tenant)

    return app

