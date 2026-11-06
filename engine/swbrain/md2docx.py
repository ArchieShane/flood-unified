"""Markdown → Word（.docx）。给客户 / 内部传阅用，本仓库的文档都是 Markdown，这里转成能直接发的 Word。

支持这套文档里实际用到的构件：一到四级标题、段落（**粗体**、`等宽`、[文字](链接)）、无序与有序列表、表格（首行表头）、引用块、分隔线、围栏代码块。
中文字体单独设 east-asia，否则 Word 里中文会掉回默认字体。

  python -m swbrain.md2docx docs/PoC演示脚本_2026-09-23.md              # 同名 .docx
  python -m swbrain.md2docx docs/a.md docs/b.md -o out/word            # 批量到目录
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATIN = "Calibri"
EASTASIA = "微软雅黑"
MONO = "Consolas"


def _set_font(run, name: str = LATIN, east: str = EASTASIA, size=None, bold=None, mono=False):
    from docx.shared import Pt
    from docx.oxml.ns import qn
    run.font.name = MONO if mono else name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), MONO if mono else east)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold


_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")
_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")


def _add_inline(par, text: str, size=None, base_bold=False):
    """把一行里的 **粗体**、`等宽`、[文字](链接) 拆成 run。"""
    for piece in _INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            r = par.add_run(piece[2:-2]); _set_font(r, size=size, bold=True)
        elif piece.startswith("`") and piece.endswith("`"):
            r = par.add_run(piece[1:-1]); _set_font(r, size=(size - 0.5) if size else 9.5, mono=True)
        elif piece.startswith("["):
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", piece)
            r = par.add_run(m.group(1)); _set_font(r, size=size, bold=base_bold)
            r2 = par.add_run(f"（{m.group(2)}）"); _set_font(r2, size=(size - 1) if size else 8.5)
            r2.font.color.rgb = __import__("docx.shared", fromlist=["RGBColor"]).RGBColor(0x66, 0x66, 0x66)
        else:
            r = par.add_run(piece); _set_font(r, size=size, bold=base_bold)


def _table(doc, rows: list[list[str]]):
    from docx.shared import Pt
    from docx.enum.table import WD_TABLE_ALIGNMENT
    t = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
    t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            c = t.cell(i, j); c.text = ""
            par = c.paragraphs[0]; par.paragraph_format.space_before = Pt(1); par.paragraph_format.space_after = Pt(1)
            _add_inline(par, cell, size=9, base_bold=(i == 0))
    return t


def _footer_page_number(section, title: str):
    """页脚：左边文档名，右边「第 N 页」（用 PAGE 域，打印和翻页都对）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    from docx.enum.text import WD_TAB_ALIGNMENT
    p = section.footer.paragraphs[0]
    p.paragraph_format.tab_stops.add_tab_stop(section.page_width - section.left_margin - section.right_margin, WD_TAB_ALIGNMENT.RIGHT)
    r = p.add_run(f"{title}	第 "); _set_font(r, size=8.5)
    fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE")
    p._p.append(fld)
    r2 = p.add_run(" 页"); _set_font(r2, size=8.5)


def _block_start(lines: list[str], i: int) -> bool:
    """第 i 行是不是新块的开头（代码、表格、标题、分隔线、引用、列表、图片），是就不并进上一段。"""
    st = lines[i].strip()
    return (st.startswith(("```", "|", ">")) or bool(re.match(r"^#{1,4}\s", st)) or bool(re.match(r"^(-{3,}|\*{3,})$", st))
            or bool(_IMAGE.match(st))
            or bool(re.match(r"^(\s*)([-*]|\d+\.)\s+", lines[i])))


def _join_soft(parts: list[str]) -> str:
    """软换行合并：中文之间直接接上，西文之间补一个空格。"""
    out = parts[0]
    for s in parts[1:]:
        out += ("" if (out[-1:] > "⺀" or s[:1] > "⺀") else " ") + s
    return out


