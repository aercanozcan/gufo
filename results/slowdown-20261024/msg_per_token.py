import json, re, subprocess, time, urllib.request

URL='http://192.168.0.133:8731/v1/chat/completions'
OP=urllib.request.build_opener(urllib.request.ProxyHandler({}))
HELPER='/usr/local/lib/evox2-recovery-rank0/scripts/recovery-helper.py'

def counters():
    out=subprocess.run(['sudo','-n',HELPER,'counters'],capture_output=True,text=True).stdout
    tx=0; rx=0; txp=0
    for m in re.finditer(r'data_tx_completed=(\d+)',out): tx+=int(m.group(1))
    for m in re.finditer(r'data_tx_posted=(\d+)',out): txp+=int(m.group(1))
    for m in re.finditer(r'data_rx_completed=(\d+)',out): rx+=int(m.group(1))
    return {'tx_completed':tx,'tx_posted':txp,'rx_completed':rx}

def ask(prompt, max_tokens):
    b={'model':'halogen-qwen3.8-flash-next','messages':[{'role':'user','content':prompt}],
       'temperature':0,'max_tokens':max_tokens,'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
    r=urllib.request.Request(URL,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'})
    t=time.time()
    with OP.open(r,timeout=900) as resp: p=json.loads(resp.read().decode())
    u=p['usage']; g=u['gufo']
    return {'wall':round(time.time()-t,2),'prompt':u['prompt_tokens'],'cached':u.get('cached_tokens',0),
            'completion':u['completion_tokens'],'decode_ms':round(g.get('decode_ms',0),1),
            'rounds':g.get('draft_rounds'),'plan':g.get('execution_plan')}

def measured(prompt, mt):
    before=counters(); r=ask(prompt,mt); after=counters(); time.sleep(0.5)
    after=counters()
    r['msg']={k:after[k]-before[k] for k in before}
    return r

p='The quick brown fox jumps over the lazy dog. Explain the sentence briefly.'
rows=[]
rows.append(measured(p,1))   # warm
rows.append(measured(p,17))
rows.append(measured(p,33))
for r in rows: print(json.dumps(r),flush=True)
open('results/slowdown-20261024/msg_per_token.json','w').write(json.dumps(rows,indent=2))
