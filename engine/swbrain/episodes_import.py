"""把客户档案里的三类文件落成积水经历契约（swbrain.episodes/1），给生命周期层当逐点真实记录。

  1) 事件报告表   Flood Management\\Incident Reports Related to Severe Rainfall Events\\Incidents Report *.xlsx（2024-02 / 2024-04 / 2026-03）
  2) 呼叫中心 / 巡查  Data Committee\\TACTICA POC SFTP - 1\\Batch2\\800555 Call Center.xlsx、AI-Inspection.xlsx、Estijabah.xlsx
  3) 卫星洪涝面   UPPC Flood_Data_April2024.gdb 的 Flood_Areas_19042024_WithoutWaterBodies（或任何 shp / GeoJSON 洪涝面）

列名事先不知道：按关键词猜列（--map 可指定），每行都写明匹配依据（坐标最近热点 / 地名文字重合），匹配不到的行原样列出，不硬塞。
坐标：WGS84 经纬度直接用；像 UTM 40N 的大数字（x > 1e5）自动转。卫星面：热点点位落在洪涝面（加 buffer_m）内 = 该点在该事件积水（source=satellite）；
面外不写「不积水」——没被拍到不等于没淹。

用法（先 --dry-run 看匹配统计，再 --push 合并进 exchange/episodes.json）：
  python -m swbrain.episodes_import --incidents "…/Incidents Report April 2024.xlsx" --event 2024-04 --dry-run
  python -m swbrain.episodes_import --polygons "…/Flood_Data_April2024.gdb" --layer Flood_Areas_19042024_WithoutWaterBodies --event 2024-04 --push
  python -m swbrain.episodes_import --callcenter "…/800555 Call Center.xlsx" --dry-run
"""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ROLE_KEYS = {
    "time": ["date", "time", "reported", "created", "opened", "occur", "تاريخ", "وقت", "datetime", "timestamp"],
    "lat": ["latitude", "lat", "y_wgs", "wgs_y", "northing_deg"],
    "lon": ["longitude", "long", "lng", "lon", "x_wgs", "wgs_x", "easting_deg"],
    "x": ["easting", "x_utm", "utm_x", " x", "coord_x", "xcoord"],
    "y": ["northing", "y_utm", "utm_y", " y", "coord_y", "ycoord"],
    "location": ["location", "address", "street", "road", "place", "site", "area", "zone", "sector", "district", "موقع", "العنوان", "منطقة", "الشارع", "landmark"],
    "description": ["description", "details", "remark", "incident", "complaint", "issue", "type", "category", "subject", "comment", "وصف", "تفاصيل", "البلاغ", "نوع"],
    "status": ["status", "action", "resolution", "closed", "state", "حالة", "الإجراء"],
    "units": ["pump", "units", "tanker", "equipment", "مضخ", "صهاريج"],
    "depth": ["depth", "water level", "level", "عمق", "ارتفاع"],
    "id": ["id", "ticket", "ref", "case", "no.", "number", "رقم"],
}
ARABIC_STOP = {"شارع", "طريق", "مدينة", "منطقة", "تقاطع", "بالاتجاهين", "دوار", "امام", "أمام", "بجانب", "مقابل", "الى", "إلى", "من", "في"}
EN_STOP = {"road", "street", "st", "rd", "the", "and", "near", "opposite", "junction", "intersection", "area", "city", "district", "along", "both", "directions", "towards", "to", "of"}


# ---------------------------------------------------------------- 读表与猜列
def load_table(path: str | Path, sheet=None) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".csv", ".txt"):
        return pd.read_csv(p)
    x = pd.ExcelFile(p)
    sh = sheet if sheet is not None else x.sheet_names[0]
    df = x.parse(sh)
    if df.shape[1] <= 1 or all(str(c).startswith("Unnamed") for c in df.columns):      # 表头不在第一行：找第一行非空最多的当表头
        raw = x.parse(sh, header=None)
        hdr = int(raw.notna().sum(axis=1).idxmax())
        df = x.parse(sh, header=hdr)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def guess_columns(df: pd.DataFrame, override: dict | None = None) -> dict:
    """列名 → 角色。override 形如 {"lat": "Y", "lon": "X", "location": "Site"}；猜不到的角色为 None。"""
    roles: dict = {k: None for k in ROLE_KEYS}
    low = {c: str(c).lower() for c in df.columns}
    for role, keys in ROLE_KEYS.items():
        best = None; best_rank = 99
        for c, lc in low.items():
            for rank, k in enumerate(keys):
                if k in lc and rank < best_rank:
                    if role == "lat" and "lon" in lc:
                        continue
                    if role == "lon" and "lat" in lc:
                        continue
                    best, best_rank = c, rank
        roles[role] = best
    # 经纬度的数值范围校验：不像经纬度就当没有
    for role, lo, hi in (("lat", 20.0, 27.0), ("lon", 50.0, 58.0)):
        c = roles.get(role)
        if c is not None:
            v = pd.to_numeric(df[c], errors="coerce").dropna()
            if not len(v) or not (v.between(lo, hi).mean() > 0.5):
                roles[role] = None
    for k, v in (override or {}).items():
        roles[k] = v
    return roles


