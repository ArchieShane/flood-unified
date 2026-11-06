"""python -m swbrain.ontology [--schema | --mermaid | --check]：导出本体声明 / 关系图 / 自检。"""
import argparse
import json
import sys

from . import schema


def regenerate_tables(txt: str) -> str:
    """把文档里 <!-- gen:xxx --> … <!-- /gen --> 之间的表格按当前声明重出；没有标记的文档原样返回。"""
    import re
    S = schema
    NL = "\n"
    ot_rows = NL.join(f"| `{o.api}` | {o.zh} / {o.en} | `{o.pk}` | {len(o.properties)} | {'随情景' if o.scoped else '静态'} | {o.desc} |" for o in S.OBJECT_TYPES)
    fp = S.OT["FloodPoint"]; fs = S.OT["FloodState"]
    prop_rows = NL.join(f"| `{p.name}` | {p.type} | {p.zh} | {p.desc or ''} | {('规则 `' + p.derived_by + '`') if p.derived_by else (p.source or '')} |" for p in fp.properties)
    state_rows = NL.join(f"| `{p.name}` | {p.type} | {p.zh} | {p.desc or ''} | {('规则 `' + p.derived_by + '`') if p.derived_by else ''} |" for p in fs.properties)
    link_rows = NL.join(f"| `{l.src}.{l.api}` → `{l.dst}` | {l.zh} | {l.card} | {l.how} |" for l in S.LINK_TYPES)
    rule_rows = NL.join(f"| `{r.api}` | {r.zh} | {r.kind} | `{r.applies_to}` | {r.logic_zh} | {r.source} |" for r in S.RULES)
    act_rows = NL.join(f"| `{a.api}` | {a.zh} | {', '.join(('**' + p.name + '**' if p.required else p.name) + ':' + p.type for p in a.params)} | {a.side_effect} | {a.examples[0] if a.examples else ''} |" for a in S.ACTIONS)
    blocks = {"objects": f"| api | 名称 | 主键 | 属性数 | 作用域 | 说明 |{NL}|---|---|---|---|---|---|{NL}{ot_rows}",
              "fp_props": f"| 属性 | 类型 | 名称 | 说明 | 来源 / 派生 |{NL}|---|---|---|---|---|{NL}{prop_rows}",
              "state_props": f"| 属性 | 类型 | 名称 | 说明 | 派生 |{NL}|---|---|---|---|---|{NL}{state_rows}",
              "mermaid": f"```mermaid{NL}{S.mermaid()}{NL}```",
              "links": f"| 关系 | 名称 | 基数 | 怎么对齐 |{NL}|---|---|---|---|{NL}{link_rows}",
              "rules": f"| api | 名称 | 类型 | 作用于 | 逻辑 | 依据 |{NL}|---|---|---|---|---|---|{NL}{rule_rows}",
              "actions": f"| api | 名称 | 参数（**粗体**必填） | 副作用 | 示例问法 |{NL}|---|---|---|---|---|{NL}{act_rows}"}
    for k, body in blocks.items():
        txt = re.sub(r"(<!-- gen:%s -->)\n.*?\n(<!-- /gen -->)" % k, lambda m: f"{m.group(1)}{NL}{body}{NL}{m.group(2)}", txt, flags=re.S)
    txt = re.sub(r"## 2\. 对象类型（\d+ 个）", f"## 2. 对象类型（{len(S.OBJECT_TYPES)} 个）", txt)
    txt = re.sub(r"## 3\. 关系（\d+ 条）", f"## 3. 关系（{len(S.LINK_TYPES)} 条）", txt)
    txt = re.sub(r"## 4\. 规则（\d+ 条，都可执行）", f"## 4. 规则（{len(S.RULES)} 条，都可执行）", txt)
    txt = re.sub(r"## 5\. 动作（\d+ 个，智能体唯一能做的事）", f"## 5. 动作（{len(S.ACTIONS)} 个，智能体唯一能做的事）", txt)
    return txt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", action="store_true", help="本体全貌 JSON")
    ap.add_argument("--mermaid", action="store_true", help="关系图（Mermaid）")
    ap.add_argument("--check", action="store_true", help="声明自检")
    ap.add_argument("--doc", action="store_true", help="重新生成 docs/本体模型.md 的表格部分")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if a.doc:
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "docs" / "本体模型.md"
        txt = p.read_text(encoding="utf-8")
        new = regenerate_tables(txt)
        p.write_text(new, encoding="utf-8", newline="\n")
        print("regenerated", p, "|", "changed" if new != txt else "unchanged")
        return 0
    if a.mermaid:
        print(schema.mermaid())
    elif a.check or not a.schema:
        errs = schema.check()
        print("本体自检：" + ("通过" if not errs else "\n".join(errs)))
        print(f"对象类型 {len(schema.OBJECT_TYPES)} · 关系 {len(schema.LINK_TYPES)} · 规则 {len(schema.RULES)} · 动作 {len(schema.ACTIONS)}")
        return 0 if not errs else 1
    if a.schema:
        print(json.dumps(schema.to_dict(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