def convert(md_path: Path, out_path: Path | None = None) -> Path:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    md = md_path.read_text(encoding="utf-8")
    doc = Document()
    for s in ("Normal", "List Bullet", "List Number"):
        try:
            st = doc.styles[s]; st.font.name = LATIN; st.font.size = Pt(10.5)
            st.element.rPr.rFonts.set(__import__("docx.oxml.ns", fromlist=["qn"]).qn("w:eastAsia"), EASTASIA)
        except KeyError:
            pass
    for h in ("Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        try:
            st_ = doc.styles[h]; st_.font.name = LATIN
            st_.element.rPr.rFonts.set(__import__("docx.oxml.ns", fromlist=["qn"]).qn("w:eastAsia"), EASTASIA)
        except KeyError:
            pass
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)                # A4（python-docx 缺省是 Letter）
    sec.left_margin = sec.right_margin = Cm(2.2); sec.top_margin = sec.bottom_margin = Cm(2.0)
    _footer_page_number(sec, md_path.stem)

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        st = ln.strip()
        # 图片：![说明](相对路径)；路径相对 Markdown 文件所在目录解析。宽度按页面版心算。
        m = _IMAGE.match(st)
        if m:
            src = m.group(2)
            if not re.match(r"^[a-z]+://", src, re.I):
                p_img = (md_path.parent / src).resolve()
                if p_img.exists():
                    from docx.shared import Cm as _Cm
                    ip = doc.add_paragraph(); ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    ip.paragraph_format.space_before = Pt(6); ip.paragraph_format.space_after = Pt(2)
                    usable = sec.page_width - sec.left_margin - sec.right_margin
                    ip.add_run().add_picture(str(p_img), width=usable)
                    if m.group(1):
                        cap = doc.add_paragraph(); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        cap.paragraph_format.space_after = Pt(10)
                        r = cap.add_run(m.group(1)); _set_font(r, size=8.5)
                        r.font.color.rgb = RGBColor(0x5E, 0x6E, 0x7B)
                else:
                    p = doc.add_paragraph(); r = p.add_run(f"[缺图：{src}]")
                    _set_font(r, size=9); r.font.color.rgb = RGBColor(0xB2, 0x3A, 0x2E)
            i += 1
            continue
        # 围栏代码块
        if st.startswith("```"):
            i += 1; buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.6); p.paragraph_format.space_after = Pt(6)
            r = p.add_run("\n".join(buf)); _set_font(r, size=9, mono=True)
            continue
        # 表格
        if st.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not re.match(r"^[\s:\-]+$", "".join(row)):
                    rows.append(row)
                i += 1
            _table(doc, rows)
            doc.add_paragraph().paragraph_format.space_after = Pt(4)
            continue
        # 标题：用真正的 Heading 样式，Word 的导航窗格与自动目录才认
        m = re.match(r"^(#{1,4})\s+(.*)$", st)
        if m:
            lvl = len(m.group(1)); text = m.group(2)
            p = doc.add_paragraph(style=f"Heading {lvl}")
            p.paragraph_format.space_before = Pt(14 if lvl <= 2 else 10); p.paragraph_format.space_after = Pt(4)
            _add_inline(p, text, size={1: 18, 2: 14, 3: 12, 4: 11}[lvl], base_bold=True)
            for r in p.runs:
                r.bold = True
                r.font.color.rgb = RGBColor(0x0B, 0x3D, 0x6B) if lvl == 1 else RGBColor(0x1B, 0x2A, 0x3A)
            if lvl == 1:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            i += 1
            continue
        # 分隔线
        if re.match(r"^(-{3,}|\*{3,})$", st):
            p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(2)
            r = p.add_run("─" * 40); _set_font(r, size=8); r.font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
            i += 1
            continue
        # 引用
        if st.startswith(">"):
            p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.6); p.paragraph_format.space_after = Pt(4)
            _add_inline(p, st.lstrip("> ").strip(), size=10)
            for r in p.runs:
                r.font.color.rgb = RGBColor(0x44, 0x44, 0x44); r.italic = True
            i += 1
            continue
        # 列表
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if m:
            indent = len(m.group(1)) // 2
            style = "List Number" if m.group(2)[0].isdigit() else "List Bullet"
            p = doc.add_paragraph(style=style)
            p.paragraph_format.left_indent = Cm(0.75 + 0.5 * indent); p.paragraph_format.space_after = Pt(2)
            _add_inline(p, m.group(3), size=10.5)
            i += 1
            continue
        # 空行
        if not st:
            i += 1
            continue
        # 普通段落：连续的非空行是同一段（Markdown 的软换行），合并；原来每行一段，长句在 Word 里被拆成两段
        buf = [st]; i += 1
        while i < len(lines) and lines[i].strip() and not _block_start(lines, i):
            buf.append(lines[i].strip()); i += 1
        p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(6)
        _add_inline(p, _join_soft(buf), size=10.5)

    out = out_path or md_path.with_suffix(".docx")
    if out.is_dir() or out.suffix.lower() != ".docx":                   # -o 指的是目录（可能还不存在）
        out.mkdir(parents=True, exist_ok=True)
        out = out / (md_path.stem + ".docx")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Markdown → Word")
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", default=None, help="输出文件或目录；缺省与源同名同目录")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    outp = Path(a.out) if a.out else None
    for f in a.files:
        src = Path(f)
        if not src.is_absolute():
            src = (ROOT / f) if (ROOT / f).exists() else src
        p = convert(src, outp)
        print(f"{src.name} → {p}  ({p.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