def _to_lonlat(df: pd.DataFrame, roles: dict) -> tuple[np.ndarray, np.ndarray, str]:
    n = len(df)
    lon = np.full(n, np.nan); lat = np.full(n, np.nan); how = "none"
    if roles.get("lat") and roles.get("lon"):
        lat = pd.to_numeric(df[roles["lat"]], errors="coerce").to_numpy(dtype=float); lon = pd.to_numeric(df[roles["lon"]], errors="coerce").to_numpy(dtype=float); how = "wgs84"
    elif roles.get("x") and roles.get("y"):
        x = pd.to_numeric(df[roles["x"]], errors="coerce").to_numpy(dtype=float); y = pd.to_numeric(df[roles["y"]], errors="coerce").to_numpy(dtype=float)
        if np.nanmedian(np.abs(x)) > 1000:                              # UTM 40N（阿布扎比 EPSG:32640）
            from pyproj import Transformer
            tr = Transformer.from_crs(32640, 4326, always_xy=True)
            ok = np.isfinite(x) & np.isfinite(y)
            lo_, la_ = tr.transform(x[ok], y[ok]); lon[ok] = lo_; lat[ok] = la_; how = "utm40n"
        else:
            lon, lat, how = x, y, "wgs84(xy)"
    return lon, lat, how


# ---------------------------------------------------------------- 匹配热点
def _tokens(s: str) -> set:
    toks = re.findall(r"[A-Za-z]{3,}|[؀-ۿ]{3,}|\d{2,}", str(s or ""))
    return {t.lower() for t in toks if t.lower() not in EN_STOP and t not in ARABIC_STOP}


def match_hotspots(df: pd.DataFrame, roles: dict, hotspots: pd.DataFrame, radius_m: float = 300.0, region_col: str | None = None) -> list[dict]:
    """每行 → {fp_id, match_by, dist_m, score}。坐标优先（radius_m 内最近热点），否则地名 / 描述与热点 location 的词重合（≥ 2 个词）。"""
    hs = hotspots.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    hx = hs["lon"].to_numpy(dtype=float) * 111000 * math.cos(math.radians(24.4)); hy = hs["lat"].to_numpy(dtype=float) * 111000
    hs_tok = [_tokens(f"{a} {l}") for a, l in zip(hs["area"].astype(str), hs["location"].astype(str))]
    lon, lat, how = _to_lonlat(df, roles)
    out = []
    text_cols = [c for c in (roles.get("location"), roles.get("description")) if c]
    for i in range(len(df)):
        m = {"fp_id": None, "match_by": None, "dist_m": None, "score": None, "coords": how}
        if np.isfinite(lon[i]) and np.isfinite(lat[i]):
            dx = hx - lon[i] * 111000 * math.cos(math.radians(24.4)); dy = hy - lat[i] * 111000
            d = np.hypot(dx, dy); j = int(np.argmin(d))
            if d[j] <= radius_m:
                m.update({"fp_id": str(hs.loc[j, "hotspot_id"]), "match_by": "coords", "dist_m": round(float(d[j]), 0)})
        if m["fp_id"] is None and text_cols:
            txt = " ".join(str(df.iloc[i][c]) for c in text_cols if pd.notna(df.iloc[i][c]))
            tk = _tokens(txt)
            if tk:
                scores = [len(tk & t) for t in hs_tok]
                j = int(np.argmax(scores))
                if scores[j] >= 2 and scores.count(scores[j]) == 1:
                    m.update({"fp_id": str(hs.loc[j, "hotspot_id"]), "match_by": "text", "score": int(scores[j])})
        out.append(m)
    return out


