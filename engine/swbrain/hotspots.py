"""客户热点清单：DMT 三个市政区（ADM 阿布扎比市、AAM 艾因、DRM Dhafra）的 Stormwater Hotspot Analysis 工作簿 → 统一契约 swbrain.hotspots/1。

工作簿结构（2026-09-09 v4.x，Origen Batch 1）：每行一个热点 —— 位置/坐标/重要度/根因；
「预案 A」（设计标准内暴雨）：人力、泵（12/8/6 寸）、罐车数量与预算；「预案 B」（超标暴雨）同类字段（列名带 2/3/4…后缀）；
「IDD 干预计划」：是否计划、设计类别、干预类型、项目阶段、业主（Parsons/AECOM）、现状/目标设计标准、动员与完工日期。
ADM 工作簿的 'ADM Hotspot Inventory' 是全量清单（507），'Exec Sum Input ' 是带人力与预算公式的 251 个子集，按 ID 合并。

在本系统里的用途：
  · 第三道：积水点 300 m 内有客户热点 → 按重要度加权优先级；
  · 第二道：没有泵队台账时，用预案 A 的分片区资源总量生成泵队（驻地取片区内最近民防站）；
  · 第一道：建议点 300 m 内有 IDD 已计划的热点 → 建议卡标注「客户已有 IDD 项目」；
  · 验证：客户热点是现场经验的地面真值，用来算预测侧（世界模型）的命中率。
用法：python -m swbrain.hotspots --dir "D:/世界模型/FloodData_Batch1/Flood Management Data - Origen - Batch1"
"""
from __future__ import annotations
import argparse
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_HOTSPOTS = "swbrain.hotspots/1"
CRIT_ORDER = ["Very High", "High", "Medium", "Low"]
CRIT_EN2ZH = {"Very High": "极高", "High": "高", "Medium": "中", "Low": "低"}
ROOT_CAUSE_ZH = {"capacity": "管网能力不足/需升级", "no_network": "无排水管网", "topography": "地形与道路工程", "other": "其他"}
REGION_NAME = {"ADM": "阿布扎比市", "AAM": "艾因", "DRM": "Dhafra"}


