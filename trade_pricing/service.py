from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4
import json
from pydantic import ValidationError
from .models import (CaseInput, CostBook, FxBook, Policy, NewCase, ReviseCase, SaveCostBook,
                     SaveFxBook, SavePolicy, RevokeSnapshot, Calculation, QuoteAction, Approval,
                     CompareInput, CashPlan, PromoteScenario, WithdrawQuote, PageQuery, SnapshotQuery)
from .store import Store
from .errors import DomainError
from .core.money import canonical, digest
from .core.calculator import calculate
from .core.cashflow import evaluate_cashflow


@dataclass(frozen=True)
class Actor:
    tenant: str
    name: str
    role: str


def utcnow():
    return datetime.now(timezone.utc)


def require(actor: Actor, *roles: str):
    if actor.role not in roles:
        raise DomainError("FORBIDDEN", "This role cannot perform this operation", 403)


def identifier(prefix):
    return prefix + "_" + uuid4().hex


class PricingService:
    def __init__(self, store: Store, seller_name: str, clock: Callable = utcnow):
        self.store, self.seller_name, self.clock = store, seller_name, clock

    def _mutate(self, actor, request, action, operation):
        return self.store.transact(actor.tenant, actor.name, request.request_id, action,
                                   request.model_dump(mode="json"), self.clock().isoformat(), operation)

    def _case(self, con, actor, case_id):
        row = con.execute("SELECT * FROM cases WHERE id=? AND tenant=?", (case_id, actor.tenant)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "Pricing case not found", 404)
        return dict(row)

    def _snapshot(self, con, actor, snapshot_id, kind, allow_revoked=False):
        row = con.execute("SELECT * FROM snapshots WHERE id=? AND tenant=? AND kind=?",
                          (snapshot_id, actor.tenant, kind)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", f"{kind} snapshot not found", 404)
        value = json.loads(row["payload"])
        if digest(value) != row["hash"]:
            raise DomainError("SNAPSHOT_INTEGRITY", "Snapshot hash mismatch", 409)
        if row["revoked_at"] and not allow_revoked:
            raise DomainError("SNAPSHOT_REVOKED", "A referenced snapshot has been revoked", 409)
        return value

    def list_cases(self, actor):
        """In-process convenience reader; HTTP/MCP use bounded pages."""
        items, before = [], None
        while True:
            page = self.case_page(actor, PageQuery(limit=100, before=before))
            items.extend(page["cases"])
            before = page["next_cursor"]
            if before is None:
                return items

    def case_page(self, actor, query: PageQuery | None = None):
        query = query or PageQuery()
        with self.store.connection() as con:
            rows = con.execute("SELECT rowid AS cursor,* FROM cases WHERE tenant=? AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?",
                               (actor.tenant, query.before, query.before, query.limit + 1)).fetchall()
            return {"cases": [{"id": r["id"], "revision": r["revision"], "case": json.loads(r["payload"])} for r in rows[:query.limit]],
                    "next_cursor": rows[query.limit - 1]["cursor"] if len(rows) > query.limit else None}

    def get_case(self, actor, case_id):
        with self.store.connection() as con:
            r = self._case(con, actor, case_id)
            quotes = con.execute("SELECT id,state,version,hash FROM quotes WHERE tenant=? AND case_id=? ORDER BY rowid DESC",
                                 (actor.tenant, case_id)).fetchall()
            return {"id": r["id"], "revision": r["revision"], "case": json.loads(r["payload"]),
                    "quotes": [dict(x) for x in quotes]}

    def create_case(self, actor: Actor, req: NewCase):
        require(actor, "admin", "agent")
        def action(con):
            if req.case.valid_until <= self.clock():
                raise DomainError("CASE_EXPIRED", "The quote validity must be in the future")
            cid = identifier("case")
            now = self.clock().isoformat()
            payload = req.case.model_dump(mode="json")
            con.execute("INSERT INTO cases VALUES(?,?,?,?,?,?)", (cid, actor.tenant, 1, canonical(payload), now, now))
            return {"id": cid, "revision": 1, "case": payload}
        return self._mutate(actor, req, "case.create", action)

    def revise_case(self, actor, case_id, req: ReviseCase):
        require(actor, "admin", "agent")
        def action(con):
            r = self._case(con, actor, case_id)
            if r["revision"] != req.expected_revision:
                raise DomainError("VERSION_CONFLICT", "Case changed; reload before revising", 409)
            if req.case.valid_until <= self.clock():
                raise DomainError("CASE_EXPIRED", "The quote validity must be in the future")
            revision = r["revision"] + 1
            payload = req.case.model_dump(mode="json")
            con.execute("UPDATE cases SET payload=?,revision=?,updated_at=? WHERE id=? AND tenant=?",
                        (canonical(payload), revision, self.clock().isoformat(), case_id, actor.tenant))
            return {"id": case_id, "revision": revision, "case": payload}
        return self._mutate(actor, req, f"case.revise:{case_id}", action)

    def save_snapshot(self, actor, kind, req):
        require(actor, "admin")
        field = {"cost": "cost_book", "fx": "fx_book", "policy": "policy"}[kind]
        value = getattr(req, field).model_dump(mode="json")
        def action(con):
            sid, now = identifier(kind), self.clock().isoformat()
            if kind == "policy":
                con.execute("UPDATE snapshots SET revoked_at=?,revoke_reason=? WHERE tenant=? AND kind='policy' AND revoked_at IS NULL",
                            (now, "Superseded by new enterprise pricing policy", actor.tenant))
            con.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,NULL,NULL)",
                        (sid, actor.tenant, kind, canonical(value), digest(value), actor.name, now))
            return {"id": sid, "kind": kind, "content_hash": digest(value), "confirmed_by": actor.name}
        return self._mutate(actor, req, f"snapshot.confirm:{kind}", action)

    def list_snapshots(self, actor):
        """In-process convenience reader; HTTP/MCP use bounded pages."""
        items, before = [], None
        while True:
            page = self.snapshot_page(actor, SnapshotQuery(limit=100, before=before))
            items.extend(page["snapshots"])
            before = page["next_cursor"]
            if before is None:
                return items

    def snapshot_page(self, actor, query: SnapshotQuery | None = None):
        query = query or SnapshotQuery()
        with self.store.connection() as con:
            rows = con.execute("""SELECT rowid AS cursor,id,kind,hash,confirmed_by,created_at,revoked_at,payload
                FROM snapshots WHERE tenant=? AND (? IS NULL OR kind=?)
                AND (?=0 OR revoked_at IS NULL) AND (? IS NULL OR rowid<?)
                ORDER BY rowid DESC LIMIT ?""",
                (actor.tenant, query.kind, query.kind, query.active_only, query.before, query.before, query.limit + 1)).fetchall()
            items = []
            for row in rows[:query.limit]:
                value = dict(row)
                value.pop("cursor")
                value["payload"] = self._snapshot(con, actor, row["id"], row["kind"], allow_revoked=True)
                items.append(value)
            return {"snapshots": items,
                    "next_cursor": rows[query.limit - 1]["cursor"] if len(rows) > query.limit else None}

    def get_snapshot(self, actor, sid):
        with self.store.connection() as con:
            row = con.execute("SELECT * FROM snapshots WHERE id=? AND tenant=?", (sid, actor.tenant)).fetchone()
            if row is None:
                raise DomainError("NOT_FOUND", "Snapshot not found", 404)
            return {"id": sid, "kind": row["kind"], "content_hash": row["hash"],
                    "confirmed_by": row["confirmed_by"], "revoked_at": row["revoked_at"],
                    "payload": self._snapshot(con, actor, sid, row["kind"], allow_revoked=True)}

    def current_policy(self, actor):
        page = self.snapshot_page(actor, SnapshotQuery(kind="policy", active_only=True, limit=1))
        return {"policy": page["snapshots"][0] if page["snapshots"] else None,
                "note": "Current means not revoked; evidence validity is checked again when calculating."}

    def revoke_snapshot(self, actor, sid, req: RevokeSnapshot):
        require(actor, "admin")
        def action(con):
            r = con.execute("SELECT id FROM snapshots WHERE id=? AND tenant=?", (sid, actor.tenant)).fetchone()
            if r is None:
                raise DomainError("NOT_FOUND", "Snapshot not found", 404)
            con.execute("UPDATE snapshots SET revoked_at=COALESCE(revoked_at,?),revoke_reason=COALESCE(revoke_reason,?) WHERE id=? AND tenant=?",
                        (self.clock().isoformat(), req.reason, sid, actor.tenant))
            return {"id": sid, "revoked": True}
        return self._mutate(actor, req, f"snapshot.revoke:{sid}", action)

    def _inputs(self, con, actor, cost_id, fx_id, policy_id):
        return (CostBook.model_validate(self._snapshot(con, actor, cost_id, "cost")),
                FxBook.model_validate(self._snapshot(con, actor, fx_id, "fx")),
                Policy.model_validate(self._snapshot(con, actor, policy_id, "policy")))

    def calculate(self, actor, case_id, req: Calculation):
        require(actor, "admin", "agent")
        def action(con):
            r = self._case(con, actor, case_id)
            if r["revision"] != req.expected_revision:
                raise DomainError("VERSION_CONFLICT", "Case revision changed", 409)
            case = CaseInput.model_validate_json(r["payload"])
            return self._create_quote(con, actor, case_id, r["revision"], case, req)
        return self._mutate(actor, req, f"quote.calculate:{case_id}", action)

    def _create_quote(self, con, actor, case_id, revision, case, req):
        costs, fx, policy = self._inputs(con, actor, req.cost_snapshot_id, req.fx_snapshot_id, req.policy_snapshot_id)
        now = self.clock()
        result = calculate(case, costs, fx, policy, req.proposed_unit_price, now)
        payload = {"seller_name": self.seller_name, "case": case.model_dump(mode="json"),
                   "case_revision": revision, "cost_snapshot_id": req.cost_snapshot_id,
                   "fx_snapshot_id": req.fx_snapshot_id, "policy_snapshot_id": req.policy_snapshot_id,
                   "cost_book": costs.model_dump(mode="json"), "fx_book": fx.model_dump(mode="json"),
                   "policy": policy.model_dump(mode="json"), "lead_time_days": costs.lead_time_days,
                   "proposed_unit_price": req.proposed_unit_price, "result": result}
        qid, content_hash = identifier("quote"), digest(payload)
        con.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (qid, actor.tenant, case_id, revision, canonical(payload), content_hash,
                     "draft", 1, actor.name, now.isoformat(), now.isoformat()))
        return {"id": qid, "state": "draft", "version": 1, "content_hash": content_hash, **payload}

    def _scenario_case(self, base, opt, label="scenario"):
        overrides = {"quantity": opt.quantity}
        for key in ("incoterm", "named_place", "transport", "fca_delivery", "payment_terms"):
            if getattr(opt, key) is not None:
                overrides[key] = getattr(opt, key)
        if opt.incoterm is not None and opt.incoterm != "FCA":
            overrides["fca_delivery"] = None
        try:
            return CaseInput.model_validate({**base, **overrides})
        except ValidationError as exc:
            details = "; ".join(e["msg"] for e in exc.errors())
            raise DomainError("INVALID_SCENARIO", f"{label}: {details}", 422) from None

    def promote_scenario(self, actor, case_id, req: PromoteScenario):
        """Revalidate and apply an alternative atomically; never transfers approval."""
        require(actor, "admin", "agent")
        def action(con):
            row = self._case(con, actor, case_id)
            if row["revision"] != req.expected_revision:
                raise DomainError("VERSION_CONFLICT", "Case changed; compare again before selecting", 409)
            case = self._scenario_case(json.loads(row["payload"]), req.scenario)
            revision = row["revision"] + 1
            calculation = Calculation(request_id=req.request_id, expected_revision=revision,
                cost_snapshot_id=req.scenario.cost_snapshot_id, fx_snapshot_id=req.fx_snapshot_id,
                policy_snapshot_id=req.policy_snapshot_id, proposed_unit_price=req.scenario.proposed_unit_price)
            quote = self._create_quote(con, actor, case_id, revision, case, calculation)
            if quote["result"]["blockers"]:
                raise DomainError("SCENARIO_BLOCKED", "; ".join(quote["result"]["blockers"]), 409)
            con.execute("UPDATE cases SET payload=?,revision=?,updated_at=? WHERE id=? AND tenant=?",
                        (canonical(case.model_dump(mode="json")), revision, self.clock().isoformat(), case_id, actor.tenant))
            return {"case_id": case_id, "case_revision": revision, "quote": quote,
                    "approval_required": True, "delivery_status": "NOT_SENT"}
        return self._mutate(actor, req, f"case.promote_scenario:{case_id}", action)

    def compare(self, actor, case_id, req: CompareInput):
        with self.store.connection() as con:
            row = self._case(con, actor, case_id)
            if row["revision"] != req.expected_revision:
                raise DomainError("VERSION_CONFLICT", "Case revision changed", 409)
            base = json.loads(row["payload"])
            rows = []
            for index, opt in enumerate(req.scenarios):
                case = self._scenario_case(base, opt, f"scenarios[{index}]")
                costs, fx, policy = self._inputs(con, actor, opt.cost_snapshot_id, req.fx_snapshot_id, req.policy_snapshot_id)
                rows.append({"label": opt.label, "quantity": opt.quantity,
                             "cost_snapshot_id": opt.cost_snapshot_id, "terms": {k: getattr(case,k) for k in ("incoterm","named_place","transport","fca_delivery","payment_terms")},
                             "result": calculate(case, costs, fx, policy, opt.proposed_unit_price, self.clock())})
            return {"scenarios": rows, "method": "explicit supplied alternatives; no invented volume discount",
                    "case_revision": row["revision"],
                    "promote_to_quote": "Use promote_pricing_scenario with this revision and the selected input; it revalidates evidence and creates a new unapproved draft atomically."}

    def _quote(self, con, actor, qid):
        r = con.execute("SELECT * FROM quotes WHERE id=? AND tenant=?", (qid, actor.tenant)).fetchone()
        if r is None:
            raise DomainError("NOT_FOUND", "Quote not found", 404)
        result = dict(r)
        result["payload"] = json.loads(result["payload"])
        if digest(result["payload"]) != r["hash"]:
            raise DomainError("QUOTE_INTEGRITY", "Quote content hash mismatch", 409)
        return result

    def _fresh(self, con, actor, row):
        if row["state"] == "withdrawn":
            raise DomainError("QUOTE_WITHDRAWN", "Quote withdrawn; customer export and finalization are disabled", 409)
        current = self._case(con, actor, row["case_id"])
        if current["revision"] != row["case_revision"]:
            raise DomainError("QUOTE_STALE", "Case changed after this quote was calculated", 409)
        p = row["payload"]
        for field, kind in [("cost_snapshot_id", "cost"), ("fx_snapshot_id", "fx"), ("policy_snapshot_id", "policy")]:
            self._snapshot(con, actor, p[field], kind)
        if datetime.fromisoformat(p["result"]["valid_until"]) <= self.clock():
            raise DomainError("QUOTE_EXPIRED", "Quote or evidence validity expired", 409)
        if p["result"]["blockers"]:
            raise DomainError("QUOTE_BLOCKED", "; ".join(p["result"]["blockers"]), 409)

    def get_quote(self, actor, qid):
        with self.store.connection() as con:
            row = self._quote(con, actor, qid)
            issue = None
            try:
                self._fresh(con, actor, row)
            except DomainError as exc:
                issue = {"code": exc.code, "message": exc.message}
            decisions = con.execute("SELECT actor,decision,content_hash,reason,created_at FROM approvals WHERE tenant=? AND quote_id=? ORDER BY id",
                                    (actor.tenant, qid)).fetchall()
            return {"id": row["id"], "state": row["state"], "version": row["version"],
                    "content_hash": row["hash"], "current_blocker": issue,
                    "decisions": [dict(d) for d in decisions], **row["payload"]}

    def _check_action(self, row, req):
        if row["version"] != req.expected_version or row["hash"] != req.content_hash:
            raise DomainError("VERSION_CONFLICT", "Quote version/content changed; reload before acting", 409)

    def submit(self, actor, qid, req: QuoteAction):
        require(actor, "admin", "agent")
        def action(con):
            row = self._quote(con, actor, qid)
            self._check_action(row, req)
            if row["state"] != "draft":
                raise DomainError("INVALID_TRANSITION", "Only a draft quote can be submitted", 409)
            self._fresh(con, actor, row)
            return self._transition(con, actor, row, "pending_approval")
        return self._mutate(actor, req, f"quote.submit:{qid}", action)

    def approve(self, actor, qid, req: Approval):
        require(actor, "approver")
        def action(con):
            row = self._quote(con, actor, qid)
            self._check_action(row, req)
            if row["state"] != "pending_approval":
                raise DomainError("INVALID_TRANSITION", "Quote is not pending approval", 409)
            if actor.name == row["created_by"]:
                raise DomainError("SELF_APPROVAL", "A quote creator cannot approve the same quote", 403)
            if req.decision == "approve":
                self._fresh(con, actor, row)
            con.execute("INSERT INTO approvals(tenant,quote_id,actor,decision,content_hash,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                        (actor.tenant, qid, actor.name, req.decision, row["hash"], req.reason, self.clock().isoformat()))
            return self._transition(con, actor, row, "approved" if req.decision == "approve" else "rejected")
        return self._mutate(actor, req, f"quote.approve:{qid}", action)

    def finalize(self, actor, qid, req: QuoteAction):
        require(actor, "approver")
        def action(con):
            row = self._quote(con, actor, qid)
            self._check_action(row, req)
            if row["state"] != "approved":
                raise DomainError("INVALID_TRANSITION", "Only an approved quote can be finalized", 409)
            self._fresh(con, actor, row)
            result = self._transition(con, actor, row, "issued")
            result["delivery_status"] = "NOT_SENT"
            return result
        return self._mutate(actor, req, f"quote.finalize:{qid}", action)

    def withdraw(self, actor, qid, req: WithdrawQuote):
        require(actor, "approver")
        def action(con):
            row = self._quote(con, actor, qid)
            self._check_action(row, req)
            if row["state"] not in ("draft", "pending_approval", "approved", "issued"):
                raise DomainError("INVALID_TRANSITION", "This quote is already rejected or withdrawn", 409)
            # Withdrawal must remain possible even after expiry, revision or evidence revocation.
            con.execute("INSERT INTO approvals(tenant,quote_id,actor,decision,content_hash,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                        (actor.tenant, qid, actor.name, "withdraw", row["hash"], req.reason, self.clock().isoformat()))
            result = self._transition(con, actor, row, "withdrawn")
            result["notice"] = "Previously downloaded files cannot be recalled. Notify recipients separately if needed."
            return result
        return self._mutate(actor, req, f"quote.withdraw:{qid}", action)

    def _transition(self, con, actor, row, state):
        version = row["version"] + 1
        con.execute("UPDATE quotes SET state=?,version=?,updated_at=? WHERE id=? AND tenant=?",
                    (state, version, self.clock().isoformat(), row["id"], actor.tenant))
        return {"id": row["id"], "state": state, "version": version, "content_hash": row["hash"]}

    def public_quote(self, actor, qid):
        with self.store.connection() as con:
            row = self._quote(con, actor, qid)
            self._fresh(con, actor, row)
            if row["state"] not in ("approved", "issued"):
                raise DomainError("NOT_APPROVED", "Only approved quotes can generate customer documents", 409)
            p, q = row["payload"], row["payload"]["case"]
            totals = p["result"]["totals"]
            # Explicit allowlist. Never serialize internal payload to a customer document.
            return {"quote_number": qid, "state": row["state"], "seller": p["seller_name"],
                    "customer": q["customer_name"], "description": q["description"],
                    "product_id": q["product_id"], "product_revision": q["product_revision"],
                    "quantity": q["quantity"], "unit": q["unit"],
                    "currency": q["currency"], "unit_price": totals["unit_price"], "total": totals["revenue"],
                    "incoterm": q["incoterm"], "named_place": q["named_place"], "edition": q["incoterms_edition"],
                    "payment_terms": q["payment_terms"], "lead_time_days": p["lead_time_days"],
                    "valid_until": p["result"]["valid_until"], "delivery_status": "NOT_SENT",
                    "note": "Quotation only. No payment request, bank instructions or contract acceptance is implied."}

    def cashflow(self, actor, req: CashPlan):
        with self.store.connection() as con:
            fx = FxBook.model_validate(self._snapshot(con, actor, req.fx_snapshot_id, "fx"))
            return evaluate_cashflow(req, fx, self.clock())

    def replay_quote(self, actor, qid):
        """Historical replay uses the original explicit clock; not a fresh approval."""
        with self.store.connection() as con:
            row = self._quote(con, actor, qid)
            p = row["payload"]
            if p["result"]["engine_version"] != "0.1.0":
                raise DomainError("ENGINE_VERSION_MISMATCH", "Replay with the archived matching engine release", 409)
            replay = calculate(CaseInput.model_validate(p["case"]), CostBook.model_validate(p["cost_book"]),
                               FxBook.model_validate(p["fx_book"]), Policy.model_validate(p["policy"]),
                               p["proposed_unit_price"], datetime.fromisoformat(p["result"]["calculated_at"]))
            return {"quote_id": qid, "matches": digest(replay) == digest(p["result"]),
                    "original_result_hash": digest(p["result"]), "replayed_result_hash": digest(replay),
                    "historical_clock": p["result"]["calculated_at"], "is_new_approval": False}

