from fastapi.testclient import TestClient
from trade_pricing.api import create_app, MAX_BODY
from trade_pricing.config import ServerConfig, Principal, initialize, read_config
from trade_pricing.errors import DomainError
from hashlib import sha256
from pydantic import ValidationError
import pytest
from .helpers import *


def setup_api(tmp_path):
    svc,c,req=setup_service(tmp_path/"db.sqlite")
    cfg=ServerConfig(tenant=AGENT.tenant,seller_name="Synthetic seller",database=str(tmp_path/"db.sqlite"),
        principals=[Principal(name=a.name,role=a.role,token_hash=sha256((a.role+'-test-token').encode()).hexdigest()) for a in[ADMIN,AGENT,APPROVER,VIEWER]])
    return TestClient(create_app(cfg,svc),base_url="http://127.0.0.1:8875"),svc,c,req


def auth(role="agent"):
    return {"Authorization":f"Bearer {role}-test-token"}


def test_http_end_to_end_lifecycle(tmp_path):
    client,svc,c,req=setup_api(tmp_path)
    r=client.post(f'/v1/cases/{c["id"]}/calculate',headers=auth(),json=req.model_dump(mode="json"));assert r.status_code==201
    q=r.json();r=client.post(f'/v1/quotes/{q["id"]}/submit',headers=auth(),json=action_for(q));assert r.status_code==200;q=r.json()
    r=client.post(f'/v1/quotes/{q["id"]}/approval',headers=auth("approver"),json={**action_for(q,"approval"),"decision":"approve","reason":"Checked"});assert r.status_code==200;q=r.json()
    r=client.get(f'/v1/quotes/{q["id"]}/document',headers=auth());assert r.status_code==200 and '<!doctype html>' in r.text
    r=client.post(f'/v1/quotes/{q["id"]}/finalize',headers=auth("approver"),json=action_for(q,"finalize"));assert r.json()["delivery_status"]=="NOT_SENT"


@pytest.mark.parametrize("path",["/v1/cases","/v1/snapshots","/v1/me","/v1/openapi","/v1/audit/verify"])
def test_protected_endpoints_require_token(tmp_path,path):
    client,*_=setup_api(tmp_path)
    assert client.get(path).status_code==401


def test_bad_token_and_query_token_rejected(tmp_path):
    client,*_=setup_api(tmp_path)
    assert client.get('/v1/cases',headers={"Authorization":"Bearer wrong"}).status_code==401
    assert client.get('/v1/cases?token=admin-test-token').status_code==401


def test_browser_origin_is_checked(tmp_path):
    client,*_=setup_api(tmp_path)
    assert client.get('/v1/cases',headers={**auth(),"Origin":"https://evil.example"}).status_code==403


def test_host_header_dns_rebinding_rejected(tmp_path):
    client,*_=setup_api(tmp_path)
    assert client.get('/v1/cases',headers={**auth(),"Host":"evil.example"}).status_code==400


def test_oversized_request_is_rejected_before_parsing(tmp_path):
    client,*_=setup_api(tmp_path)
    assert client.post('/v1/cases',headers={**auth(),"Content-Type":"application/json"},content=b'x'*(MAX_BODY+1)).status_code==413


def test_unknown_input_fields_are_rejected(tmp_path):
    client,*_=setup_api(tmp_path)
    req={"request_id":"new","case":case_data(),"tenant":"other-factory"}
    assert client.post('/v1/cases',headers=auth(),json=req).status_code==422


def test_viewer_cannot_write(tmp_path):
    client,*_=setup_api(tmp_path)
    assert client.post('/v1/cases',headers=auth("viewer"),json={"request_id":"new","case":case_data()}).status_code==403


def test_agent_cannot_approve_costs(tmp_path):
    client,*_=setup_api(tmp_path)
    assert client.post('/v1/cost-snapshots',headers=auth(),json={"request_id":"new","cost_book":costs_data()}).status_code==403


def test_html_static_and_security_headers(tmp_path):
    client,*_=setup_api(tmp_path)
    response=client.get('/')
    assert response.status_code==200 and '外贸报价决策台' in response.text
    assert response.headers['cache-control']=='no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert client.get('/assets/app.js').status_code==200
    assert client.get('/assets/server.json').status_code==404


def test_setup_no_seed_no_overwrite_and_role_separation(tmp_path):
    p=initialize(tmp_path/'private','factory-test','Real user supplies company name')
    cfg=read_config(p)
    assert len(cfg.principals)==4 and len({p.token_hash for p in cfg.principals})==4
    assert not (tmp_path/'private/pricing.sqlite').exists()
    assert cfg.authenticate((tmp_path/'private/agent.token').read_text().strip()).role=='agent'
    with pytest.raises(DomainError):initialize(tmp_path/'private','factory-test','Other name')


def test_nonlocal_http_config_fails_closed(tmp_path):
    with pytest.raises(ValidationError):ServerConfig(tenant="t",seller_name="s",database="db",origin="http://public.example",
        principals=[Principal(name="test",role="agent",token_hash="0"*64)])
