import json, threading, time, urllib.request, glob, os
URL='http://192.168.0.133:8731/v1/chat/completions'
OP=urllib.request.build_opener(urllib.request.ProxyHandler({}))
stop=False; samples=[]
def snap():
    freqs=[int(open(f).read()) for f in glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq')]
    freqs.sort()
    hv={}
    for h in glob.glob('/sys/class/drm/card1/device/hwmon/hwmon*/'):
        for f in ('temp1_input','power1_average','power1_input'):
            try: hv[f]=open(h+f).read().strip()
            except Exception: pass
    busy=None
    try: busy=open('/sys/class/drm/card1/device/gpu_busy_percent').read().strip()
    except Exception: pass
    return {'cpu_mhz_min':freqs[0]//1000 if freqs else None,'cpu_mhz_med':freqs[len(freqs)//2]//1000 if freqs else None,
            'cpu_mhz_max':freqs[-1]//1000 if freqs else None,'gpu_busy':busy,**hv}
def sampler():
    while not stop:
        samples.append((time.time(),snap())); time.sleep(0.7)
t=threading.Thread(target=sampler,daemon=True); t.start()
b={'model':'halogen-qwen3.8-flash-next','messages':[{'role':'user','content':'List the numbers from one to forty.'}],
   'temperature':0,'max_tokens':40,'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
r=urllib.request.Request(URL,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'})
t0=time.time()
with OP.open(r,timeout=600) as resp: p=json.loads(resp.read().decode())
stop=True; t.join()
print('WALL',round(time.time()-t0,2),'DECODE_TPS',round(p['usage']['completion_tokens']/(p['usage']['gufo']['decode_ms']/1000),2))
for ts,s in samples: print(round(ts-t0,2),json.dumps(s))
