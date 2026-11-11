'use strict';
// This client never calculates money. The server is the sole authority.
let token='', identity=null, cases=[], snapshots=[], currentCase=null, currentQuote=null;
const $=id=>document.getElementById(id);
let noticeTimer, revisionTarget=null, scenarios=[];

function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);$('notice').style.display='block';clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').style.display='none',12000);}
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function btn(text,fn,cls){const b=el('button',text,cls);b.addEventListener('click',()=>guard(fn,b));return b;}
async function guard(fn,button){if(button)button.disabled=true;try{await fn();}catch(e){notice(e.message,true);}finally{if(button)button.disabled=false;}}
function requestId(){return crypto.randomUUID();}
async function api(path,method='GET',body){const headers={Authorization:'Bearer '+token};if(body)headers['Content-Type']='application/json';let r;try{r=await fetch(path,{method,headers,body:body?JSON.stringify(body):undefined,cache:'no-store'});}catch{throw new Error('网络结果不明。先刷新核对，勿反复建立新的报价或发送请求。');}const text=await r.text();let data;try{data=JSON.parse(text);}catch{data=text;}if(!r.ok)throw new Error(typeof data==='string'?`HTTP ${r.status}`:JSON.stringify(data.error));return data;}
async function readJSON(file){if(!file)throw new Error('请选择文件');if(file.size>1000000)throw new Error('文件超过 1 MB');return JSON.parse(await file.text());}
async function allPages(path,key){
  const items=[];let cursor=null;
  do{const query=new URLSearchParams({limit:'100'});if(cursor!==null)query.set('before',String(cursor));
    const page=await api(path+'?'+query);items.push(...page[key]);cursor=page.next_cursor??null;
  }while(cursor!==null);
  return items;
}
async function refresh(){[cases,snapshots]=await Promise.all([allPages('/v1/cases','cases'),allPages('/v1/snapshots','snapshots')]);renderLists();if(currentCase)await selectCase(currentCase.id);}
function renderLists(){const target=$('cases');target.replaceChildren();if(!cases.length)target.append(el('p','还没有真实询盘。','muted'));for(const c of cases){const item=el('div',undefined,'card');item.append(el('strong',c.case.customer_name),el('small',`${c.case.quantity} ${c.case.unit} · ${c.case.incoterm} · v${c.revision}`),btn('查看案件',()=>selectCase(c.id),'secondary'));target.append(item);}const list=$('snapshot-list');list.replaceChildren();for(const s of snapshots){const item=el('div',undefined,'card');item.append(el('strong',`${s.kind} · ${s.revoked_at?'已停用':'未撤销'}`),el('small',s.id));if(identity.role==='admin'&&!s.revoked_at)item.append(btn('撤销依据',async()=>{const reason=prompt('撤销原因（已引用此依据的报价将不能继续批准或定稿）');if(!reason)return;await api(`/v1/snapshots/${s.id}/revoke`,'POST',{request_id:requestId(),reason});await refresh();},'secondary'));list.append(item);}if(!snapshots.length)list.append(el('p','暂无经成本管理员确认的依据。','muted'));for(const [kind,id]of[['cost','cost-select'],['fx','fx-select'],['policy','policy-select']]){const selected=$(id).value;$(id).replaceChildren();$(id).append(new Option('选择已确认快照',''));for(const s of snapshots.filter(x=>x.kind===kind&&!x.revoked_at))$(id).append(new Option(`${s.payload.product_id||s.payload.name||s.payload.target_currency} · ${s.id}`,s.id));if([...$(id).options].some(x=>x.value===selected))$(id).value=selected;}}
function row(parent,label,value){const r=el('div',undefined,'row');r.append(el('span',label),el('span',value));parent.append(r);}
async function selectCase(id){const previous=currentCase;currentCase=await api(`/v1/cases/${id}`);if(!previous||previous.id!==id||previous.revision!==currentCase.revision){clearScenarios();$('scenario-quantity').value=currentCase.case.quantity;}$('empty').hidden=true;$('case-detail').hidden=false;$('case-title').textContent=currentCase.case.customer_name;$('case-revision').textContent='案件版本 '+currentCase.revision;const s=$('case-summary');s.replaceChildren();for(const [key,label]of[['description','产品'],['product_revision','产品修订'],['quantity','数量'],['unit','单位'],['named_place','指定地点'],['payment_terms','付款条件']])row(s,label,currentCase.case[key]);$('calculator-controls').hidden=!['admin','agent'].includes(identity.role);const ql=$('quote-list');ql.replaceChildren();for(const q of currentCase.quotes){const card=el('div',undefined,'card');card.append(el('small',q.id),btn(`${q.state} · v${q.version}`,()=>selectQuote(q.id),'secondary'));ql.append(card);}if(currentQuote&&currentCase.quotes.some(x=>x.id===currentQuote.id))await selectQuote(currentQuote.id);else{$('quote-detail').replaceChildren();currentQuote=null;}}
async function selectQuote(id){currentQuote=await api(`/v1/quotes/${id}`);const q=currentQuote,root=$('quote-detail');root.replaceChildren();root.append(el('h3','内部报价决策'));if(q.current_blocker)root.append(el('div',`${q.current_blocker.code}: ${q.current_blocker.message}`,'bad'));const r=q.result;if(r.totals){const metrics=el('div',undefined,'metrics');for(const [label,value]of[['对客单价',`${r.currency} ${r.totals.unit_price}`],['订单贡献额',`${r.currency} ${r.totals.contribution}`],['政策底价',`${r.currency} ${r.totals.minimum_policy_unit_price}`]]){const m=el('div',undefined,'metric');m.append(el('small',label),el('strong',value));metrics.append(m);}root.append(metrics);root.append(el('p',`贡献率原始比率：${r.totals.contribution_margin}。不等于会计净利润。`,'muted'));}else root.append(el('div','依据不足，未输出利润数字。','bad'));const actions=el('div',undefined,'actions');const binding=()=>({request_id:requestId(),expected_version:currentQuote.version,content_hash:currentQuote.content_hash});if(q.state==='draft'&&['agent','admin'].includes(identity.role)&&!q.current_blocker)actions.append(btn('提交人工审核',async()=>{await api(`/v1/quotes/${id}/submit`,'POST',binding());await selectCase(currentCase.id);}));if(q.state==='pending_approval'&&identity.role==='approver'){for(const decision of['approve','reject'])actions.append(btn(decision==='approve'?'批准此确切版本':'退回修改',async()=>{const reason=prompt('填写审核意见');if(!reason)return;await api(`/v1/quotes/${id}/approval`,'POST',{...binding(),decision,reason});await selectCase(currentCase.id);},decision==='reject'?'danger':undefined));}if(q.state==='approved'&&identity.role==='approver'&&!q.current_blocker)actions.append(btn('定稿（不会外发）',async()=>{await api(`/v1/quotes/${id}/finalize`,'POST',binding());await selectCase(currentCase.id);}));if(['approved','issued'].includes(q.state)&&!q.current_blocker)actions.append(btn('导出对客报价 HTML',async()=>{const text=await api(`/v1/quotes/${id}/document`);const url=URL.createObjectURL(new Blob([text],{type:'text/html;charset=utf-8'}));const a=el('a');a.href=url;a.download=id+'.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);},'secondary'));if(identity.role==='approver'&&['draft','pending_approval','approved','issued'].includes(q.state))actions.append(btn('撤回此报价',async()=>{
  const reason=prompt('撤回原因（将禁止再次导出；已下载文件需要另行通知接收方）');if(!reason||!reason.trim())return;
  await api(`/v1/quotes/${id}/withdraw`,'POST',{...binding(),reason});await selectCase(currentCase.id);notice('报价已撤回。已下载文件不会被远程删除。');
},'danger'));
root.append(actions);const details=el('details');details.append(el('summary','查看可复算明细 / 依据 / 内容哈希'),el('pre',JSON.stringify(q,null,2),'details'));root.append(details);}
$('token-file').addEventListener('change',()=>guard(async()=>{const f=$('token-file').files[0];if(!f||f.size>1024)throw new Error('无效的密钥文件');token=(await f.text()).trim();identity=await api('/v1/me');$('identity').textContent=`${identity.name} · ${identity.role}`;$('seller').textContent=identity.seller;$('login-panel').hidden=true;$('app').hidden=false;$('logout').hidden=false;$('admin-actions').hidden=identity.role!=='admin';$('new-case').hidden=!['admin','agent'].includes(identity.role);await refresh();}));
$('logout').addEventListener('click',()=>{token='';identity=null;location.reload();});
$('refresh').addEventListener('click',()=>guard(refresh,$('refresh')));
$('new-case').addEventListener('click',()=>$('case-file').click());
$('case-file').addEventListener('change',()=>guard(async()=>{const data=await readJSON($('case-file').files[0]);const created=await api('/v1/cases','POST',{request_id:requestId(),case:data});await refresh();await selectCase(created.id);$('case-file').value='';notice('已创建真实询盘案件');}));
$('import-snapshot').addEventListener('click',()=>$('snapshot-file').click());
$('snapshot-file').addEventListener('change',()=>guard(async()=>{const kind=$('snapshot-kind').value,data=await readJSON($('snapshot-file').files[0]);if(!confirm('确认已核对原始文件、哈希、有效期、金额和适用范围？'))return;const map={cost:['cost-snapshots','cost_book'],fx:['fx-snapshots','fx_book'],policy:['policies','policy']};await api('/v1/'+map[kind][0],'POST',{request_id:requestId(),[map[kind][1]]:data});$('snapshot-file').value='';await refresh();notice('已记录人工确认的快照');}));
$('calculate').addEventListener('click',()=>guard(async()=>{const cost=$('cost-select').value,fx=$('fx-select').value,policy=$('policy-select').value;if(!cost||!fx||!policy)throw new Error('请完整选择成本、汇率和定价政策');const body={request_id:requestId(),expected_revision:currentCase.revision,cost_snapshot_id:cost,fx_snapshot_id:fx,policy_snapshot_id:policy,proposed_unit_price:$('proposed-price').value.trim()||null};const q=await api(`/v1/cases/${currentCase.id}/calculate`,'POST',body);await selectCase(currentCase.id);await selectQuote(q.id);},$('calculate')));


