#!/usr/bin/env python3
"""Offline Chromium component rendering of isolated synthetic service snapshots.
No browser network requests; this is NOT a deployed browser end-to-end test.
"""
import json
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.helpers import *
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as td:
    svc,c,req=setup_service(Path(td)/'db.sqlite')
    q=svc.calculate(AGENT,c['id'],req);q=svc.submit(AGENT,q['id'],QuoteAction(**action_for(q)))
    q=svc.approve(APPROVER,q['id'],Approval(**action_for(q,'approval'),decision='approve',reason='Synthetic render validation'))
    q=svc.finalize(APPROVER,q['id'],QuoteAction(**action_for(q,'finalize')))
    routes={'/v1/cases':{'cases':svc.list_cases(AGENT)},'/v1/snapshots':{'snapshots':svc.list_snapshots(AGENT)},
            '/v1/cases/'+c['id']:svc.get_case(AGENT,c['id']),'/v1/quotes/'+q['id']:svc.get_quote(AGENT,q['id'])}
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1100})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        html=(ROOT/'trade_pricing/web/index.html').read_text()
        html=html.replace('<link rel="stylesheet" href="/assets/style.css">','').replace('<script src="/assets/app.js" defer></script>','')
        page.set_content(html)
        page.add_style_tag(content=(ROOT/'trade_pricing/web/style.css').read_text())
        page.evaluate('''routes => {window.__routes=routes;window.fetch=async path=>{if(!(path in routes))throw Error('No offline snapshot for '+path);return {ok:true,text:async()=>JSON.stringify(routes[path])};};}''',routes)
        page.add_script_tag(content=(ROOT/'trade_pricing/web/app.js').read_text())
        page.evaluate('''async data => {identity={name:'SYNTHETIC REVIEWER',role:'approver',seller:'合成测试企业 · 非真实业务'};document.getElementById('login-panel').hidden=true;document.getElementById('app').hidden=false;document.getElementById('new-case').hidden=true;document.getElementById('identity').textContent='离线组件验收 · 合成数据';document.getElementById('seller').textContent=identity.seller;await refresh();await selectCase(data.case);await selectQuote(data.quote);}''',{'case':c['id'],'quote':q['id']})
        for name,w,h in [('desktop',1440,1100),('mobile',390,844)]:
            page.set_viewport_size({'width':w,'height':h})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'verification'/f'ui-{name}.png'),full_page=True)
        assert not errors,errors
        browser.close()
report={'status':'passed','mode':'offline_browser_component_render','synthetic_only':True,'browser_network_used':False,
        'viewports':[{'width':1440,'height':1100},{'width':390,'height':844}],'horizontal_overflow':False,'page_errors':[],
        'not_verified':'Real browser-to-localhost navigation was blocked by environment administrator policy; full browser E2E is not claimed.'}
(ROOT/'verification/ui-render.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