# ---------------------------------------------------------------- 解析
def _num(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("°", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


def _latlon(lat, lon):
    """坐标清洗：去 °、处理「lat,lon」写在一格、范围校验（阿联酋 22–26 N，51–57 E）。"""
    la, lo = _num(lat), _num(lon)
    if np.isnan(la) and isinstance(lat, str) and "," in lat:
        parts = [p.strip() for p in lat.split(",")]
        if len(parts) == 2:
            la, lo2 = _num(parts[0]), _num(parts[1]); lo = lo if not np.isnan(lo) else lo2
    if not (22.0 <= la <= 26.5) or not (51.0 <= lo <= 57.0):
        return np.nan, np.nan
    return la, lo


def _yes(x) -> bool | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    s = str(x).strip().lower()
    if s in ("yes", "y", "true", "1"):
        return True
    if s in ("no", "n", "false", "0", "n/a", "na", "-"):
        return False
    return None


def _crit(x) -> str | None:
    s = str(x or "").strip().lower()
    return {"very high": "Very High", "high": "High", "medium": "Medium", "low": "Low"}.get(s)


def _root_cause(x) -> str:
    s = str(x or "").lower()
    if "absen" in s or "no network" in s:
        return "no_network"
    if "upgrade" in s or "inadequate" in s or "capacity" in s:
        return "capacity"
    if "topograph" in s or "road engineering" in s:
        return "topography"
    return "other"


def _find(hdr: list[str], *cands, startswith=False, contains=False):
    low = [h.lower().strip() for h in hdr]
    for c in cands:
        c = c.lower()
        for i, h in enumerate(low):
            if h == c or (startswith and h.startswith(c)) or (contains and c in h):
                return i
    return None


def _sheet_frame(ws) -> tuple[pd.DataFrame, list[str]]:
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    hi = next(i for i, r in enumerate(rows) if r and any(str(v).strip() == "Hotspot_Location" for v in r if v is not None))
    hdr = [str(h).replace("\n", " ").strip() if h is not None else f"col{i}" for i, h in enumerate(rows[hi])]
    return pd.DataFrame(rows[hi + 1:], columns=hdr), hdr


def _parse_sheet(ws, region: str) -> pd.DataFrame:
    df, hdr = _sheet_frame(ws)
    iid = _find(hdr, "#", "Hotspot_ID"); ilat = _find(hdr, "Latitude"); ilon = _find(hdr, "Longitude")
    if iid is None or ilat is None:
        return pd.DataFrame()
    def col(*names, **kw):
        i = _find(hdr, *names, **kw); return df.iloc[:, i] if i is not None else pd.Series([None] * len(df), index=df.index)
    # 预案 B 的列名带后缀：Manpower_Qty2 / Pumps_Qty4 / Tankers_Qty6
    out = pd.DataFrame({
        "region": region,
        "seq": pd.to_numeric(df.iloc[:, iid], errors="coerce"),
        "area": col("Hotspot_Area").astype(str).str.strip(),
        "location": col("Hotspot_Location").astype(str).str.strip(),
        "criticality": col("Criticality").map(_crit),
        "developer_private": col("Developer_Private_Area").map(_yes),
        "developer_name": col("Developer Name").astype(str).str.strip().replace({"None": None, "N/A": None, "nan": None}),
        "network_available": col("Network_Available").map(_yes),
        "root_cause_raw": col("Root_Cause").astype(str).str.strip(),
        "root_cause_details": col("Root_Cause_Details").astype(str).str.strip().replace({"None": None, "nan": None}),
        "planA_manpower": col("Manpower_Qty").map(_num),
        "planA_pumps": col("Pumps_Qty").map(_num),
        "planA_pump12": col("Diesel Pump 12 inch Qty", contains=True).map(_num),
        "planA_pump8": col("Diesel Pump 8 inch Qty", contains=True).map(_num),
        "planA_pump6": col("Diesel Pump 6 inch Qty", contains=True).map(_num),
        "planA_tankers": (col("Tankers_Qty") if _find(hdr, "Tankers_Qty") is not None else col("Suction Tankers (NO", startswith=True)).map(_num),   # 精确匹配优先，否则 ADM 的 “Suction Tankers (NO'S)”；不能 startswith，会碰到预案 B 的 Tankers_Qty6
        "planB_manpower": col("Manpower_Qty2").map(_num),
        "planB_pumps": col("Pumps_Qty4").map(_num),
        "planB_tankers": col("Tankers_Qty6").map(_num),
        "idd_planned": col("IDD Intervention Planned", "IDD Interventoin Planned").astype(str).str.strip().replace({"None": None, "nan": None}),   # 艾因/Dhafra 表头拼错
        "idd_category": col("IDD_Design_Category").astype(str).str.strip().replace({"None": None, "nan": None}),
        "idd_type": col("Type of Intervention").astype(str).str.strip().replace({"None": None, "nan": None}),
        "idd_stage": col("Project Stage").astype(str).str.strip().replace({"None": None, "nan": None}),
        "idd_owner": col("IDD_Owner_Name").astype(str).str.strip().replace({"None": None, "nan": None}),
        "idd_idas_approved": col("Approval from IDAS", startswith=True).astype(str).str.strip().replace({"None": None, "nan": None}),
        "idd_mobilization": col("Planned_IDD_Mobilization_Date").astype(str).str[:10].replace({"None": None, "nan": None}),
        "idd_completion": col("Planned_IDD_Completion_Date").astype(str).str[:10].replace({"None": None, "nan": None}),
        "design_event_current": col("Hotspot Network Rain Design Event (Current)").astype(str).str.strip().str[:200].replace({"None": None, "nan": None}),
        "design_event_future": col("Hotspot Network Rain Design Event (Future)").astype(str).str.strip().str[:120].replace({"None": None, "nan": None}),
        "idd_budget_note": col("Additional Budget Requested").astype(str).str.strip().str[:120].replace({"None": None, "nan": None}),
        "source_sheet": ws.title.strip(),
    })
    ll = [_latlon(a, b) for a, b in zip(df.iloc[:, ilat], df.iloc[:, ilon] if ilon is not None else [None] * len(df))]
    out["lat"] = [x[0] for x in ll]; out["lon"] = [x[1] for x in ll]
    # 预算：预案 A 块（Manpower_Qty2 之前）里名字含 AED 且不含 Qty/Day/hour 的列求和；预案 B 三列
    ib = _find(hdr, "Manpower_Qty2")
    a_cols = [i for i, h in enumerate(hdr) if "aed" in h.lower() and "qty" not in h.lower() and "/day" not in h.lower() and "per hour" not in h.lower() and (ib is None or i < ib)
              and not h.lower().startswith("total pump active") and not h.lower().startswith("total pump standby") and "cost (aed) -" not in h.lower() and "cost (aed) for" not in h.lower()]
    out["planA_budget_aed"] = df.iloc[:, a_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1) if a_cols else np.nan
    b_cols = [i for i, h in enumerate(hdr) if h in ("Manpower_Budget_AED3", "Pumps_Budget_AED5", "Tankers_Budget_AED7")]
    out["planB_budget_aed"] = df.iloc[:, b_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1) if b_cols else np.nan
    out = out[out["seq"].notna()].copy()
    out["root_cause"] = out["root_cause_raw"].map(_root_cause)
    out["idd_planned_flag"] = out["idd_planned"].map(lambda s: "yes" if str(s).strip().lower() in ("yes", "y") else ("assessing" if "assess" in str(s).lower() else ("no" if str(s).strip().lower() in ("no", "n") else None)))
    return out


def parse_workbook(path: Path) -> pd.DataFrame:
    import openpyxl
    name = path.name.upper()
    region = "ADM" if "ADM" in name else "AAM" if "AAM" in name else "DRM" if "DRM" in name else "UNK"
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    main_sheet = next((s for s in wb.sheetnames if s.strip() in ("ADM Hotspot Inventory", "Analysis")), wb.sheetnames[0])
    base = _parse_sheet(wb[main_sheet], region)
    # ADM：用 'Exec Sum Input ' 补人力/预算/预案 B（按 ID）
    extra = next((s for s in wb.sheetnames if s.strip() == "Exec Sum Input"), None)
    if extra is not None and len(base):
        ex = _parse_sheet(wb[extra], region).set_index("seq")
        base = base.set_index("seq")
        for c in ("planA_manpower", "planA_budget_aed", "planB_manpower", "planB_pumps", "planB_tankers", "planB_budget_aed"):
            if c in ex.columns:
                take = ex[c].reindex(base.index)
                cur = base[c] if c in base.columns else pd.Series(np.nan, index=base.index)
                base[c] = np.where((cur.isna() | (cur == 0)) & take.notna(), take, cur)
        base = base.reset_index()
    base["hotspot_id"] = [f"{region}-{int(s)}" for s in base["seq"]]
    base["source_file"] = path.name
    return base


def parse_dir(d: Path) -> pd.DataFrame:
    frames = [parse_workbook(p) for p in sorted(Path(d).glob("*.xlsx")) if not p.name.startswith("~$")]
    df = pd.concat([f for f in frames if len(f)], ignore_index=True)
    return df


# ---------------------------------------------------------------- 坐标修正：客户 KMZ（「506 Hot Spots AUH.kmz」）
def kmz_coords(path) -> pd.DataFrame:
    """读客户的 506 热点 KMZ：SimpleData م（序号）/ الوصف（描述）/ المركز（中心）/ الاولوية（优先级），Point 坐标取几何。返回 seq, lon, lat, desc_ar, center_ar, priority_ar。"""
    import zipfile
    from lxml import etree
    z = zipfile.ZipFile(path)
    kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), "doc.kml")
    root = etree.fromstring(z.read(kml), etree.XMLParser(recover=True, huge_tree=True))
    ns = root.nsmap.get(None) or "http://www.opengis.net/kml/2.2"; K = {"k": ns}
    rows = []
    for pm in root.iter("{%s}Placemark" % ns):
        if pm.find(".//k:Point", K) is None:
            continue
        sd = {x.get("name"): (x.text or "").strip() for x in pm.iterfind(".//k:SimpleData", K)}
        seq = sd.get("م") or sd.get("#") or sd.get("ID") or pm.findtext("k:name", default="", namespaces=K)
        try:
            seq = int(str(seq).strip())
        except (TypeError, ValueError):
            continue
        c = pm.findtext(".//k:coordinates", default="", namespaces=K).strip().split(",")
        try:
            lon, lat = float(c[0]), float(c[1])
        except (IndexError, ValueError):
            continue
        rows.append({"seq": seq, "lon": lon, "lat": lat, "desc_ar": sd.get("الوصف"), "center_ar": sd.get("المركز"), "priority_ar": sd.get("الاولوية")})
    return pd.DataFrame(rows).drop_duplicates("seq")


