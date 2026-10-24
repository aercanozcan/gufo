import json, threading, time, urllib.request, os

URL='http://192.168.0.133:8731/v1/chat/completions'
OP=urllib.request.build_opener(urllib.request.ProxyHandler({}))
stop=False
samples=[]
def card_paths(root='/sys/class/drm'):
    out={}
    for c in os.listdir(root):
        if not c.startswith('card') or '.' in c: continue
        d=os.path.join(root,c,'device')
        try:
            busy=int(open(os.path.join(d,'gpu_busy_percent')).read())
        except Exception:
            continue
        out[c]={'busy':busy}
        for f in ('pp_dpm_sclk','pp_dpm_mclk','power_dpm_force_performance_level','hwmon/hwmon0/temp1_input'):
            try: out[c][f]=open(os.path.join(d,f)).read().strip()
            except Exception: pass
    return out
def sampler():
    while not stop:
        samples.append((time.time(), card_paths()))
        time.sleep(0.5)
t=threading.Thread(target=sampler,daemon=True); t.start()
body={'model':'halogen-qwen3.8-flash-next','messages':[{'role':'user','content':'Write a 200 word plain paragraph about rivers.'}],
      'temperature':0,'max_tokens':96,'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
t0=time.time()
with OP.open(req,timeout=900) as r: p=json.loads(r.read().decode())
stop=True; t.join()
wall=time.time()-t0
print('wall',round(wall,2),'usage',p['usage'],'gufo',p['usage'].get('gufo'))
for ts,c in samples:
    print(round(ts-t0,2), json.dumps(c))