def _time_str(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        t = pd.to_datetime(v, errors="coerce", dayfirst=True)             # 阿联酋表格是 日/月/年
        return None if pd.isna(t) else t.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _num(v):
    try:
        f = float(v); return f if f == f else None
    except (TypeError, ValueError):
        m = re.search(r"\d+(\.\d+)?", str(v or ""))
        return float(m.group(0)) if m else None


# ---------------------------------------------------------------- 三类转换
def from_incident_report(path, event_id: str, hotspots: pd.DataFrame, sheet=None, colmap: dict | None = None, radius_m: float = 300.0, source: str = "incident_report") -> dict:
    df = load_table(path, sheet); roles = guess_columns(df, colmap)
    matches = match_hotspots(df, roles, hotspots, radius_m)
    rows, unmatched = [], []
    for i, m in enumerate(matches):
        r = df.iloc[i]
        desc = " | ".join(str(r[c])[:120] for c in (roles.get("location"), roles.get("description"), roles.get("status")) if c and pd.notna(r[c]))
        at = _time_str(r[roles["time"]]) if roles.get("time") else None
        if not m["fp_id"]:
            unmatched.append({"row": int(i), "text": desc[:200], "at": at}); continue
        rid = f"{source.upper()[:3]}-{event_id}-{(str(r[roles['id']]) if roles.get('id') and pd.notna(r[roles['id']]) else i)}"
        base = {"id": rid, "fp_id": m["fp_id"], "event_id": str(event_id), "flooded": True, "source": source, "note": desc[:200], "match": {k: v for k, v in m.items() if v is not None}}
        d = _num(r[roles["depth"]]) if roles.get("depth") else None
        if d is not None:
            base["depth_m"] = d if d < 5 else round(d / 100.0, 2)          # 大于 5 当厘米
        u = _num(r[roles["units"]]) if roles.get("units") else None
        if u is not None and u >= 0:
            base["units"] = int(u)
        if at:
            base["stage"] = "reported"; base["at"] = at
        rows.append(base)
    return {"rows": rows, "unmatched": unmatched, "columns": roles, "stats": {"rows": int(len(df)), "matched": len(rows), "by_coords": sum(1 for m in matches if m["match_by"] == "coords"), "by_text": sum(1 for m in matches if m["match_by"] == "text"), "unmatched": len(unmatched)}}


def from_call_center(path, events: dict, hotspots: pd.DataFrame, sheet=None, colmap: dict | None = None, radius_m: float = 300.0, source: str = "call_center", keyword: str | None = r"flood|rain|water|storm|غرق|مياه|أمطار|امطار|تجمع") -> dict:
    """呼叫中心 / 巡查表：按时间落进事件窗口，按坐标或地名对热点；keyword 过滤只留与积水有关的记录（None = 不过滤）。"""
    from . import lifecycle as LC
    df = load_table(path, sheet); roles = guess_columns(df, colmap)
    if keyword and (roles.get("description") or roles.get("location")):
        cols = [c for c in (roles.get("description"), roles.get("location")) if c]
        txt = df[cols].astype(str).agg(" ".join, axis=1)
        df = df[txt.str.contains(keyword, case=False, regex=True, na=False)].reset_index(drop=True)
    matches = match_hotspots(df, roles, hotspots, radius_m)
    rows, unmatched, no_event = [], [], 0
    for i, m in enumerate(matches):
        r = df.iloc[i]; at = _time_str(r[roles["time"]]) if roles.get("time") else None
        eid = next((k for k, e in events.items() if LC.in_window(at, e.get("start"), e.get("end"))), None) if at else None
        desc = " | ".join(str(r[c])[:120] for c in (roles.get("location"), roles.get("description"), roles.get("status")) if c and pd.notna(r[c]))
        if eid is None:
            no_event += 1; continue
        if not m["fp_id"]:
            unmatched.append({"row": int(i), "event_id": eid, "text": desc[:200], "at": at}); continue
        rid = f"CC-{eid}-{(str(r[roles['id']]) if roles.get('id') and pd.notna(r[roles['id']]) else i)}"
        rows.append({"id": rid, "fp_id": m["fp_id"], "event_id": eid, "flooded": True, "stage": "reported", "at": at, "source": source, "note": desc[:200], "match": {k: v for k, v in m.items() if v is not None}})
    return {"rows": rows, "unmatched": unmatched, "columns": roles, "stats": {"rows": int(len(df)), "matched": len(rows), "unmatched": len(unmatched), "outside_event_windows": no_event}}


def from_flood_polygons(path, event_id: str, hotspots: pd.DataFrame, layer: str | None = None, buffer_m: float = 30.0, date: str | None = None, source: str = "satellite") -> dict:
    """洪涝面（gdb 图层 / shp / GeoJSON）× 热点点位 → 落在面内（含 buffer_m）的点在该事件积水。面外不写不积水。"""
    import geopandas as gpd
    from shapely.geometry import Point
    g = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if g.crs is None:
        g = g.set_crs(4326)
    g = g.to_crs(32640)                                                 # 米制做 buffer
    geom = g.geometry.buffer(float(buffer_m)).union_all() if hasattr(g.geometry, "union_all") else g.geometry.buffer(float(buffer_m)).unary_union
    hs = hotspots.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    pts = gpd.GeoSeries([Point(x, y) for x, y in zip(hs["lon"].astype(float), hs["lat"].astype(float))], crs=4326).to_crs(32640)
    inside = pts.within(geom)
    rows = [{"id": f"SAT-{event_id}-{hid}", "fp_id": str(hid), "event_id": str(event_id), "flooded": True, "source": source, "note": f"{Path(str(path)).name}{(':' + layer) if layer else ''}{(' ' + date) if date else ''} 洪涝面内（buffer {buffer_m:g} m）", **({"at": date} if date else {})}
            for hid, ok in zip(hs["hotspot_id"], inside) if bool(ok)]
    return {"rows": rows, "unmatched": [], "columns": {"layer": layer, "crs": str(g.crs), "n_polygons": int(len(g))}, "stats": {"hotspots": int(len(hs)), "flooded": len(rows), "buffer_m": buffer_m}}


# ---------------------------------------------------------------- 输出
def write_or_push(res: dict, out: str | Path | None, push: bool, cfg: dict | None, name: str = "episodes") -> dict:
    payload = {"schema": "swbrain.episodes/1", "source": "episodes_import", "rows": [{k: v for k, v in r.items() if k != "match"} | ({"match": r["match"]} if r.get("match") else {}) for r in res["rows"]]}
    if push:
        from . import feeds
        return feeds.push(cfg, name, payload, origin="episodes_import")
    out = Path(out) if out else ROOT / "out" / f"{name}_import.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**payload, "unmatched": res.get("unmatched"), "columns": res.get("columns"), "stats": res.get("stats")}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"written": str(out), **res["stats"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="客户事件报告 / 呼叫中心 / 卫星洪涝面 → swbrain.episodes/1")
    ap.add_argument("--incidents"); ap.add_argument("--callcenter"); ap.add_argument("--polygons"); ap.add_argument("--layer")
    ap.add_argument("--event", help="事件编号（incidents / polygons 必填，如 2024-04）"); ap.add_argument("--date", help="卫星影像日期（如 2024-04-19）")
    ap.add_argument("--sheet"); ap.add_argument("--map", help="列角色映射：lat=Y,lon=X,location=Site,time=Date,description=Details")
    ap.add_argument("--radius", type=float, default=300.0); ap.add_argument("--buffer", type=float, default=30.0)
    ap.add_argument("--out"); ap.add_argument("--push", action="store_true"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--config")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from . import db, hotspots as hsm, lifecycle as LC
    cfg = db.load_config(a.config); hs = hsm.load(ROOT / cfg["hotspots"]["file"])
    colmap = dict(kv.split("=", 1) for kv in a.map.split(",")) if a.map else None
    if a.incidents:
        if not a.event:
            ap.error("--incidents 需要 --event")
        res = from_incident_report(a.incidents, a.event, hs, a.sheet, colmap, a.radius)
    elif a.callcenter:
        res = from_call_center(a.callcenter, LC.events(cfg), hs, a.sheet, colmap, a.radius)
    elif a.polygons:
        if not a.event:
            ap.error("--polygons 需要 --event")
        res = from_flood_polygons(a.polygons, a.event, hs, a.layer, a.buffer, a.date)
    else:
        ap.error("给 --incidents / --callcenter / --polygons 之一")
    print("列角色：", json.dumps(res.get("columns"), ensure_ascii=False))
    print("统计：", json.dumps(res.get("stats"), ensure_ascii=False))
    for u in (res.get("unmatched") or [])[:10]:
        print("  未匹配：", json.dumps(u, ensure_ascii=False)[:200])
    if a.dry_run and not a.push:
        print("dry-run：不写文件。样例：", json.dumps(res["rows"][:3], ensure_ascii=False)[:600])
        return 0
    print(json.dumps(write_or_push(res, a.out, a.push, cfg), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
