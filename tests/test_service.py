from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from copy import deepcopy
import pytest
from trade_pricing.errors import DomainError
from trade_pricing.documents import render_quote
from .helpers import *


def test_approval_lifecycle_and_no_delivery_claim(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    q=svc.calculate(AGENT,c["id"],req)
    q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    q=svc.approve(APPROVER,q["id"],Approval(**action_for(q,"approve"),decision="approve",reason="Checked source documents"))
    q=svc.finalize(APPROVER,q["id"],QuoteAction(**action_for(q,"finalize")))
    assert q["state"]=="issued" and q["delivery_status"]=="NOT_SENT"
    assert svc.store.verify_audit(AGENT.tenant)["valid"]


def test_idempotency_replay_and_conflict(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    q=svc.calculate(AGENT,c["id"],req)
    assert svc.calculate(AGENT,c["id"],req)==q
    req2=req.model_copy(update={"proposed_unit_price":"16"})
    with pytest.raises(DomainError,match="request_id"):
        svc.calculate(AGENT,c["id"],req2)


def test_concurrent_retries_create_exactly_one_quote(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    with ThreadPoolExecutor(max_workers=8) as pool:
        result=list(pool.map(lambda _:svc.calculate(AGENT,c["id"],req),range(16)))
    assert len({x["id"] for x in result})==1
    assert len(svc.get_case(AGENT,c["id"])["quotes"])==1


def test_agent_cannot_confirm_cost_or_change_policy(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    for kind,request in [("cost",SaveCostBook(request_id="evil",cost_book=costs_data())),("policy",SavePolicy(request_id="evil",policy=policy_data()))]:
        with pytest.raises(DomainError) as ex:svc.save_snapshot(AGENT,kind,request)
        assert ex.value.status==403


def test_agent_cannot_approve(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    q=svc.calculate(AGENT,c["id"],req);q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    with pytest.raises(DomainError) as ex:svc.approve(AGENT,q["id"],Approval(**action_for(q,"approve"),decision="approve",reason="self"))
    assert ex.value.status==403


def test_separation_of_duties_survives_same_actor_with_different_role(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    q=svc.calculate(AGENT,c["id"],req);q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    with pytest.raises(DomainError) as ex:svc.approve(Actor(AGENT.tenant,AGENT.name,"approver"),q["id"],Approval(**action_for(q,"approve"),decision="approve",reason="self"))
    assert ex.value.code=="SELF_APPROVAL"


def test_customer_document_not_available_before_approval(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    with pytest.raises(DomainError) as ex:svc.public_quote(AGENT,q["id"])
    assert ex.value.code=="NOT_APPROVED"


def test_quote_change_invalidates_old_approval(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    case=case_data();case["quantity"]="150"
    svc.revise_case(AGENT,c["id"],ReviseCase(request_id="revise",expected_revision=1,case=case))
    with pytest.raises(DomainError) as ex:svc.approve(APPROVER,q["id"],Approval(**action_for(q,"approve"),decision="approve",reason="approve old"))
    assert ex.value.code=="QUOTE_STALE"


def test_revoked_cost_invalidates_quote(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    svc.revoke_snapshot(ADMIN,req.cost_snapshot_id,RevokeSnapshot(request_id="revoke",reason="Supplier revised quote"))
    with pytest.raises(DomainError) as ex:svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    assert ex.value.code=="SNAPSHOT_REVOKED"


def test_new_enterprise_policy_revokes_old_policy(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    svc.save_snapshot(ADMIN,"policy",SavePolicy(request_id="new-policy",policy=policy_data()))
    assert svc.get_quote(AGENT,q["id"])["current_blocker"]["code"]=="SNAPSHOT_REVOKED"


def test_expired_quote_cannot_be_finalized(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    q=svc.approve(APPROVER,q["id"],Approval(**action_for(q,"approve"),decision="approve",reason="ok"))
    svc.clock=lambda:NOW+timedelta(days=16)
    with pytest.raises(DomainError) as ex:svc.finalize(APPROVER,q["id"],QuoteAction(**action_for(q,"finalize")))
    assert ex.value.code=="QUOTE_EXPIRED"


def test_hash_binding_prevents_wrong_approval(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    bad=action_for(q);bad["content_hash"]="f"*64
    with pytest.raises(DomainError) as ex:svc.submit(AGENT,q["id"],QuoteAction(**bad))
    assert ex.value.code=="VERSION_CONFLICT"


def test_rejected_quote_requires_new_revision(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    q=svc.approve(APPROVER,q["id"],Approval(**action_for(q,"reject"),decision="reject",reason="Wrong price"))
    with pytest.raises(DomainError):svc.submit(AGENT,q["id"],QuoteAction(**action_for(q,"resubmit")))


def test_cross_tenant_objects_not_disclosed(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    other=Actor("other-factory","other-agent","agent")
    assert svc.list_cases(other)==[] and svc.list_snapshots(other)==[]
    for fn,arg in [(svc.get_case,c["id"]),(svc.get_quote,q["id"])]:
        with pytest.raises(DomainError) as ex:fn(other,arg)
        assert ex.value.status==404


def test_restart_preserves_data_and_idempotency(tmp_path):
    path=tmp_path/"db.sqlite";svc,c,req=setup_service(path);q=svc.calculate(AGENT,c["id"],req)
    reopened=PricingService(Store(path),"Synthetic seller",clock=lambda:NOW)
    assert reopened.calculate(AGENT,c["id"],req)==q
    assert reopened.store.verify_audit(AGENT.tenant)["valid"]


def test_document_excludes_private_cost_margin_and_evidence(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");q=svc.calculate(AGENT,c["id"],req)
    q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    svc.approve(APPROVER,q["id"],Approval(**action_for(q,"approve"),decision="approve",reason="ok"))
    p=svc.public_quote(AGENT,q["id"]); html=render_quote(p)
    for forbidden in ["minimum_policy_unit_price","contribution_margin","cost_snapshot_id","synthetic-fixture","1150.00"]:
        assert forbidden not in html and forbidden not in str(p)
    assert "APPROVED DRAFT" in html


def test_document_escapes_customer_html(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    case=case_data();case["customer_name"]='<img src=x onerror="alert(1)">'
    svc.revise_case(AGENT,c["id"],ReviseCase(request_id="revise",expected_revision=1,case=case))
    req=req.model_copy(update={"expected_revision":2})
    q=svc.calculate(AGENT,c["id"],req);q=svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    svc.approve(APPROVER,q["id"],Approval(**action_for(q,"approve"),decision="approve",reason="ok"))
    html=render_quote(svc.public_quote(AGENT,q["id"]))
    assert "<img" not in html and "&lt;img" in html


def test_tampered_snapshot_rejected(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    with svc.store.connection() as con:con.execute("UPDATE snapshots SET payload='{}' WHERE id=?",(req.cost_snapshot_id,))
    with pytest.raises(DomainError) as ex:svc.calculate(AGENT,c["id"],req)
    assert ex.value.code=="SNAPSHOT_INTEGRITY"


def test_audit_tamper_detection(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    with svc.store.connection() as con:con.execute("UPDATE audit SET payload='{}' WHERE seq=1")
    assert svc.store.verify_audit(AGENT.tenant)["valid"] is False


def test_compare_does_not_invent_bulk_discount(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    out=svc.compare(AGENT,c["id"],CompareInput(expected_revision=1,fx_snapshot_id=req.fx_snapshot_id,policy_snapshot_id=req.policy_snapshot_id,
       scenarios=[{"label":"current","quantity":"100","cost_snapshot_id":req.cost_snapshot_id},
                  {"label":"unsupported bulk","quantity":"1000","cost_snapshot_id":req.cost_snapshot_id}]))
    assert out["scenarios"][0]["result"]["status"]=="calculated"
    assert "QUANTITY_OUTSIDE_CONFIRMED_COST_RANGE" in out["scenarios"][1]["result"]["blockers"]


def test_below_floor_cannot_submit(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite");req=req.model_copy(update={"proposed_unit_price":"10"})
    q=svc.calculate(AGENT,c["id"],req)
    with pytest.raises(DomainError) as ex:svc.submit(AGENT,q["id"],QuoteAction(**action_for(q)))
    assert ex.value.code=="QUOTE_BLOCKED"


def test_historical_replay_matches_without_renewing_approval(tmp_path):
    svc,c,req=setup_service(tmp_path/'db.sqlite');q=svc.calculate(AGENT,c['id'],req)
    svc.clock=lambda:NOW+timedelta(days=365)
    r=svc.replay_quote(AGENT,q['id'])
    assert r['matches'] and r['is_new_approval'] is False
    assert svc.get_quote(AGENT,q['id'])['current_blocker']['code']=='QUOTE_EXPIRED'


def test_explicit_trade_term_scenario_uses_its_own_confirmed_costs(tmp_path):
    svc,c,req=setup_service(tmp_path/'db.sqlite')
    co=costs_data();co.update(incoterm='FCA',fca_delivery='seller_premises',named_place='Synthetic factory gate')
    co['lines'] += [line('load','origin_loading','5'),line('clear','export_clearance','10')]
    snap=svc.save_snapshot(ADMIN,'cost',SaveCostBook(request_id='fca-cost',cost_book=co))
    r=svc.compare(AGENT,c['id'],CompareInput(expected_revision=1,fx_snapshot_id=req.fx_snapshot_id,policy_snapshot_id=req.policy_snapshot_id,
        scenarios=[{'label':'FCA alternative','quantity':'100','cost_snapshot_id':snap['id'],
                    'incoterm':'FCA','fca_delivery':'seller_premises','transport':'road','named_place':'Synthetic factory gate'}]))
    assert r['scenarios'][0]['result']['totals']['known_order_cost']=='1165.00'
    assert r['scenarios'][0]['terms']['incoterm']=='FCA'