function clearScenarios(){scenarios=[];$('scenario-list').replaceChildren();$('comparison-results').replaceChildren();}
function renderScenarios(){const list=$('scenario-list');list.replaceChildren();scenarios.forEach((s,i)=>{
  const card=el('div',undefined,'card');card.append(el('strong',s.label),el('small',`${s.quantity} ${currentCase.case.unit} · ${s.incoterm} ${s.named_place} · ${s.payment_terms}`),btn('移除',()=>{scenarios.splice(i,1);$('comparison-results').replaceChildren();renderScenarios();},'secondary'));list.append(card);
});}
$('revise-case').addEventListener('click',()=>{revisionTarget={id:currentCase.id,revision:currentCase.revision};$('revision-file').click();});
$('revision-file').addEventListener('change',()=>guard(async()=>{
  const target=revisionTarget;if(!target)throw Error('请先选择原询盘');const data=await readJSON($('revision-file').files[0]);
  if(!confirm('修改后旧版本报价将不能继续审批或导出，是否继续？'))return;
  await api(`/v1/cases/${target.id}`,'PUT',{request_id:requestId(),expected_revision:target.revision,case:data});
  $('revision-file').value='';await refresh();await selectCase(target.id);notice('已更新原案件；新报价需要重新审批。');
}));
$('add-scenario').addEventListener('click',()=>guard(()=>{
  if(scenarios.length>=20)throw Error('一次最多比较 20 个方案');
  const cost=snapshots.find(s=>s.id===$('cost-select').value);if(!cost)throw Error('请先选择该方案的成本快照');
  const scenario={label:$('scenario-label').value.trim()||`方案 ${scenarios.length+1}`,quantity:$('scenario-quantity').value.trim(),cost_snapshot_id:cost.id,proposed_unit_price:$('proposed-price').value.trim()||null};
  for(const key of ['incoterm','named_place','transport','fca_delivery','payment_terms'])scenario[key]=cost.payload[key]??null;
  scenarios.push(scenario);$('comparison-results').replaceChildren();renderScenarios();
}));
$('clear-scenarios').addEventListener('click',clearScenarios);
$('compare-scenarios').addEventListener('click',()=>guard(async()=>{
  if(!scenarios.length)throw Error('请先加入至少一个方案');
  const caseId=currentCase.id,request={expected_revision:currentCase.revision,fx_snapshot_id:$('fx-select').value,policy_snapshot_id:$('policy-select').value,scenarios:structuredClone(scenarios)};
  if(!request.fx_snapshot_id||!request.policy_snapshot_id)throw Error('请选择汇率和定价政策');
  const comparison=await api(`/v1/cases/${caseId}/compare`,'POST',request);
  if(currentCase.id!==caseId||currentCase.revision!==request.expected_revision)return;
  const root=$('comparison-results');root.replaceChildren();
  comparison.scenarios.forEach((item,i)=>{
    const card=el('div',undefined,'card'),r=item.result;card.append(el('strong',item.label));
    card.append(el('small',`${item.quantity} ${currentCase.case.unit} · ${item.terms.incoterm} ${item.terms.named_place} · ${item.terms.payment_terms}`));
    if(r.totals){row(card,'单价',`${r.currency} ${r.totals.unit_price}`);row(card,'订单贡献额',`${r.currency} ${r.totals.contribution}`);}
    if(r.blockers.length)card.append(el('p',r.blockers.join('；'),'bad'));
    else card.append(btn('选定并生成待审报价',async()=>{
      if(!confirm('此操作会更新原案件，使旧版本报价失效，并生成需要重新审批的草稿。是否继续？'))return;
      const result=await api(`/v1/cases/${caseId}/promote-scenario`,'POST',{request_id:requestId(),expected_revision:request.expected_revision,fx_snapshot_id:request.fx_snapshot_id,policy_snapshot_id:request.policy_snapshot_id,scenario:request.scenarios[i]});
      await refresh();await selectCase(caseId);await selectQuote(result.quote.id);notice('已选定方案并生成新草稿，请提交独立审核。');
    }));root.append(card);
  });
},$('compare-scenarios')));
