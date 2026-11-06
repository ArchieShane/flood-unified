import re, sys, json, pathlib, collections, glob, csv
sys.stdout.reconfigure(encoding="utf-8")
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
T = pathlib.Path(glob.glob(r"C:\Users\admin\AppData\Local\Temp\claude\C--freedo-zn-260930\a789a4c4-e89a-4f5d-9217-6bc78cf4dbee\scratchpad\adm250_post_test_*")[0]) / "testhost" / "ADM_City_61mm1h_250m"
def walk(o, keys, vals):
    if isinstance(o, dict):
        for k, v in o.items():
            if CJK.search(k): keys[k] += 1
            walk(v, keys, vals)
    elif isinstance(o, list):
        for v in o: walk(v, keys, vals)
    elif isinstance(o, str) and CJK.search(o): vals[re.sub(r"[\d,\.]+", "#", o)[:120]] += 1
for p in ["统计/ad_stats.json", "积水点/积水点图层.geojson"]:
    keys, vals = collections.Counter(), collections.Counter()
    walk(json.load(open(T/p, encoding="utf-8")), keys, vals)
    print("==", p); print(" keys:", list(keys)); print(" vals:", list(vals))
for p in ["统计/汇水区汇总.csv"]:
    rows = list(csv.reader(open(T/p, encoding="utf-8-sig")))
    print("==", p); [print("  ", r[:3]) for r in rows]
rows = list(csv.reader(open(T/"积水点/积水点结果表.csv", encoding="utf-8-sig")))
print(rows[0]); print(sorted({r[-1] for r in rows[1:]}), sorted({r[5] for r in rows[1:]}))
print(sorted({p for ro in rows[1:] for p in [ro[2]]}))
# 计算设置.md 行
for ln in open(T/"计算设置.md", encoding="utf-8"): print(ln.strip()[:110])
# 积水点.json 公共
print(open(T/"公共/积水点.json", encoding="utf-8").read(300))
# ponds / pumps json 全部不同的值模板
for f in ["cases/ad250/CITY_ponds.json", "cases/ad250/CITY_existing_pumps.json"]:
    vals = collections.Counter(); walk(json.load(open(f, encoding="utf-8")), collections.Counter(), vals)
    print("==", f, len(vals)); [print("   ", v) for v in vals]
