"""开源治理引擎 PoC 第 4 步：和 UN 第八轮结果逐项对比。

  python tools/oss_gov/o4_compare.py [e1|c2]

真值（都读快照副本）：
  Summary.geodatabase / Errors        UDMS SummarizeUNErrors 的错误明细（每行一个错误，起点 / 终点 GlobalID）
  Stormwater_AD.geodatabase           UN_6_DirtyAreas（ERRORCODE 是按位的错误码掩码，ERRORMESSAGE 带 29 的子网名）、UN_6_Subnetworks
  Stormwater_AD_reporting.gdb         各类的 smasubnetworkname（子网名）
  s5_report.json                      UN 第八轮汇总数
连接键：GlobalID（预处理库的 GlobalID 装进 UN 后不变，已验证 OUTFALL 255 / 255 一致）。
产物：out/<口径>/compare.json、out/<口径>/diff_*.csv（分歧样例）
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402

CODES = (8, 9, 10, 13, 19, 20, 21, 25)


def ro(p):
    return sqlite3.connect(f"file:{Path(p).as_posix()}?mode=ro", uri=True)


def main():
    C.setup(sys.argv)
    T = C.Timer()
    feat = pd.read_parquet(C.OUT / "features.parquet")
    g2 = feat.set_index("globalid").gid
    mine = pd.concat([pd.read_parquet(C.OUT / "errors.parquet"), pd.read_parquet(C.OUT / "errors_29.parquet")])
    sub = pd.read_parquet(C.OUT / "subnet.parquet")
    o3 = json.load(open(C.OUT / "o3_trace.json", encoding="utf-8"))
    s5 = json.load(open(C.INP / "s5_report.json", encoding="utf-8"))["report"]
    R = {"mode": C.MODE}

    # ---------- 1 错误 ----------
    con = ro(C.SUMMARY)
    un = pd.read_sql("select ErrorCode code, OriginGUID o, DestinationGUID d, OriginAssetGroup oag, DestinationAssetGroup dag, AdditionalMessage msg from Errors", con)
    un["og"] = g2.reindex(C.norm_gid(un.o)).values
    un["dg"] = g2.reindex(C.norm_gid(un.d.fillna(""))).values
    R["un_errors_unmatched_guid"] = int(un.og.isna().sum())
    con = ro(C.UN_MG)
    da = pd.read_sql("select ERRORCODE, ERRORMESSAGE from UN_6_DirtyAreas", con)
    ec = da.ERRORCODE.fillna(0).to_numpy(dtype=np.int64)
    by_bit = {c: int(((ec >> c) & 1).sum()) for c in range(0, 40) if ((ec >> c) & 1).any()}
    un29 = sorted({n for m in da.ERRORMESSAGE.fillna("") for n in re.findall(r"29,(SM[AI]-\d{4})", m)})
    sn = pd.read_sql("select SUBNETWORKNAME n, LASTUPDATESUBNETWORK is not null upd from UN_6_Subnetworks", con)
    un_upd = sn.groupby("n").upd.min()
    tab = []
    ex = []
    feat_cols = ["gid", "layer", "assetid", "globalid", "ag", "at"]
    for code in CODES:
        u = un[un.code == code]
        m = mine[mine.code == code]
        U = set(u.og.dropna().astype(int)) | set(u.dg.dropna().astype(int))
        M = set(m.origin_gid) | set(m.dest_gid[m.dest_gid >= 0])
        tab.append({"code": code, "UN_dirty_areas(按位)": by_bit.get(code, 0), "UN_s5_report(消息段)": s5["errors_by_un_code"].get(str(code), 0),
                    "UN_errors_rows": len(u), "OSS_rows": len(m), "UN_features": len(U), "OSS_features": len(M), "both": len(U & M),
                    "UN_only": len(U - M), "OSS_only": len(M - U)})
        for side, S in (("UN_only", U - M), ("OSS_only", M - U)):
            for g in sorted(S)[:10]:
                ex.append({"code": code, "side": side, **feat.loc[g, feat_cols].to_dict()})
    myn29 = sorted({x for p in o3["B"]["conflicts_29"] for x in p})
    tab.append({"code": 29, "UN_dirty_areas(按位)": by_bit.get(29, 0), "UN_s5_report(消息段)": s5["errors_by_un_code"].get("29", 0),
                "UN_errors_rows": 0, "OSS_rows": o3["B"]["n_conflict_pairs"], "UN_features": len(un29), "OSS_features": len(myn29),
                "both": len(set(un29) & set(myn29)), "UN_only": len(set(un29) - set(myn29)), "OSS_only": len(set(myn29) - set(un29)),
                "note": "29 按子网名计：UN = 脏区消息里的子网名；OSS = 冲突对涉及的子网名"})
    R["errors"] = tab
    R["errors_29_names"] = {"UN": un29, "OSS": myn29}
    R["un_dirty_area_bits"] = by_bit
    R["un_dirty_areas_total"] = int(len(da))
    pd.DataFrame(ex).to_csv(C.OUT / "diff_errors.csv", index=False, encoding="utf-8-sig")
    T.lap("1 错误对比")

    # ---------- 2 子网 ----------
    # 控制器命名：按 GlobalID 对 UN 的 SubnetworkControllers（导出库）
    sc = pyogrio.read_dataframe(C.REP, layer="SubnetworkControllers", columns=["featureglobalid", "subnetworkname"], read_geometry=False)
    sc["gid"] = g2.reindex(C.norm_gid(sc.featureglobalid)).values
    myc = pd.read_parquet(C.OUT / "controllers.parquet").merge(sc[["gid", "subnetworkname"]].dropna(), on="gid", how="outer")
    R["controllers_name_match"] = {"UN": int(len(sc)), "OSS": int(myc.name.notna().sum()), "same_name": int((myc.name == myc.subnetworkname).sum())}
    rl = pyogrio.read_dataframe(C.REP, layer="StormwaterLine", columns=["globalid", "smasubnetworkname", "isconnected"], read_geometry=False, use_arrow=True)
    rl["gid"] = g2.reindex(C.norm_gid(rl.globalid)).values
    R["un_lines_unmatched"] = int(rl.gid.isna().sum())
    L = sub[sub.kind == "line"].merge(rl[["gid", "smasubnetworkname"]].astype({"gid": "int64"}), on="gid", how="left").rename(columns={"smasubnetworkname": "UN"})
    nL = len(L)
    R["lines_total"] = nL
    un_in = ~L.UN.isin(["Unknown"]) & L.UN.notna()
    R["UN_lines_in_subnet"] = int(un_in.sum())
    R["UN_share"] = round(un_in.mean(), 4)
    R["UN_names"] = {"total": int(len(un_upd)), "updated": int(un_upd.sum()), "failed": int((~un_upd.astype(bool)).sum())}
    un_failed = set(un_upd.index[~un_upd.astype(bool)])
    agree = {}
    for var in "ABCD":
        v = L[var]
        vin = ~v.isin(["Unknown", "Multiple"])
        same = (v == L.UN)
        agree[var] = {"lines_in_subnet": int(vin.sum()), "share": round(vin.mean(), 4), "same_label(含都为 Unknown)": int(same.sum()),
                      "agreement": round(same.mean(), 4), "both_in_same_name": int((same & un_in).sum()), "both_in_diff_name": int((vin & un_in & ~same).sum()),
                      "UN_in_OSS_unknown": int((un_in & (v == "Unknown")).sum()), "UN_in_OSS_multiple": int((un_in & (v == "Multiple")).sum()),
                      "UN_unknown_OSS_in": int((~un_in & vin).sum()), "UN_unknown_OSS_multiple": int((~un_in & (v == "Multiple")).sum())}
        if var in "CD":
            fail = set(o3[var]["failed_names"])
            agree[var]["failed_names"] = {"OSS": len(fail), "UN": len(un_failed), "both": len(fail & un_failed), "UN_only": sorted(un_failed - fail),
                                          "OSS_only": sorted(fail - un_failed)}
    R["subnet_agreement"] = agree
    # 分歧样例：按 UN 名 × OSS 名 统计，取最大的若干组
    for var in "CD":
        d = L[L[var] != L.UN].groupby(["UN", var]).size().sort_values(ascending=False).head(25)
        R[f"top_diff_groups_{var}"] = [[a, b, int(n)] for (a, b), n in d.items()]
    dl = L[L.C != L.UN][["gid", "UN", "B", "C", "D"]].merge(feat[["gid", "assetid", "globalid", "at", "length"]], on="gid")
    dl.to_csv(C.OUT / "diff_lines_C.csv", index=False, encoding="utf-8-sig")
    # 每个子网名的线数：UN vs OSS（C）
    cnt = pd.DataFrame({"UN": L.UN.value_counts(), "C": L.C.value_counts(), "D": L.D.value_counts(), "B": L.B.value_counts()}).fillna(0).astype(int)
    cnt.to_csv(C.OUT / "diff_subnet_counts.csv", encoding="utf-8-sig")
    T.lap("2 子网对比")

    # ---------- 3 SMA-0175 / RD-A ----------
    dom = gpd.read_file(C.INP / "rda_domain.geojson").to_crs(32640).union_all()
    lg = gpd.read_parquet(C.OUT / "lines_geom.parquet")
    inside = lg.intersects(dom).values                    # 与 area_stats.py 的 arcpy spatial_filter 一样按「相交」
    Li = L.set_index("gid")
    rd = {"lines_intersecting_RD-A": int(inside.sum())}
    for col in ("UN", "A", "B", "C", "D"):
        s = Li[col]
        rd[col] = {"SMA-0175_total": int((s == "SMA-0175").sum()), "SMA-0175_in_RD-A": int((s[inside] == "SMA-0175").sum()),
                   "RD-A_by_name": s[inside].value_counts().head(6).to_dict()}
    R["SMA-0175"] = rd
    T.lap("3 SMA-0175")
    R["timing_s"] = T.total()
    C.jdump(R, C.OUT / "compare.json")
    print(json.dumps({k: R[k] for k in ("errors", "UN_names", "SMA-0175")}, ensure_ascii=False, default=str)[:4000])
    print(json.dumps(agree, ensure_ascii=False, default=str)[:3000])


if __name__ == "__main__":
    main()
