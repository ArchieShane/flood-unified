"""六个交换契约的 JSON Schema：从一处声明生成 `exchange/schemas/*.schema.json`，并与 `feeds.validate()` 的实际行为对表。

为什么要有：给 ADM 侧对接的人一份正式规格（draft 2020-12），也让「示例文件、校验代码、文档表格」三者不各说各话。
  python -m swbrain.schemagen            写 exchange/schemas/*.schema.json
  python -m swbrain.schemagen --check    只对表：示例文件要过 schema，且 schema 认的 / 不认的与 feeds.validate 一致（退出码 1 = 不一致）
不引入 jsonschema 依赖：自带一个够用的子集校验器（类型、必填、范围、枚举、anyOf-必填其一）。"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BBOX = {"lon": (51.0, 57.0), "lat": (22.3, 26.6)}          # 阿布扎比酋长国
_LON = {"type": "number", "minimum": BBOX["lon"][0], "maximum": BBOX["lon"][1], "description": "经度（WGS84）"}
_LAT = {"type": "number", "minimum": BBOX["lat"][0], "maximum": BBOX["lat"][1], "description": "纬度（WGS84）"}
_FPID = {"type": "string", "pattern": r"^(ADM|AAM|DRM|HS)[ \-_]?\d{1,4}$", "description": "客户热点编号，如 ADM-62"}
STAGES = ["alerted", "staged", "reported", "verified", "dispatched", "arrived", "pumping", "dry", "closed", "reviewed"]

ROWS = {
    "incidents": {
        "title": "993 报案",
        "desc": "一条现场报案。有热点编号最好；只有坐标时我们按 300 m 内最近的热点匹配，匹配不上的会原样退回。",
        "props": {"id": {"type": "string"}, "fp_id": _FPID, "lon": _LON, "lat": _LAT,
                  "at": {"type": "string", "description": "报案时间（ISO）"}, "verified": {"type": "boolean", "description": "是否已核实（核实过的在重规划里加权更多）"},
                  "note": {"type": "string"}, "source": {"type": "string"}},
        "anyOfRequired": [["fp_id"], ["lon", "lat"]],
    },
    "checkins": {
        "title": "承包商到场 check-in",
        "desc": "某驻地实际到位了几台泵，用于预置到位率。",
        "props": {"id": {"type": "string"}, "depot_id": {"type": "string"}, "site_id": {"type": "string"},
                  "units": {"type": "integer", "minimum": 0, "description": "到位台数"}, "at": {"type": "string"}, "by": {"type": "string"}, "note": {"type": "string"}},
        "required": ["units"], "anyOfRequired": [["depot_id"], ["site_id"]],
    },
    "bridges": {
        "title": "桥梁限重 / 净高台账",
        "desc": "重型泵车过不去的桥或下穿道。限重与净高至少给一个。",
        "props": {"id": {"type": "string"}, "name": {"type": "string"}, "lon": _LON, "lat": _LAT,
                  "limit_t": {"type": "number", "exclusiveMinimum": 0, "description": "限重（吨）"}, "clearance_m": {"type": "number", "exclusiveMinimum": 0, "description": "净高（米）"},
                  "owner": {"type": "string"}, "verified": {"type": "boolean"}},
        "required": ["lon", "lat"], "anyOfRequired": [["limit_t"], ["clearance_m"]],
    },
    "contractors": {
        "title": "承包商服务边界",
        "desc": "驻地 → 承包商，或市政 / 片区 → 承包商。",
        "props": {"depot_id": {"type": "string"}, "region": {"type": "string", "enum": ["ADM", "AAM", "DRM"]}, "area": {"type": "string"},
                  "contractor": {"type": "string"}, "contact": {"type": "string"}, "boundary": {"description": "可选的 GeoJSON 面"}},
        "required": ["contractor"], "anyOfRequired": [["depot_id"], ["region"]],
    },
    "events": {
        "title": "降雨事件",
        "desc": "一场真实（或预报中）的雨。date 或 start 给日期；缺 start/end 时按 date 前后几天算窗口。",
        "props": {"event_id": {"type": "string"}, "kind": {"type": "string", "enum": ["historical", "forecast", "live"]},
                  "label": {"type": "string"}, "label_en": {"type": "string"},
                  "date": {"type": "string", "pattern": r"^\d{4}-\d{2}(-\d{2})?"}, "start": {"type": "string", "pattern": r"^\d{4}-\d{2}(-\d{2})?"}, "end": {"type": "string", "pattern": r"^\d{4}-\d{2}(-\d{2})?"},
                  "p24_mm": {"type": "number", "exclusiveMinimum": 0, "description": "缺省片区的 24 h 雨量"}, "p24_by_area": {"type": "object", "description": "分站 / 分片区的 24 h 雨量"},
                  "scenario_key": {"type": "string", "description": "对上的设计情景；留空表示不映射"}, "alert_level": {"type": "string", "enum": ["蓝", "黄", "橙", "红"]},
                  "impacted_areas": {"type": "array", "items": {"type": "string"}, "description": "公开报道点名的积水片区（片区级证据，不是逐点记录）"},
                  "source": {"type": "string"}, "note": {"type": "string"}},
        "required": ["event_id"], "anyOfRequired": [["date"], ["start"]],
    },
    "episodes": {
        "title": "逐点积水记录",
        "desc": "某个热点在某场雨里的一条事实：是否积水、多深多久、派了几台、某个阶段什么时候发生。同一点同一事件的多行会合并。",
        "props": {"id": {"type": "string"}, "fp_id": _FPID, "event_id": {"type": "string"},
                  "flooded": {"type": "boolean", "description": "该点在这场雨里是否积水（对账的地面真值）"},
                  "depth_m": {"type": "number", "minimum": 0}, "duration_min": {"type": "number", "minimum": 0},
                  "units": {"type": "integer", "minimum": 0, "description": "实际派泵台数"}, "first_arrival_min": {"type": "number"}, "drain_min": {"type": "number"},
                  "stage": {"type": "string", "enum": STAGES}, "at": {"type": "string", "description": "该阶段的时间（ISO）"},
                  "source": {"type": "string", "description": "complaint / inspection / 993 / contractor / satellite …"}, "note": {"type": "string"}},
        "required": ["fp_id", "event_id"],
    },
}


def schema_for(name: str) -> dict:
    spec = ROWS[name]
    row = {"type": "object", "properties": spec["props"], "additionalProperties": True}
    if spec.get("required"):
        row["required"] = spec["required"]
    if spec.get("anyOfRequired"):
        row["anyOf"] = [{"required": r} for r in spec["anyOfRequired"]]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://swbrain.local/schemas/{name}.schema.json",
        "title": f"swbrain.{name}/1 · {spec['title']}",
        "description": spec["desc"] + " 顶层 {schema, as_of, source, rows[]}；也接受裸数组。",
        "type": "object",
        "properties": {"schema": {"const": f"swbrain.{name}/1"}, "as_of": {"type": "string"}, "source": {"type": "string"},
                       "rows": {"type": "array", "items": row}},
        "required": ["rows"],
    }


# ---------------------------------------------------------------- 够用的子集校验器（不引依赖）
def _check_row(row: dict, row_schema: dict) -> list[str]:
    errs = []
    if not isinstance(row, dict):
        return ["不是对象"]
    for k in row_schema.get("required", []):
        if row.get(k) is None:
            errs.append(f"缺必填 {k}")
    for group in [a["required"] for a in row_schema.get("anyOf", [])] or []:
        pass
    anys = [a["required"] for a in row_schema.get("anyOf", [])]
    if anys and not any(all(row.get(k) is not None for k in g) for g in anys):
        errs.append("这几组至少要有一组：" + " 或 ".join("+".join(g) for g in anys))
    for k, sp in row_schema.get("properties", {}).items():
        if row.get(k) is None:
            continue
        v = row[k]; ty = sp.get("type")
        if ty == "number" and not isinstance(v, (int, float)) or ty == "integer" and not isinstance(v, int) or ty == "string" and not isinstance(v, str) or ty == "boolean" and not isinstance(v, bool) or ty == "array" and not isinstance(v, list) or ty == "object" and not isinstance(v, dict):
            errs.append(f"{k} 类型应为 {ty}"); continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in sp and v < sp["minimum"]:
                errs.append(f"{k} 不能小于 {sp['minimum']}")
            if "maximum" in sp and v > sp["maximum"]:
                errs.append(f"{k} 不能大于 {sp['maximum']}")
            if "exclusiveMinimum" in sp and v <= sp["exclusiveMinimum"]:
                errs.append(f"{k} 要大于 {sp['exclusiveMinimum']}")
        if sp.get("enum") and v not in sp["enum"]:
            errs.append(f"{k} 应为 {'/'.join(map(str, sp['enum']))} 之一")
        if sp.get("pattern"):
            import re
            if not re.match(sp["pattern"], str(v)):
                errs.append(f"{k} 不符合 {sp['pattern']}")
    return errs


def validate_doc(name: str, doc) -> list[dict]:
    """按 schema 校验一份文档，返回 [{row, errors}]（空 = 全过）。"""
    sch = schema_for(name); row_schema = sch["properties"]["rows"]["items"]
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    out = []
    for i, r in enumerate(rows or []):
        e = _check_row(r, row_schema)
        if e:
            out.append({"row": i, "id": (r.get("id") if isinstance(r, dict) else None), "errors": e})
    return out


def write_all(out_dir: Path | None = None) -> list[str]:
    d = out_dir or ROOT / "exchange" / "schemas"
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for name in ROWS:
        p = d / f"{name}.schema.json"
        p.write_text(json.dumps(schema_for(name), ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        written.append(p.name)
    return written


def cross_check() -> dict:
    """对表：① 示例文件要过 schema；② schema 判不合格的行，feeds.validate 也要判不合格（反之不强求，validate 更严）。"""
    from . import feeds
    out = {"examples": {}, "disagree": []}
    for name in ROWS:
        for ex in sorted((ROOT / "exchange" / "examples").glob(f"{name}*.json")):
            doc = json.loads(ex.read_text(encoding="utf-8"))
            bad = validate_doc(name, doc)
            out["examples"][ex.name] = {"rows": len(doc.get("rows") or []), "schema_bad": len(bad), "detail": bad[:3]}
            rows = doc.get("rows") or []
            ok_rows, rejected = feeds.validate(name, rows)
            bad_idx = {b["row"] for b in bad}; rej_idx = {r["row"] for r in rejected}
            for i in sorted(bad_idx - rej_idx):
                out["disagree"].append({"file": ex.name, "row": i, "schema": "不合格", "validate": "合格", "why": [b for b in bad if b["row"] == i][0]["errors"]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="交换契约的 JSON Schema：生成与对表")
    ap.add_argument("--check", action="store_true", help="只对表，不写文件")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not a.check:
        print("已写：", ", ".join(write_all()))
    r = cross_check()
    for f, v in r["examples"].items():
        print(f"  {f}: {v['rows']} 行，schema 不合格 {v['schema_bad']}" + (f" → {v['detail']}" if v["schema_bad"] else ""))
    if r["disagree"]:
        print("schema 与 feeds.validate 不一致：")
        for d in r["disagree"][:10]:
            print("   ", d)
        return 1
    print("schema 与 feeds.validate 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
