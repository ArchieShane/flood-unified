import re, sys, json, pathlib, collections
sys.stdout.reconfigure(encoding="utf-8")
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
def scan_text(p, maxshow=6):
    n = 0; shown = []
    for i, ln in enumerate(open(p, encoding="utf-8", errors="replace")):
        if CJK.search(ln):
            n += 1
            if len(shown) < maxshow: shown.append((i+1, ln.strip()[:150]))
    return n, shown
W = pathlib.Path(r"C:\freedo\swbrain_work\swmm")
for p in [W/"AX_CITY_M61H1_L2.inp"]:
    print(p.name, scan_text(p))
for p in ["cases/ad250/CITY_ponds.json", "cases/ad250/CITY_existing_pumps.json", "cases/ad250/hotspots_ad.json", "cases/ad250/CITY_domain.geojson"]:
    print(p, scan_text(p))
# json 里的中文键 / 值汇总
def walk(o, keys, vals, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if CJK.search(k): keys[k] += 1
            walk(v, keys, vals)
    elif isinstance(o, list):
        for v in o: walk(v, keys, vals)
    elif isinstance(o, str) and CJK.search(o):
        vals[o[:100]] += 1
for p in ["cases/ad250/CITY_ponds.json", "cases/ad250/CITY_existing_pumps.json", "cases/ad250/hotspots_ad.json", "cases/ad250/CITY_domain.geojson"]:
    keys, vals = collections.Counter(), collections.Counter()
    walk(json.load(open(p, encoding="utf-8")), keys, vals)
    print("==", p, "中文键", len(keys), list(keys)[:12]); print("   中文值", len(vals)); [print("     ", v[:90], c) for v, c in vals.most_common(12)]