def apply_kmz_coords(df: pd.DataFrame, path, region: str = "ADM") -> tuple[pd.DataFrame, dict]:
    """用 KMZ 的坐标覆盖 region 的热点坐标（按序号），原表坐标留在 lat_xlsx / lon_xlsx，coord_source 标 kmz / xlsx。
    背景：客户 v4.4 工作簿里 ADM 表的经纬度整体错了一行（第 n 行填的是第 n+1 个热点的坐标），KMZ 是修正后的定位。返回 (df, 核对统计)。"""
    k = kmz_coords(path)
    df = df.copy()
    if "lat_xlsx" not in df.columns:
        df["lat_xlsx"] = df["lat"]; df["lon_xlsx"] = df["lon"]
    if "coord_source" not in df.columns:
        df["coord_source"] = "xlsx"
    m = (df["region"] == region)
    kk = k.set_index("seq")
    seqs = pd.to_numeric(df.loc[m, "seq"], errors="coerce")
    hit = seqs.map(lambda v: v in kk.index if pd.notna(v) else False)
    idx = df.index[m][hit.values]
    # 覆盖前先量偏差
    old = df.loc[idx, ["lon", "lat"]].astype(float).values
    new = kk.loc[seqs[hit.values].astype(int).values, ["lon", "lat"]].values
    d = np.hypot((old[:, 0] - new[:, 0]) * 111000 * np.cos(np.radians(24.4)), (old[:, 1] - new[:, 1]) * 111000)
    d = d[np.isfinite(d)]
    # 错行判定：原表第 n 行坐标 ≈ KMZ 第 n+1 个
    shifted = 0
    for i, sq in zip(idx, seqs[hit.values].astype(int).values):
        if (sq + 1) in kk.index and pd.notna(df.at[i, "lon_xlsx"]):
            dx = np.hypot((float(df.at[i, "lon_xlsx"]) - kk.at[sq + 1, "lon"]) * 111000 * np.cos(np.radians(24.4)), (float(df.at[i, "lat_xlsx"]) - kk.at[sq + 1, "lat"]) * 111000)
            shifted += int(dx < 30)
    df.loc[idx, "lon"] = new[:, 0]; df.loc[idx, "lat"] = new[:, 1]; df.loc[idx, "coord_source"] = "kmz"
    for c in ("desc_ar", "center_ar", "priority_ar"):
        df.loc[idx, c] = kk.loc[seqs[hit.values].astype(int).values, c].values
    stat = {"kmz_points": int(len(k)), "region": region, "matched": int(len(idx)), "unmatched_in_xlsx": int(m.sum() - len(idx)),
            "moved_median_m": (round(float(np.median(d)), 0) if len(d) else None), "moved_p90_m": (round(float(np.quantile(d, 0.9)), 0) if len(d) else None),
            "moved_gt_200m": int((d > 200).sum()), "xlsx_row_shift_plus1": shifted}
    return df, stat


