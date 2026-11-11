"""Regression cases from the source review; all business inputs are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from fastapi.testclient import TestClient
import pytest
from trade_pricing.adapters.mcp import ApiClient, TOOLS
from trade_pricing.errors import DomainError
from trade_pricing.documents import render_quote
from .helpers import *
from .test_api import setup_api, auth


def selected(req, **changes):
    scenario = {"label": "Synthetic alternative", "quantity": "150", "cost_snapshot_id": req.cost_snapshot_id}
    scenario.update(changes)
    return PromoteScenario(request_id="select-alternative", expected_revision=1,
        fx_snapshot_id=req.fx_snapshot_id, policy_snapshot_id=req.policy_snapshot_id, scenario=scenario)


@pytest.mark.parametrize("changes", [
    {"quantity": "0"}, {"proposed_unit_price": "0"},
    {"incoterm": "FCA", "named_place": "Test gate", "transport": "road"},
    {"incoterm": "EXW", "named_place": "Test gate", "transport": "road", "fca_delivery": "other_place"},
    {"fca_delivery": "other_place"},
])
def test_invalid_alternatives_return_422_not_500(tmp_path, changes):
    client,svc,c,req = setup_api(tmp_path)
    client = TestClient(client.app, base_url="http://127.0.0.1:8875", raise_server_exceptions=False)
    scenario = {"label": "Invalid synthetic alternative", "quantity": "100", "cost_snapshot_id": req.cost_snapshot_id, **changes}
    body = {"expected_revision": 1, "fx_snapshot_id": req.fx_snapshot_id, "policy_snapshot_id": req.policy_snapshot_id, "scenarios": [scenario]}
    response = client.post(f'/v1/cases/{c["id"]}/compare', headers=auth(), json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] in ("VALIDATION", "INVALID_SCENARIO")


def test_fractional_counted_scenario_returns_422_with_index(tmp_path):
    client,svc,c,req = setup_api(tmp_path)
    case = case_data(); case["unit"] = "piece"
    svc.revise_case(AGENT,c["id"],ReviseCase(request_id="piece-case",expected_revision=1,case=case))
    body = {"expected_revision": 2, "fx_snapshot_id": req.fx_snapshot_id, "policy_snapshot_id": req.policy_snapshot_id,
            "scenarios": [{"label":"Bad count", "quantity":"1.5", "cost_snapshot_id":req.cost_snapshot_id}]}
    response = client.post(f'/v1/cases/{c["id"]}/compare', headers=auth(), json=body)
    assert response.status_code == 422
    assert "scenarios[0]" in response.json()["error"]["message"]


def test_selected_scenario_is_atomic_idempotent_and_requires_new_approval(tmp_path):
    svc,c,req = setup_service(tmp_path/"db.sqlite")
    old = svc.calculate(AGENT,c["id"],req)
    old = svc.submit(AGENT,old["id"],QuoteAction(**action_for(old)))
    old = svc.approve(APPROVER,old["id"],Approval(**action_for(old,"approve-old"),decision="approve",reason="Synthetic approval"))
    selection = selected(req)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: svc.promote_scenario(AGENT,c["id"],selection), range(8)))
    assert len({r["quote"]["id"] for r in results}) == 1
    new = results[0]["quote"]
    assert new["state"] == "draft" and new["case_revision"] == 2
    assert new["case"]["quantity"] == "150"
    assert svc.get_case(AGENT,c["id"])["revision"] == 2
    assert svc.get_quote(AGENT,old["id"])["current_blocker"]["code"] == "QUOTE_STALE"
    with pytest.raises(DomainError) as exc: svc.public_quote(AGENT,new["id"])
    assert exc.value.code == "NOT_APPROVED"
    assert svc.store.verify_audit(AGENT.tenant)["valid"]


@pytest.mark.parametrize("changes", [{"quantity":"201"}, {"proposed_unit_price":"1"}])
def test_blocked_selection_rolls_back_case_quote_and_idempotency(tmp_path, changes):
    svc,c,req = setup_service(tmp_path/"db.sqlite")
    before = svc.store.verify_audit(AGENT.tenant)["head_hash"]
    with pytest.raises(DomainError) as exc: svc.promote_scenario(AGENT,c["id"],selected(req,**changes))
    assert exc.value.code == "SCENARIO_BLOCKED"
    current = svc.get_case(AGENT,c["id"])
    assert current["revision"] == 1 and current["case"]["quantity"] == "100" and current["quotes"] == []
    assert svc.store.verify_audit(AGENT.tenant)["head_hash"] == before
    # A failed transaction must not poison the request id.
    assert svc.promote_scenario(AGENT,c["id"],selected(req))["case_revision"] == 2


def test_selected_scenario_rechecks_revocation_and_case_revision(tmp_path):
    svc,c,req = setup_service(tmp_path/"db.sqlite")
    selection = selected(req)
    svc.revoke_snapshot(ADMIN,req.cost_snapshot_id,RevokeSnapshot(request_id="revoke",reason="Synthetic cost correction"))
    with pytest.raises(DomainError) as exc: svc.promote_scenario(AGENT,c["id"],selection)
    assert exc.value.code == "SNAPSHOT_REVOKED"
    svc.revise_case(AGENT,c["id"],ReviseCase(request_id="revise",expected_revision=1,case=case_data()))
    with pytest.raises(DomainError) as exc: svc.promote_scenario(AGENT,c["id"],selection)
    assert exc.value.code == "VERSION_CONFLICT"


@pytest.mark.parametrize("actor", [VIEWER, APPROVER, Actor("other-tenant", "agent", "agent")])
def test_selection_permissions_and_tenant_isolation(tmp_path, actor):
    svc,c,req = setup_service(tmp_path/"db.sqlite")
    with pytest.raises(DomainError) as exc: svc.promote_scenario(actor,c["id"],selected(req))
    assert exc.value.status in (403,404)
    assert svc.get_case(AGENT,c["id"])["revision"] == 1


def test_snapshot_pages_keep_old_active_policy_and_fx_accessible(tmp_path):
    client,svc,c,req = setup_api(tmp_path)
    for i in range(305):
        svc.save_snapshot(ADMIN,"cost",SaveCostBook(request_id=f"cost-{i}",cost_book=costs_data()))
    seen=[];cursor=None
    while True:
        params={"limit":100}
        if cursor: params["before"]=cursor
        page=client.get('/v1/snapshots',headers=auth(),params=params).json()
        seen.extend(s["id"] for s in page["snapshots"])
        cursor=page["next_cursor"]
        if cursor is None: break
    assert len(seen) == 308 and len(set(seen)) == 308
    assert len(svc.list_snapshots(AGENT)) == 308
    assert req.fx_snapshot_id in seen and req.policy_snapshot_id in seen
    assert client.get('/v1/policies/current',headers=auth()).json()["policy"]["id"] == req.policy_snapshot_id
    fx=client.get('/v1/snapshots',headers=auth(),params={"kind":"fx","active_only":"true"}).json()
    assert [s["id"] for s in fx["snapshots"]] == [req.fx_snapshot_id]
    assert client.get(f'/v1/snapshots/{req.cost_snapshot_id}',headers=auth()).json()["payload"]["product_id"] == "test-sku"
    svc.revoke_snapshot(ADMIN,req.fx_snapshot_id,RevokeSnapshot(request_id="revoke",reason="Synthetic revocation"))
    assert client.get('/v1/snapshots?kind=fx&active_only=true',headers=auth()).json()["snapshots"] == []
    assert svc.snapshot_page(Actor("other","reader","viewer"))["snapshots"] == []
    with pytest.raises(DomainError) as exc: svc.get_snapshot(Actor("other","reader","viewer"),req.cost_snapshot_id)
    assert exc.value.status == 404


def test_case_pagination_stable_when_new_case_inserted(tmp_path):
    svc,c,req=setup_service(tmp_path/'db.sqlite')
    for i in range(4): svc.create_case(AGENT,NewCase(request_id=f'new-{i}',case=case_data()))
    page1=svc.case_page(AGENT,PageQuery(limit=2))
    svc.create_case(AGENT,NewCase(request_id='interleaved',case=case_data()))
    page2=svc.case_page(AGENT,PageQuery(limit=100,before=page1['next_cursor']))
    ids=[r['id'] for r in page1['cases']+page2['cases']]
    assert len(ids)==5 and len(set(ids))==5 and c['id'] in ids


@pytest.mark.parametrize("query", ['limit=0','limit=101','before=0','kind=unknown'])
def test_invalid_pagination_returns_422(tmp_path, query):
    client,*_=setup_api(tmp_path)
    assert client.get('/v1/snapshots?'+query,headers=auth()).status_code == 422


def test_customer_document_retains_revision_without_cost_fields(tmp_path):
    svc,c,req=setup_service(tmp_path/'db.sqlite')
    q=svc.calculate(AGENT,c['id'],req);q=svc.submit(AGENT,q['id'],QuoteAction(**action_for(q)))
    svc.approve(APPROVER,q['id'],Approval(**action_for(q,'approve'),decision='approve',reason='Synthetic check'))
    public=svc.public_quote(AGENT,q['id']);html=render_quote(public)
    assert public['product_revision']=='rev1' and 'Revision rev1' in html
    assert 'cost_book' not in public and 'minimum_policy_unit_price' not in html


@pytest.mark.parametrize("state", ['draft','pending_approval','approved','issued'])
def test_withdrawal_preserves_history_blocks_export_and_survives_restart(tmp_path, state):
    path=tmp_path/'db.sqlite';svc,c,req=setup_service(path)
    q=svc.calculate(AGENT,c['id'],req)
    if state!='draft': q=svc.submit(AGENT,q['id'],QuoteAction(**action_for(q)))
    if state in ('approved','issued'): q=svc.approve(APPROVER,q['id'],Approval(**action_for(q,'approve'),decision='approve',reason='Synthetic check'))
    if state=='issued': q=svc.finalize(APPROVER,q['id'],QuoteAction(**action_for(q,'finalize')))
    request=WithdrawQuote(**action_for(q,'withdraw'),reason='Supplier corrected specification')
    for actor in (AGENT,ADMIN,VIEWER):
        with pytest.raises(DomainError) as exc: svc.withdraw(actor,q['id'],request)
        assert exc.value.status==403
    result=svc.withdraw(APPROVER,q['id'],request)
    assert result['state']=='withdrawn' and result==svc.withdraw(APPROVER,q['id'],request)
    svc=PricingService(Store(path),'Synthetic seller',clock=lambda:NOW)
    stored=svc.get_quote(AGENT,q['id'])
    assert stored['current_blocker']['code']=='QUOTE_WITHDRAWN'
    assert stored['decisions'][-1]['reason']==request.reason
    assert stored['decisions'][-1]['actor']==APPROVER.name
    for method in (svc.public_quote,):
        with pytest.raises(DomainError) as exc: method(AGENT,q['id'])
        assert exc.value.code=='QUOTE_WITHDRAWN'
    with pytest.raises(DomainError):svc.finalize(APPROVER,q['id'],QuoteAction(**action_for(result,'new-finalize')))
    assert svc.replay_quote(AGENT,q['id'])['matches'] and svc.store.verify_audit(AGENT.tenant)['valid']


def test_withdrawal_checks_version_but_allows_expired_quotes(tmp_path):
    svc,c,req=setup_service(tmp_path/'db.sqlite');q=svc.calculate(AGENT,c['id'],req)
    wrong=action_for(q,'bad');wrong['expected_version']=9
    with pytest.raises(DomainError) as exc:svc.withdraw(APPROVER,q['id'],WithdrawQuote(**wrong,reason='Synthetic check'))
    assert exc.value.code=='VERSION_CONFLICT'
    svc.clock=lambda:NOW+timedelta(days=31)
    assert svc.withdraw(APPROVER,q['id'],WithdrawQuote(**action_for(q,'expired'),reason='Expired offer'))['state']=='withdrawn'


def test_mcp_routing_carries_revision_and_pagination(tmp_path):
    svc,c,req=setup_service(tmp_path/'db.sqlite')
    adapter=object.__new__(ApiClient)
    captured=[]
    adapter.call=lambda method,path,data=None: captured.append((method,path,data)) or {}
    adapter.invoke('revise_pricing_case',{'case_id':c['id'],'request_id':'revise','expected_revision':1,'case':case_data()})
    assert captured[-1][0]=='PUT' and captured[-1][2]['expected_revision']==1
    adapter.invoke('promote_pricing_scenario',{'case_id':c['id'],**selected(req).model_dump(mode='json')})
    assert captured[-1][1].endswith('/promote-scenario')
    adapter.invoke('list_cost_evidence',{'kind':'fx','active_only':True,'before':300,'limit':10})
    assert 'before=300' in captured[-1][1] and 'active_only=true' in captured[-1][1]
    assert not any('withdraw' in name or 'approve' in name for name in TOOLS)
