import geopandas as gpd, pandas as pd, json, glob, os, sys
sys.path.insert(0, r"C:\freedo\zn-260930\thinking-drainage-system\tools")
os.chdir(r"C:\freedo\zn-260930")
T = os.environ["TEMP"]
M = gpd.read_file("thinking-drainage-system/cases/ad/ponds_all.geojson")
K = pd.read_pickle(T + "/ponds_pipes.pkl")
M["lon"] = M.geometry.representative_point().x.round(6); M["lat"] = M.geometry.representative_point().y.round(6)
# 建模方式：从各区 *_ponds.json 读
how = {}
for f in glob.glob("thinking-drainage-system/cases/ad/*_ponds.json"):
    d = json.load(open(f, encoding="utf-8")); reg = os.path.basename(f).split("_")[0]
    for p in d["ponds"]:
        w = p["wiring"]; how[p["unitid"]] = (reg, "接管网" if (w.get("pipes")) else "独立蓄水体", p["vol_model_m3"], round(w["depth_m"], 2), p.get("depth_how", "全市 L2 手工接法"))
    for s in d["not_modelled"]:
        how[s["unitid"]] = (reg, "不建", None, None, s["why"])
import pickle, pathlib
wired = {}
for f in pathlib.Path("C:/freedo/swbrain_work/swmm/c2d").glob("ad_net2_*.pkl"):      # 本机跑 network() 留下的缓存：塘有没有接上井
    for q in pickle.loads(f.read_bytes())["ponds"]:
        wired[q["unitid"]] = len(q["inflows"])
rows = []
for _, r in M.iterrows():
    uid = r["pid"] if r["cat"].startswith("已建成塘") else f"PD{int(r['seq']):03d}"
    h = how.get(uid)
    rows.append({"序号": int(r["seq"]), "编号/名称": r["pid"], "模型内编号": uid, "类别": r["cat"], "所在ADM区": r["adm"], "5m内管线数": int(r["pipes_5m"]),
                 "建模方式": (("接管网（%d 个井）" % wired[uid] if wired.get(uid) else "独立蓄水体") if h and h[1] != "不建" else h[1]) if h else ("不在 ADM 14 区内" if not r["adm"] else "?"), "模型库容m³": h[2] if h else None, "模型深度m": h[3] if h else None, "库容/深度取法": h[4] if h else None,
                 "面积m²": r["area_m2"], "来源库容m³": r["vol_m3"], "来源深度m": r["depth_m"], "属性来源": r["attr_src"], "lon": r["lon"], "lat": r["lat"], "所有来源（重叠合并）": r["sources"], "主来源": r["src"]})
out = pd.DataFrame(rows)
summ = out.groupby("类别").agg(去重后个数=("序号", "size"), 在ADM区内=("所在ADM区", lambda s: int((s.str.len() > 0).sum()))).reset_index()
adm = out[out["所在ADM区"].str.len() > 0].groupby(["所在ADM区", "建模方式"]).size().unstack(fill_value=0).reset_index()
with pd.ExcelWriter("蓄水池清单_2026-10-16.xlsx") as w:
    out.to_excel(w, sheet_name="合并清单", index=False); summ.to_excel(w, sheet_name="汇总", index=False); adm.to_excel(w, sheet_name="ADM各区建模", index=False)
print(summ.to_string()); print(adm.to_string())
