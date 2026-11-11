#!/usr/bin/env python3
"""Real localhost HTTP + MCP process smoke. Every business record is SYNTHETIC.
Uses a temporary private directory, never the user's production configuration.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from trade_pricing.config import initialize
from tests.helpers import case_data, costs_data, policy_data, NOW


def fresh(value,delta):
    if isinstance(value,dict):return {k:fresh(v,delta) for k,v in value.items()}
    if isinstance(value,list):return [fresh(x,delta) for x in value]
    if isinstance(value,str):
        try:
            if len(value)>20 and 'T' in value and '+' in value:
                return (datetime.fromisoformat(value)+delta).isoformat()
        except ValueError:pass
    return value


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',default='verification/live-smoke.json');args=ap.parse_args()
    output=(ROOT/args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pricing-synthetic-') as td:
        td=Path(td)
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        cfg=initialize(td/'private','synthetic-verification','SYNTHETIC VALIDATION — NOT A REAL EXPORTER',port)
        log=open(td/'server.log','w',encoding='utf-8')
        server=subprocess.Popen([sys.executable,'-m','trade_pricing','serve','--config',str(cfg)],cwd=ROOT,stdout=log,stderr=log)
        url=f'http://127.0.0.1:{port}';checks=[]
        def call(path,role='agent',data=None,method=None):
            token=(td/'private'/f'{role}.token').read_text().strip()
            req=Request(url+path,method=method or ('POST' if data is not None else 'GET'),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
                data=json.dumps(data).encode() if data is not None else None)
            with urlopen(req,timeout=10) as res:
                data=res.read().decode()
                return json.loads(data) if 'application/json' in res.headers['content-type'] else data
        try:
            for _ in range(100):
                try:
                    with urlopen(url+'/health',timeout=1) as r:
                        if r.status==200:break
                except Exception:time.sleep(.1)
            else:raise RuntimeError('Service did not start: '+(td/'server.log').read_text())
            assert call('/v1/cases')['cases']==[];checks.append('empty_database_no_business_seed')
            delta=datetime.now(timezone.utc)-NOW
            case=fresh(case_data(),delta);cost=fresh(costs_data(),delta);policy=fresh(policy_data(),delta)
            c=call('/v1/cases',data={'request_id':'live-case','case':case})
            co=call('/v1/cost-snapshots','admin',{'request_id':'live-cost','cost_book':cost})
            fx=call('/v1/fx-snapshots','admin',{'request_id':'live-fx','fx_book':{'target_currency':'USD','rates':[]}})
            po=call('/v1/policies','admin',{'request_id':'live-policy','policy':policy})
            request={'request_id':'live-calc','expected_revision':1,'cost_snapshot_id':co['id'],'fx_snapshot_id':fx['id'],'policy_snapshot_id':po['id'],'proposed_unit_price':'15'}
            q=call(f"/v1/cases/{c['id']}/calculate",data=request)
            assert q['result']['totals']['contribution']=='320.00';checks.append('real_http_exact_calculation')
            assert call(f"/v1/cases/{c['id']}/calculate",data=request)['id']==q['id'];checks.append('http_idempotency')
            messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'verification','version':'1'}}},
                      {'jsonrpc':'2.0','method':'notifications/initialized'},
                      {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'get_pricing_case','arguments':{'case_id':c['id']}}}]
            mcp=subprocess.run([sys.executable,'-m','trade_pricing','mcp','--url',url,'--token-file',str(td/'private/agent.token')],cwd=ROOT,
                input=''.join(json.dumps(m)+'\n' for m in messages),text=True,capture_output=True,timeout=20,check=True)
            replies=[json.loads(x) for x in mcp.stdout.splitlines()]
            assert len(replies)==3 and len(replies[1]['result']['tools'])==11
            assert replies[2]['result']['structuredContent']['id']==c['id'];checks.append('real_stdio_subprocess_to_http_roundtrip')
            forbidden=subprocess.run([sys.executable,'-m','trade_pricing','mcp','--url',url,'--token-file',str(td/'private/admin.token')],cwd=ROOT,input='',text=True,capture_output=True,timeout=20)
            assert forbidden.returncode!=0;checks.append('mcp_rejects_privileged_token')
            def bind(q,rid):return {'request_id':rid,'expected_version':q['version'],'content_hash':q['content_hash']}
            q=call(f"/v1/quotes/{q['id']}/submit",data=bind(q,'live-submit'))
            q=call(f"/v1/quotes/{q['id']}/approval",'approver',{**bind(q,'live-approve'),'decision':'approve','reason':'Synthetic verification'})
            q=call(f"/v1/quotes/{q['id']}/finalize",'approver',bind(q,'live-finalize'))
            checks.append('real_http_approver_role_approval_and_finalize')
            current=call(f"/v1/quotes/{q['id']}")
            assert current['state']=='issued'
            public=call(f"/v1/quotes/{q['id']}/customer")
            html=call(f"/v1/quotes/{q['id']}/document")
            assert public['delivery_status']=='NOT_SENT' and '1150.00' not in html
            assert call('/v1/audit/verify')['valid'];checks.append('audit_chain_verified')
            server.terminate();server.wait(timeout=10)
            server=subprocess.Popen([sys.executable,'-m','trade_pricing','serve','--config',str(cfg)],cwd=ROOT,stdout=log,stderr=log)
            for _ in range(100):
                try:
                    if call(f"/v1/quotes/{q['id']}")['state']=='issued':break
                except Exception:time.sleep(.1)
            else:raise RuntimeError('Persistent state was not recovered')
            checks.append('process_restart_recovers_issued_quote')
            report={'status':'passed','at':datetime.now(timezone.utc).isoformat(),'synthetic_only':True,
                    'real_network':'localhost HTTP only','mcp_protocol':'2025-11-25 tools-only stdio',
                    'checks':checks,'check_count':len(checks),'external_buyers_contacted':0,
                    'openmausbot_host_verified':False,'github_remote_created':False,
                    'output_sample':{'revenue':'1500.00','cost':'1150.00','revenue_fee':'30.00','contribution':'320.00','currency':'USD'}}
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:
            if server.poll() is None:server.terminate();server.wait(timeout=10)
            log.close()


if __name__=='__main__':main()
