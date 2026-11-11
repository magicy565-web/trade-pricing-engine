"""ISOLATED SYNTHETIC FIXTURES. Not market prices, customer facts or production seeds."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from trade_pricing.models import *
from trade_pricing.service import Actor, PricingService
from trade_pricing.store import Store

NOW = datetime(2026, 11, 11, 4, 0, tzinfo=timezone.utc)
ADMIN = Actor("factory-test", "cost-controller", "admin")
AGENT = Actor("factory-test", "otto-worker", "agent")
APPROVER = Actor("factory-test", "finance-reviewer", "approver")
VIEWER = Actor("factory-test", "internal-viewer", "viewer")


def evidence(label="synthetic-test"):
    return {"reference": f"synthetic-fixture:{label}", "sha256": sha256(label.encode()).hexdigest(),
            "observed_at": (NOW - timedelta(days=1)).isoformat(),
            "valid_until": (NOW + timedelta(days=30)).isoformat(), "kind": "manual_confirmation"}


def case_data():
    return {"external_ref": "SYNTHETIC-TEST-ONLY", "customer_name": "Test buyer, not a real business",
            "product_id": "test-sku", "product_revision": "rev1", "description": "Synthetic test fabric",
            "quantity": "100", "unit": "meter", "currency": "USD", "incoterm": "EXW",
            "named_place": "Synthetic factory gate", "transport": "road", "payment_terms": "100% prepayment",
            "valid_until": (NOW + timedelta(days=15)).isoformat(), "max_lead_days": 30}


def line(id, category, amount, basis="fixed", currency="USD", **extra):
    return {"id": id, "label": f"Test cost {id}", "category": category, "amount": amount,
            "currency": currency, "basis": basis, "unit": "meter" if basis == "per_unit" else None,
            "payer": "seller", "covers": [], "evidence": evidence(id), **extra}


def costs_data():
    c = case_data()
    return {**{key: c[key] for key in ["product_id", "product_revision", "incoterm", "named_place", "transport", "payment_terms", "unit"]},
            "quantity_min": "50", "quantity_max": "200", "lead_time_days": 20,
            "tax_basis": "net_nonrecoverable_confirmed", "scope_note": "Synthetic test scope; no tax rebate assumed",
            "evidence": evidence("scope"), "lines": [line("product", "product", "10", "per_unit"),
            line("pack", "packaging", "0.5", "per_unit"), line("fixed", "other", "100")]}


def policy_data():
    return {"name": "Synthetic policy", "minimum_margin": "0.1", "target_margin": "0.2",
            "revenue_fee_rate": "0.02", "unit_price_decimals": 4, "fee_note": "Synthetic fee assumptions",
            "evidence": evidence("policy")}


def base_models():
    return CaseInput.model_validate(case_data()), CostBook.model_validate(costs_data()), FxBook(target_currency="USD", rates=[]), Policy.model_validate(policy_data())


def setup_service(path):
    svc = PricingService(Store(path), "Synthetic seller", clock=lambda: NOW)
    c = svc.create_case(AGENT, NewCase(request_id="new-case", case=case_data()))
    cost = svc.save_snapshot(ADMIN, "cost", SaveCostBook(request_id="cost-book", cost_book=costs_data()))
    fx = svc.save_snapshot(ADMIN, "fx", SaveFxBook(request_id="fx-book", fx_book={"target_currency": "USD", "rates": []}))
    policy = svc.save_snapshot(ADMIN, "policy", SavePolicy(request_id="policy-book", policy=policy_data()))
    req = Calculation(request_id="calculate-1", expected_revision=1, cost_snapshot_id=cost["id"], fx_snapshot_id=fx["id"], policy_snapshot_id=policy["id"], proposed_unit_price="15")
    return svc, c, req


def action_for(q, request_id="submit-1"):
    return {"request_id": request_id, "expected_version": q["version"], "content_hash": q["content_hash"]}
