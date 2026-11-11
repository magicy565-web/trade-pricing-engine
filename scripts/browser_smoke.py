#!/usr/bin/env python3
"""Real browser -> local HTTP -> SQLite workflow; isolated synthetic data only.

Requires Playwright and a Chromium binary (CHROMIUM_PATH, default /usr/bin/chromium).
No route mocks, prefilled production records or external messages are used.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from trade_pricing.config import initialize
from tests.helpers import case_data, costs_data, policy_data, NOW
from smoke import fresh
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='verification/browser-smoke.json')
    output=Path(parser.parse_args().output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pricing-browser-synthetic-') as directory:
        td=Path(directory)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        cfg=initialize(td/'private','synthetic-browser','SYNTHETIC TEST — NOT A REAL EXPORTER',port)
        url=f'http://127.0.0.1:{port}'
        with open(td/'server.log','w') as log:
            server=subprocess.Popen([sys.executable,'-m','trade_pricing','serve','--config',str(cfg)],cwd=ROOT,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urlopen(url+'/health',timeout=1) as response:
                            if response.status==200:break
                    except OSError:time.sleep(.1)
                else:raise RuntimeError('Service did not start')

                def call(path,role='agent',body=None):
                    token=(td/'private'/f'{role}.token').read_text().strip()
                    request=Request(url+path,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
                                    data=json.dumps(body).encode() if body is not None else None)
                    with urlopen(request,timeout=10) as response:return json.load(response)

                delta=datetime.now(timezone.utc)-NOW
                case=call('/v1/cases',body={'request_id':'browser-case','case':fresh(case_data(),delta)})
                cost=call('/v1/cost-snapshots','admin',{'request_id':'browser-cost','cost_book':fresh(costs_data(),delta)})
                fx=call('/v1/fx-snapshots','admin',{'request_id':'browser-fx','fx_book':{'target_currency':'USD','rates':[]}})
                policy=call('/v1/policies','admin',{'request_id':'browser-policy','policy':fresh(policy_data(),delta)})
                errors=[];checks=[]
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
                    page=browser.new_page(viewport={'width':1440,'height':1100})
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('dialog',lambda dialog:dialog.accept('Synthetic browser review' if dialog.type=='prompt' else None))
                    page.goto(url,wait_until='networkidle')
                    page.locator('#token-file').set_input_files(td/'private/agent.token')
                    page.get_by_role('button',name='查看案件',exact=True).click()
                    page.locator('#cost-select').select_option(cost['id'])
                    page.locator('#fx-select').select_option(fx['id'])
                    page.locator('#policy-select').select_option(policy['id'])
                    page.locator('#scenario-label').fill('Synthetic 150m option')
                    page.locator('#scenario-quantity').fill('150')
                    page.get_by_role('button',name='加入比较',exact=True).click()
                    page.get_by_role('button',name='比较方案',exact=True).click()
                    page.get_by_role('button',name='选定并生成待审报价',exact=True).click()
                    page.get_by_role('button',name='提交人工审核',exact=True).click()
                    page.get_by_role('button',name='pending_approval · v2',exact=True).wait_for()
                    current=call(f'/v1/cases/{case["id"]}')
                    assert current['revision']==2 and current['case']['quantity']=='150'
                    qid=current['quotes'][0]['id'];checks.append('browser_compare_select_submit')
                    for width,height in [(1440,1100),(390,844)]:
                        page.set_viewport_size({'width':width,'height':height})
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    checks.append('desktop_and_mobile_no_overflow')
                    page.locator('#logout').click()
                    page.locator('#token-file').set_input_files(td/'private/approver.token')
                    page.get_by_role('button',name='查看案件',exact=True).click()
                    page.get_by_role('button',name='pending_approval · v2',exact=True).click()
                    page.get_by_role('button',name='批准此确切版本',exact=True).click()
                    page.get_by_role('button',name='定稿（不会外发）',exact=True).click()
                    page.get_by_role('button',name='issued · v4',exact=True).wait_for()
                    with page.expect_download() as downloaded:
                        page.get_by_role('button',name='导出对客报价 HTML',exact=True).click()
                    document=Path(downloaded.value.path()).read_text()
                    assert 'Revision rev1' in document and 'QUOTATION · NOT SENT' in document
                    assert 'cost_book' not in document and 'minimum_policy_unit_price' not in document
                    checks.append('browser_independent_approval_finalize_download')
                    page.get_by_role('button',name='撤回此报价',exact=True).click()
                    page.get_by_role('button',name='withdrawn · v5',exact=True).wait_for()
                    assert page.get_by_role('button',name='导出对客报价 HTML',exact=True).count()==0
                    assert call(f'/v1/quotes/{qid}')['current_blocker']['code']=='QUOTE_WITHDRAWN'
                    checks.append('browser_withdraw_removes_export')
                    assert not errors,errors
                    browser.close()
                report={'status':'passed','at':datetime.now(timezone.utc).isoformat(),'synthetic_only':True,
                        'mode':'real_browser_to_local_http','checks':checks,'page_errors':errors,
                        'external_messages_sent':0,'openmausbot_host_verified':False}
                output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
            finally:
                server.terminate();server.wait(timeout=10)


if __name__=='__main__':main()
