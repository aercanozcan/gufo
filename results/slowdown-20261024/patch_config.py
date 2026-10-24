import json, os, sys
root = sys.argv[1]
lock = os.path.join(root, 'config/builds.lock.json')
with open(lock) as f: b = json.load(f)
b['gufo_binary'] = 'builds/gufo-perf-dc55ce48-20261024/gufo'
b['gufo_sha256'] = '4e34c79a23bbf2ffd0f1f9989d947e13416b0ca26d784a15892b9a469eef8e82'
b['gufo_revision'] = 'dc55ce483db76715518fc458278bf28b0d832793'
b['build_method'] = 'dc55ce48_perf_ab_20261024'
with open(lock, 'w') as f: json.dump(b, f, indent=2); f.write('\n')
cfg = os.path.join(root, 'config/cluster.json')
with open(cfg) as f: c = json.load(f)
c['cache_namespace'] = 'perf-dc55-20261024'
with open(cfg, 'w') as f: json.dump(c, f, indent=2); f.write('\n')
print('patched', root)