# ---------------------------------------------------------------- 契约
def to_contract(df: pd.DataFrame, source_note: str = "") -> dict:
    recs = json.loads(df.replace({np.nan: None}).to_json(orient="records", force_ascii=False))
    for r in recs:
        for k in ("planA_manpower", "planA_pumps", "planA_pump12", "planA_pump8", "planA_pump6", "planA_tankers", "planB_manpower", "planB_pumps", "planB_tankers"):
            if r.get(k) is not None:
                r[k] = int(round(r[k]))
    return {"schema": SCHEMA_HOTSPOTS, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source_note,
            "regions": {k: int(v) for k, v in df["region"].value_counts().items()}, "n": int(len(df)), "hotspots": recs}


def write_contract(df: pd.DataFrame, path: Path, source_note: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_contract(df, source_note), ensure_ascii=False), encoding="utf-8")
    return path


def load(path: Path) -> pd.DataFrame | None:
    p = Path(path)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA_HOTSPOTS:
        raise ValueError(f"{p}: schema 应为 {SCHEMA_HOTSPOTS}")
    df = pd.DataFrame(d["hotspots"])
    for c in ("lat", "lon", "lat_xlsx", "lon_xlsx", "planA_pumps", "planA_pump12", "planA_pump8", "planA_pump6", "planA_tankers", "planA_manpower", "planA_budget_aed", "planB_budget_aed"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df.attrs["source"] = d.get("source"); df.attrs["generated_at"] = d.get("generated_at")
    return df


def example() -> dict:
    df = pd.DataFrame([
        {"region": "ADM", "seq": 1, "hotspot_id": "ADM-1", "area": "Example Area", "location": "Example underpass", "lat": 24.45, "lon": 54.60, "criticality": "Very High", "developer_private": False,
         "developer_name": None, "network_available": True, "root_cause_raw": "The network needs an upgrade", "root_cause": "capacity", "root_cause_details": None,
         "planA_manpower": 4, "planA_pumps": 3, "planA_pump12": 1, "planA_pump8": 0, "planA_pump6": 2, "planA_tankers": 1, "planA_budget_aed": 250000.0,
         "planB_manpower": 4, "planB_pumps": 3, "planB_tankers": 1, "planB_budget_aed": 250000.0, "idd_planned": "Yes", "idd_planned_flag": "yes", "idd_category": "Detailed", "idd_type": "Permanent",
         "idd_stage": "Contractor Design Phase", "idd_owner": "Consultant A", "idd_idas_approved": "No", "idd_mobilization": "2026-10-01", "idd_completion": "2027-01-31",
         "design_event_current": "1-in-5 yr", "design_event_future": "1-in-10 yr + 20% climate change factor", "idd_budget_note": None, "source_sheet": "example", "source_file": "example.xlsx"},
    ])
    return to_contract(df, "example")


# ---------------------------------------------------------------- 在系统里怎么用
def _xy(lon, lat):
    k = np.cos(np.radians(24.4)) * 111000.0
    return np.c_[np.asarray(lon, float) * k, np.asarray(lat, float) * 111000.0]


def match(lon, lat, hs: pd.DataFrame, radius_m: float) -> tuple[np.ndarray, np.ndarray]:
    """每个点最近的客户热点（半径内），返回 (热点行号 或 -1, 距离 m)。"""
    from scipy.spatial import cKDTree
    h = hs.dropna(subset=["lon", "lat"])
    if not len(h) or not len(lon):
        return np.full(len(lon), -1), np.full(len(lon), np.inf)
    d, j = cKDTree(_xy(h["lon"].values, h["lat"].values)).query(_xy(lon, lat), distance_upper_bound=radius_m)
    idx = np.where(np.isfinite(d), h.index.values[np.minimum(j, len(h) - 1)], -1)
    return idx, d


def priority_weights(lon, lat, hs: pd.DataFrame | None, cfg: dict) -> tuple[np.ndarray, list, list]:
    """积水点的客户热点加权：半径内最近热点的重要度权重；返回 (权重, 热点 id 列表, 重要度列表)。"""
    n = len(lon)
    if hs is None or not len(hs):
        return np.zeros(n), [None] * n, [None] * n
    H = cfg["hotspots"]; W = H["criticality_weight"]
    idx, _ = match(lon, lat, hs, float(H["match_radius_m"]))
    w = np.array([float(W.get(hs.loc[i, "criticality"], 1.0)) if i >= 0 else 0.0 for i in idx])
    ids = [hs.loc[i, "hotspot_id"] if i >= 0 else None for i in idx]
    crit = [hs.loc[i, "criticality"] if i >= 0 else None for i in idx]
    return w, ids, crit


def annotate_proposals(props: pd.DataFrame, hs: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    """建议点 300 m 内的客户热点：标 client_hotspot_*，IDD 已计划的另标注。"""
    if hs is None or not len(hs) or not len(props) or "lon" not in props.columns:
        return props
    p = props.copy()
    ok = p["lon"].notna() & p["lat"].notna()
    idx = np.full(len(p), -1); idx_ok, _ = match(p.loc[ok, "lon"].values, p.loc[ok, "lat"].values, hs, float(cfg["hotspots"]["match_radius_m"])); idx[ok.values] = idx_ok
    def g(i, c): return hs.loc[i, c] if i >= 0 and c in hs.columns else None
    p["client_hotspot_id"] = [g(i, "hotspot_id") for i in idx]
    p["client_criticality"] = [g(i, "criticality") for i in idx]
    p["client_idd_planned"] = [g(i, "idd_planned_flag") for i in idx]
    p["client_idd_stage"] = [g(i, "idd_stage") for i in idx]
    p["client_idd_owner"] = [g(i, "idd_owner") for i in idx]
    p["client_hotspot"] = [(f"客户热点 {g(i,'hotspot_id')}（{CRIT_EN2ZH.get(g(i,'criticality'), g(i,'criticality'))}）" + (f"，IDD 已计划：{g(i,'idd_stage') or ''} · {g(i,'idd_owner') or ''}" if g(i, "idd_planned_flag") == "yes" else ("，IDD 评估中" if g(i, "idd_planned_flag") == "assessing" else "，无 IDD 计划"))) if i >= 0 else None for i in idx]
    p["client_hotspot_en"] = [(f"Client hotspot {g(i,'hotspot_id')} ({g(i,'criticality')})" + (f", IDD planned: {g(i,'idd_stage') or ''} · {g(i,'idd_owner') or ''}" if g(i, "idd_planned_flag") == "yes" else (", IDD under assessment" if g(i, "idd_planned_flag") == "assessing" else ", no IDD plan"))) if i >= 0 else None for i in idx]
    return p


def fleet_from_plan_a(hs: pd.DataFrame, facilities: pd.DataFrame, cfg: dict, regions=None) -> dict:
    """预案 A 的分片区资源总量 → 泵队台账（契约 swbrain.fleet/1）。驻地 = 片区热点重心最近的民防站（几个片区落到同一民防站就合成一个驻地）；6 寸泵按 bank 成组。
    regions 缺省取 [dispatch.resources].regions（三家市政都上），没配就只 ADM。"""
    from scipy.spatial import cKDTree
    H = cfg["hotspots"]; PM = H["pump_models"]; bank = int(H.get("six_inch_bank", 3))
    regions = tuple(regions or (((cfg.get("dispatch") or {}).get("resources") or {}).get("regions") or ("ADM",)))
    h = hs[hs["region"].isin(regions)].dropna(subset=["lon", "lat"])
    cd = facilities[facilities["kind"] == "civil_defense"].dropna(subset=["lon", "lat"]).reset_index(drop=True)
    tree = cKDTree(_xy(cd["lon"].values, cd["lat"].values)) if len(cd) else None
    models = [{"model": PM["12in"]["model"], **{k: v for k, v in PM["12in"].items() if k not in ("model", "duty_aed_h")}, "duty_aed_h": PM["12in"]["duty_aed_h"], "note": "客户预案 A 12 寸柴油泵；排量为假设"},
              {"model": PM["8in"]["model"], **{k: v for k, v in PM["8in"].items() if k not in ("model", "duty_aed_h")}, "duty_aed_h": PM["8in"]["duty_aed_h"], "note": "客户预案 A 8 寸柴油泵；排量为假设"},
              {"model": PM["6in"]["model"] + f"-x{bank}", **{k: v for k, v in PM["6in"].items() if k not in ("model", "duty_aed_h", "rate_m3h", "crew", "weight_t")},
               "rate_m3h": PM["6in"]["rate_m3h"] * bank, "crew": PM["6in"]["crew"] * bank, "weight_t": PM["6in"]["weight_t"] * bank, "duty_aed_h": PM["6in"]["duty_aed_h"] * bank, "note": f"客户预案 A 6 寸柴油泵，{bank} 台一组建模"}]
    depots, units, k = [], [], 0
    groups: dict = {}
    for area, g in h.groupby(["region", "area"]):
        clon, clat = float(g["lon"].mean()), float(g["lat"].mean())
        if tree is not None:
            _, ci = tree.query(_xy([clon], [clat])[0]); gk = (str(area[0]), int(ci))
        else:
            gk = (str(area[0]), str(area[1]))
        groups.setdefault(gk, []).append((str(area[1]), g))
    for j, (gk, items) in enumerate(groups.items()):
        g = pd.concat([x[1] for x in items]); area = "/".join(x[0] for x in items)
        clon, clat = float(g["lon"].mean()), float(g["lat"].mean())
        if tree is not None:
            ci = gk[1]; name = str(cd.loc[ci, "name"]); dlon, dlat = float(cd.loc[ci, "lon"]), float(cd.loc[ci, "lat"])
        else:
            name = f"{area} 重心"; dlon, dlat = clon, clat
        n12, n8, n6 = int(np.nansum(g["planA_pump12"])), int(np.nansum(g["planA_pump8"])), int(np.nansum(g["planA_pump6"]))
        n_tot = int(np.nansum(pd.to_numeric(g.get("planA_pumps"), errors="coerce"))) if "planA_pumps" in g.columns else 0
        if n12 + n8 + n6 == 0 and n_tot > 0:
            # AAM / DRM 的预案 A 只给泵总数不分尺寸：按 ADM 预案 A 的尺寸比例拆（12 寸 14% / 8 寸 11% / 6 寸 75%，模拟）
            n12 = int(round(n_tot * 0.14)); n8 = int(round(n_tot * 0.11)); n6 = max(n_tot - n12 - n8, 0)
        if n12 + n8 + n6 == 0:
            continue
        dep = {"depot_id": f"A{len(depots)+1:02d}", "name": f"{name} · 预案A {area}", "area": area, "region": str(gk[0]), "lon": dlon, "lat": dlat, "capacity_units": n12 + n8 + int(np.ceil(n6 / bank)),
               "size_split_simulated": bool(int(np.nansum(g["planA_pump12"])) + int(np.nansum(g["planA_pump8"])) + int(np.nansum(g["planA_pump6"])) == 0),
               "plan_a": {"pump12": n12, "pump8": n8, "pump6": n6, "tankers": int(np.nansum(g["planA_tankers"])), "manpower": int(np.nansum(g["planA_manpower"])), "hotspots": int(len(g))}}
        depots.append(dep)
        for m, n in ((models[0]["model"], n12), (models[1]["model"], n8), (models[2]["model"], int(np.ceil(n6 / bank)))):
            for _ in range(n):
                k += 1; units.append({"unit_id": f"PA-{k:03d}", "model": m, "depot_id": dep["depot_id"], "status": "available", "lon": dlon, "lat": dlat})
    return {"schema": "swbrain.fleet/1", "simulated": False, "derived_from": "client_plan_a", "as_of": str(hs.attrs.get("generated_at") or time.strftime("%Y-%m-%d")), "regions": list(regions),
            "models": models, "depots": depots, "units": units,
            "note": "由客户热点清单预案 A 的分片区泵数生成：数量是客户的，驻地取片区热点重心最近的民防站（假设），排量按尺寸估（假设）；客户给正式台账后替换 exchange/fleet.json",
            "note_en": "Derived from the client hotspot register, Plan A pump counts by area: counts are the client's; depot = nearest civil-defence centre to the area centroid (assumption); rates estimated from pump size (assumption)"}


def gwm_depth_at(hs: pd.DataFrame, runs_dir: str | Path, run_id: str) -> np.ndarray:
    """世界模型某次推演在各热点所在 250 m 格的最大水深（m）；格外为 nan。"""
    from pyproj import Transformer
    p = Path(runs_dir) / run_id / "surface_depth_labels_250m.npz"
    out = np.full(len(hs), np.nan)
    if not p.exists():
        return out
    z = np.load(p); x, y, land = z["x"], z["y"], z["land_mask"]; R, C = land.shape; mx = z["depth_m"].max(axis=0).reshape(R, C)
    ok = hs["lon"].notna() & hs["lat"].notna()
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    ex, ey = tr.transform(hs.loc[ok, "lon"].values, hs.loc[ok, "lat"].values)
    ci = np.searchsorted(x, ex) - 1; ri = np.searchsorted(-y, -ey) - 1
    inside = (ci >= 0) & (ci < C) & (ri >= 0) & (ri < R)
    vals = np.full(len(ex), np.nan); vals[inside] = mx[ri[inside], ci[inside]]
    out[ok.values] = vals
    return out


def stats(hs: pd.DataFrame, cs: pd.DataFrame | None = None, fp: dict | None = None, cfg: dict | None = None) -> dict:
    """汇总：按区/重要度/根因/IDD；与本系统终点的邻近；与预测侧（世界模型）的命中率。"""
    s = {"n": int(len(hs)), "by_region": {k: int(v) for k, v in hs["region"].value_counts().items()},
         "by_criticality": {c: int((hs["criticality"] == c).sum()) for c in CRIT_ORDER},
         "by_root_cause": {k: int(v) for k, v in hs["root_cause"].value_counts().items()},
         "network_available": int(hs["network_available"].fillna(False).astype(bool).sum()),
         "idd_planned": int((hs["idd_planned_flag"] == "yes").sum()), "idd_assessing": int((hs["idd_planned_flag"] == "assessing").sum()),
         "planA": {"pumps": int(np.nansum(hs["planA_pumps"])), "pump12": int(np.nansum(hs["planA_pump12"])), "pump8": int(np.nansum(hs["planA_pump8"])), "pump6": int(np.nansum(hs["planA_pump6"])),
                   "tankers": int(np.nansum(hs["planA_tankers"])), "manpower": int(np.nansum(hs["planA_manpower"])), "budget_aed": float(np.nansum(hs["planA_budget_aed"]))},
         "with_coords": int((hs["lon"].notna() & hs["lat"].notna()).sum()), "source": hs.attrs.get("source"), "generated_at": hs.attrs.get("generated_at")}
    if "nf_backwater_10" in hs.columns:                       # 匹配管段的全网溢流路由结果（run.py 挂上）
        bw = hs["nf_backwater_10"].fillna(False).astype(bool); sp = pd.to_numeric(hs["nf_spill_m3_10"], errors="coerce").fillna(0) > 0
        s["nf_10"] = {"matched_reach": int(hs["edge_gid"].notna().sum()), "spill_at_reach": int(sp.sum()), "backwater_risk": int(bw.sum()), "backwater_only": int((bw & ~sp).sum()), "flagged_any": int((sp | bw).sum())}
    adm = hs[(hs["region"] == "ADM") & hs["lon"].notna()]
    if cs is not None and len(adm):
        from scipy.spatial import cKDTree
        c = cs.dropna(subset=["lon", "lat"])
        d, _ = cKDTree(_xy(c["lon"].values, c["lat"].values)).query(_xy(adm["lon"].values, adm["lat"].values))
        nonet = adm["network_available"] == False
        s["adm_vs_network"] = {"n": int(len(adm)), "within_500m_of_terminal": int((d < 500).sum()), "within_1km_of_terminal": int((d < 1000).sum()),
                               "client_no_network": int(nonet.sum()), "client_no_network_but_terminal_within_500m": int(((d < 500) & nonet.values).sum())}
    if fp is not None and cfg is not None and (fp.get("model") or {}).get("type") == "world":
        dep = gwm_depth_at(adm, cfg["gwm"].get("runs_dir", ""), fp.get("run_id", ""))
        ins = np.isfinite(dep)
        if ins.any():
            byc = {}
            for cname in CRIT_ORDER:
                m = (adm["criticality"] == cname).values & ins
                byc[cname] = {"n": int(m.sum()), "median_depth_m": (round(float(np.median(dep[m])), 3) if m.any() else None), "hit_015": int((dep[m] >= 0.15).sum()), "hit_030": int((dep[m] >= 0.30).sum())}
            s["gwm_vs_hotspots"] = {"run_id": fp.get("run_id"), "scenario": (fp.get("scenario") or {}).get("name"), "inside_grid": int(ins.sum()),
                                    "median_depth_m": round(float(np.median(dep[ins])), 3), "hit_015": int((dep[ins] >= 0.15).sum()), "hit_030": int((dep[ins] >= 0.30).sum()),
                                    "pod_015": round(float((dep[ins] >= 0.15).mean()), 3), "pod_030": round(float((dep[ins] >= 0.30).mean()), 3), "by_criticality": byc}
        # 积水点覆盖了多少客户热点
        pts = fp.get("points") or []
        if pts:
            idx, _ = match([p["lon"] for p in pts], [p["lat"] for p in pts], adm, float(cfg["hotspots"]["match_radius_m"]))
            s["flood_points_matching_hotspot"] = int((idx >= 0).sum()); s["hotspots_covered_by_flood_points"] = int(len(set(int(i) for i in idx if i >= 0)))
    return s


def discrepancy_report(hs: pd.DataFrame, cs: pd.DataFrame, radius_m: float = 500.0) -> pd.DataFrame:
    """客户标「无管网」但本系统 500 m 内有汇水区终点的热点：给客户核对（可能是有干管无进水口，也可能是台账口径不同）。"""
    from scipy.spatial import cKDTree
    a = hs[(hs["region"] == "ADM") & hs["lon"].notna() & (hs["network_available"] == False)].copy()
    c = cs.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    if not len(a) or not len(c):
        return pd.DataFrame()
    d, j = cKDTree(_xy(c["lon"].values, c["lat"].values)).query(_xy(a["lon"].values, a["lat"].values))
    a["nearest_terminal"] = c.loc[j, "terminal"].values; a["nearest_terminal_unitid"] = c.loc[j, "terminal_unitid"].values
    a["nearest_terminal_kind"] = c.loc[j, "terminal_kind"].values; a["nearest_catchment_km"] = c.loc[j, "len_km"].values.round(2); a["distance_m"] = d.round(0)
    a = a[a["distance_m"] <= radius_m].sort_values(["criticality", "distance_m"], key=lambda s: s.map({"Very High": 0, "High": 1, "Medium": 2, "Low": 3}) if s.name == "criticality" else s)
    return a[["hotspot_id", "area", "location", "lat", "lon", "criticality", "root_cause_raw", "distance_m", "nearest_terminal_unitid", "nearest_terminal_kind", "nearest_catchment_km", "idd_planned_flag", "idd_stage"]]


def to_geojson(hs: pd.DataFrame) -> dict:
    feats = []
    for r in hs.dropna(subset=["lon", "lat"]).replace({np.nan: None}).to_dict(orient="records"):
        props = {k: r.get(k) for k in ("hotspot_id", "region", "area", "location", "criticality", "network_available", "root_cause", "root_cause_raw", "planA_pumps", "planA_pump12", "planA_pump8", "planA_pump6",
                                        "planA_tankers", "planA_manpower", "idd_planned_flag", "idd_category", "idd_type", "idd_stage", "idd_owner", "idd_completion", "design_event_future")}
        props["criticality_zh"] = CRIT_EN2ZH.get(r.get("criticality"), r.get("criticality")); props["root_cause_zh"] = ROOT_CAUSE_ZH.get(r.get("root_cause"), r.get("root_cause"))
        for k in ("dtm_z", "low_z", "spill_z", "depth_max_m", "pond_volume_m3", "pond_area_m2", "terrain_note_zh", "terrain_note_en", "edge_gid", "nf_spill_m3_10", "nf_backwater_10"):   # 5 m DTM 填洼、匹配管段的 10 年溢流/顶托（有才带）
            if r.get(k) is not None and not (isinstance(r.get(k), float) and np.isnan(r.get(k))):
                props[k] = r[k]
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]}, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def main(argv=None):
    import tomllib
    ap = argparse.ArgumentParser(description="客户热点清单 → swbrain.hotspots/1")
    ap.add_argument("--dir", default=None, help="客户热点工作簿目录；缺省取 config [data].hotspot_xlsx_dir")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--geojson", default=None, help="另写一份地图用的 GeoJSON（一二维热点群计算读 console/data/hotspots.geojson；平时由 swbrain.run 写）")
    ap.add_argument("--kmz", default=None, help="客户 506 热点 KMZ，按序号覆盖 ADM 坐标；缺省取 config [hotspots].coords_kmz")
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    a.dir = a.dir or (cfg.get("data") or {}).get("hotspot_xlsx_dir") or r"D:/世界模型/FloodData_Batch1/Flood Management Data - Origen - Batch1"
    df = parse_dir(Path(a.dir))
    kmz = a.kmz or cfg.get("hotspots", {}).get("coords_kmz")
    note_kmz = ""
    if kmz and Path(kmz).exists():
        df, st = apply_kmz_coords(df, kmz, cfg.get("hotspots", {}).get("coords_kmz_region", "ADM"))
        print(f"KMZ 坐标覆盖：{st}")
        note_kmz = f" · ADM 坐标按 {Path(kmz).name} 覆盖 {st['matched']} 个（原表错行 {st['xlsx_row_shift_plus1']} 个）"
        pd.DataFrame({"hotspot_id": df.loc[df["coord_source"] == "kmz", "hotspot_id"], "lon_xlsx": df.loc[df["coord_source"] == "kmz", "lon_xlsx"], "lat_xlsx": df.loc[df["coord_source"] == "kmz", "lat_xlsx"],
                      "lon_kmz": df.loc[df["coord_source"] == "kmz", "lon"], "lat_kmz": df.loc[df["coord_source"] == "kmz", "lat"]}).to_csv(ROOT / "out" / "客户热点坐标核对_xlsx_vs_kmz.csv", index=False, encoding="utf-8-sig")
    elif kmz:
        print(f"KMZ 不存在，坐标沿用工作簿：{kmz}")
    out = Path(a.out) if a.out else ROOT / cfg["hotspots"]["file"]
    write_contract(df, out, source_note=f"Origen Batch 1 · {Path(a.dir).name} · 解析于 {time.strftime('%Y-%m-%d')}{note_kmz}")
    (ROOT / "exchange" / "examples" / "hotspots.example.json").write_text(json.dumps(example(), ensure_ascii=False, indent=2), encoding="utf-8")
    s = stats(df)
    print(f"热点 {s['n']} 个 {s['by_region']}，有坐标 {s['with_coords']}；重要度 {s['by_criticality']}；根因 {s['by_root_cause']}；IDD 已计划 {s['idd_planned']}，评估中 {s['idd_assessing']}")
    print(f"预案 A：泵 {s['planA']['pumps']}（12寸 {s['planA']['pump12']} / 8寸 {s['planA']['pump8']} / 6寸 {s['planA']['pump6']}），罐车 {s['planA']['tankers']}，人力 {s['planA']['manpower']}，预算 {s['planA']['budget_aed']:,.0f} AED")
    if a.geojson:
        Path(a.geojson).parent.mkdir(parents=True, exist_ok=True)
        Path(a.geojson).write_text(json.dumps(to_geojson(load(out)), ensure_ascii=False), encoding="utf-8")
        print(f"→ {a.geojson}")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
