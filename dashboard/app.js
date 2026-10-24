'use strict';
const $=id=>document.getElementById(id);
let csrf='', pendingAction='', polling=false, access='token', lastState=null;
const UI_MAX_SNAPSHOT_AGE=11.75;
const fmt=(n,d=1)=>typeof n==='number'&&Number.isFinite(n)?n.toLocaleString(undefined,{maximumFractionDigits:d}):'—';
const gib=n=>typeof n==='number'?`${fmt(n/1073741824)} GiB`:'—';
const age=t=>t?`${Math.max(0,Math.floor(Date.now()/1000-t))}s ago`:'waiting';
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,body){const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);const options=body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body),signal:controller.signal}:{signal:controller.signal};try{const r=await fetch(path,options);const value=await r.json();if(!r.ok){const e=new Error(value.error||value.message||'Request failed');e.status=r.status;e.requiresToken=value.requires_token===true;throw e;}return value;}finally{clearTimeout(timeout);}}
function signedIn(yes){$('login').hidden=yes;$('console').hidden=!yes;$('logout').hidden=!yes||access==='local';}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/login',{token:$('token').value});$('token').value='';$('login-error').textContent='';await refresh();}catch(err){$('login-error').textContent=err.message;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{});}finally{csrf='';signedIn(false);}});
function vital(label,value,percent){const v=node('div',undefined,'vital');v.append(node('span',label),node('strong',value));if(typeof percent==='number'){const bar=node('div',undefined,'bar'),fill=node('i');fill.style.width=`${Math.min(100,Math.max(0,percent))}%`;bar.append(fill);v.append(bar);}return v;}
function temperature(sensors,pattern){const values=sensors.filter(s=>s.unit==='°C'&&pattern.test(s.device)&&Number.isFinite(s.value)).map(s=>s.value);return values.length?Math.max(...values):null;}
function renderHost(h){
 const card=node('article',undefined,'host'),head=node('div',undefined,'panel-head');
 const active=h.service?.ActiveState;
 head.append(node('h2',`Rank ${h.rank}`),node('span',h.error?'Offline':active==='active'?'Running':active==='failed'?'Failed':active==='inactive'?'Stopped':active||'Unknown',h.error||active==='failed'?'bad':active==='active'?'good':'muted'));
 card.append(head);
 if(h.error){card.append(node('p','Host unavailable. Check power and LAN/SSH.','bad'));return card;}
 const used=h.memory.MemTotal-h.memory.MemAvailable,gpu=h.gpus[0]||{},vs=node('div',undefined,'vitals');
 vs.append(vital('CPU',`${fmt(h.cpu.busy_percent)} %`,h.cpu.busy_percent),vital('GPU',`${fmt(gpu.busy_percent)} %`,gpu.busy_percent),vital('Memory',gib(used),100*used/h.memory.MemTotal));
 vs.lastChild?.append(node('small',`of ${gib(h.memory.MemTotal)}`));card.append(vs);
 const temps=node('div',undefined,'sensor-list');
 temps.append(node('span',`CPU ${fmt(temperature(h.sensors,/k10temp|zenpower|coretemp/i))} °C`),node('span',`GPU ${fmt(temperature(h.sensors,/amdgpu/i))} °C`));card.append(temps);
 const cables=node('div',undefined,'cable-list');
 (h.usb4?.cables||[]).forEach((c,i)=>{const row=node('div',undefined,'cable');row.append(node('span',`Cable ${i+1} · ${c.device}`,'muted compact'),node('strong',`RX ${fmt(c.rx_gbps,0)} / TX ${fmt(c.tx_gbps,0)} Gb/s`,c.full_speed?'good':'bad'));if(!c.full_speed)row.append(node('small','Below 40 Gb/s or speed unavailable','bad'));cables.append(row);});
 if((h.usb4?.cables?.length||0)!==2)cables.append(node('p','Two cable links required; missing readings do not pass.','bad compact'));
 card.append(cables);return card;
}
function renderUsb4(hosts){
 const known=hosts.length===2&&[0,1].every(rank=>hosts.some(h=>h.rank===rank&&!h.error&&h.usb4));
 const full=known&&hosts.every(h=>h.usb4.full_speed);
 const rails=full&&hosts.every(h=>h.rails?.length===4&&h.rails.every(r=>r.state==='4: ACTIVE'));
 $('usb4-status').textContent=!known?'Cable speed unverified':full?'Both cables · 40 Gb/s each':'Cables below required speed';
 $('usb4-status').className=`status-pill ${full?'good':'bad'}`;
 $('usb4-detail').hidden=!!rails;
 $('usb4-detail').textContent=!known?'Waiting for current readings from both hosts.':!full?'Both cables require 40 Gb/s RX and TX on both hosts. Stop both, then replug each cable at this computer, one at a time, in the same ports. Once speeds recover, press Recover USB4.':'Cable speeds are correct. RDMA is not ready; press Recover USB4. Cable and rail readiness do not confirm transport health.';
}
function updateReadiness(s){
 const inf=s.inference||{},op=s.operation||{},health=inf.transport_health?.status||'unknown';
 const fresh=typeof s.time==='number'&&Number.isFinite(s.time)&&Date.now()/1000-s.time>=0&&Date.now()/1000-s.time<=UI_MAX_SNAPSHOT_AGE;
 const nativeReady=fresh&&!op.running&&s.bridge_readiness?.ready===true;
 const bridgeReady=s.bridge?.reachable===true;
 const ready=nativeReady&&bridgeReady;
 const bridgeUnavailable=nativeReady&&!bridgeReady;
 const stale=!fresh;
 $('ready').textContent=op.running?(op.action==='recover'?'Recovering USB4…':op.action==='nhi-reset'?'Resetting USB4 NHI…':op.action==='stop'?'Stopping cluster…':op.message?.startsWith('Checking model')?'Checking model files…':'Starting cluster…'):health==='fault'?'Transport fault · Restart both':stale?'Status stale · Restart both':ready?'● API ready':bridgeUnavailable?'Pi bridge unavailable · Start both':(inf.api_ready||inf.ready)&&health!=='ready'?'API ready · transport unverified':(inf.api_ready||inf.ready)?'Cluster unavailable · Restart both':op.outcome==='failed'?'Operation failed':'○ API stopped';
 $('ready').className=`status-pill ${op.running?'':ready?'good':(health==='fault'||stale||s.bridge_readiness?.ready===false||(s.bridge_readiness?.ready===true&&s.bridge?.reachable!==true))?'bad':op.outcome==='failed'?'bad':''}`;
 document.querySelectorAll('[data-action]').forEach(b=>{if(b.dataset.action==='start')b.className=bridgeUnavailable&&!op.running?'restart-prominent':'secondary';if(b.dataset.action==='restart')b.className=(!ready&&!bridgeUnavailable&&!op.running)?'restart-prominent':'secondary';});
}
function render(s){
 lastState=s;csrf=s.csrf;signedIn(true);$('connection').textContent=`Updated ${age(s.time)}`;
 const inf=s.inference||{},m=inf.metrics||{},op=s.operation||{};
 updateReadiness(s);
 $('prefill').textContent=fmt(m['llamacpp:prompt_tokens_seconds']);$('decode').textContent=fmt(m['llamacpp:predicted_tokens_seconds']);
 renderUsb4(s.hosts||[]);$('hosts').replaceChildren(...(s.hosts||[]).map(renderHost));
 $('operation').textContent=op.message||'No operation requested.';$('operation').className=op.outcome==='failed'?'bad':'';
 document.querySelectorAll('[data-action]').forEach(b=>b.disabled=!!s.operation?.running);
}
async function refresh(){if(polling)return;polling=true;try{let state;try{state=await api('/api/state');}catch(e){if(e.status!==401)throw e;await api('/api/session',{});state=await api('/api/state');}access=state.access;render(state);}catch(e){lastState=null;if(e.requiresToken){signedIn(false);$('connection').textContent='Sign in to continue';}else $('connection').textContent='Connection lost · retrying…';renderUsb4([]);$('hosts').replaceChildren();$('ready').textContent='API status unavailable';$('ready').className='status-pill';}finally{polling=false;}}
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>{
 pendingAction=button.dataset.action;
 $('confirm-title').textContent=pendingAction==='recover'?'Recover USB4 on both hosts?':pendingAction==='nhi-reset'?'Reset the USB4 controllers on both hosts?':`${pendingAction[0].toUpperCase()+pendingAction.slice(1)} both hosts?`;
 $('confirm-body').textContent=pendingAction==='recover'?'This interrupts active requests, stops both Gufo services and reloads the pinned USB4 drivers without a password. Both cables must already show 40 Gb/s in each direction. Inference stays stopped. Cable and rail readings alone do not confirm transport or serving health. No automatic reboot.':pendingAction==='nhi-reset'?'Use this only when inference is slow with a fixed ~10 ms per exchange while both cables already show 40 Gb/s. It interrupts active requests, stops both Gufo services, unloads the pinned USB4 drivers, resets the USB4 controllers on both hosts and reloads them without a password. Inference stays stopped afterwards; press Start both when this finishes. No automatic reboot, and it is not retried automatically.':pendingAction==='start'?'This checks the inputs and prepares fresh paired USB4 transport when both services are stopped, then starts inference. When Gufo is ready, it also checks the Pi bridge and its model route. The status will show progress until checks finish. Verification and loading can take several minutes.':'This interrupts active inference requests on both hosts.';
 $('confirm').showModal();
}));
$('confirm').addEventListener('close',async()=>{if($('confirm').returnValue!=='confirm')return;try{await api('/api/control',{action:pendingAction,confirm:'both'});await refresh();}catch(e){$('operation').textContent=e.message;}});
refresh();setInterval(refresh,3000);setInterval(()=>{if(lastState)updateReadiness(lastState);},250);
