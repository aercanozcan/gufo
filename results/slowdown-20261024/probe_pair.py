import json, threading, time, urllib.request, os, subprocess

URL='http://192.168.0.133:8731/v1/chat/completions'
OP=urllib.request.build_opener(urllib.request.ProxyHandler({}))
SSH=['ssh','-F','/home/ercanozcan/Desktop/llm/evox2-cluster-rerun-20261024/.private/ssh_config','-T','-o','BatchMode=yes','ercan@192.168.0.217']
REMOTE='for c in /sys/class/drm/card*/device/gpu_busy_percent; do echo -n "$(basename $(dirname $(dirname $c)))="; cat $c; done; ps -eo pcpu,comm,args --sort=-pcpu | grep -E "gufo serve|podman" | grep -v grep | head -3 | cut -c1-120'
stop=False; samples=[]
def local():
    out={}
    for c in os.listdir('/sys/class/drm'):
        if c.startswith('card') and '.' not in c:
            try: out[c]=int(open(f'/sys/class/drm/{c}/device/gpu_busy_percent').read())
            except Exception: pass
    ps=subprocess.run(['ps','-eo','pcpu,comm,args','--sort=-pcpu'],capture_output=True,text=True).stdout.splitlines()
    top=[l[:110] for l in ps if ('gufo serve' in l or 'podman' in l)][:2]
    return {'gpu':out,'ps':top}
def sampler():
    while not stop:
        t=time.time()
        loc=local()
        try:
            rem=subprocess.run(SSH+[REMOTE],capture_output=True,text=True,timeout=15).stdout.strip().splitlines()
        except Exception as e: rem=[f'err {e}']
        samples.append((t,loc,rem))
        time.sleep(0.4)
t=threading.Thread(target=sampler,daemon=True); t.start()
body={'model':'halogen-qwen3.8-flash-next','messages':[{'role':'user','content':'Name five rivers.'}],
      'temperature':0,'max_tokens':32,'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
t0=time.time()
with OP.open(req,timeout=900) as r: p=json.loads(r.read().decode())
stop=True; t.join()
print('WALL',round(time.time()-t0,2),'USAGE',json.dumps(p['usage']))
for ts,loc,rem in samples:
    print(round(ts-t0,2),'LOCAL',json.dumps(loc),'REMOTE',json.dumps(rem))
