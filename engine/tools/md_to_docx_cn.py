"""Markdown → Word（中文字体、表格、嵌套列表、粗体、行内代码）。用法：python tools/md_to_docx_cn.py in.md out.docx"""
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

CN, EN, MONO = "微软雅黑", "Calibri", "Consolas"


def set_font(run, name=EN, cn=CN, size=None, bold=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    rf.set(qn("w:eastAsia"), cn)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)


def add_inline(par, text, size=None, bold_all=False):
    text = text.replace("<br>", "\n")
    for tok in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            set_font(par.add_run(tok[2:-2]), size=size, bold=True)
        elif tok.startswith("`") and tok.endswith("`"):
            set_font(par.add_run(tok[1:-1]), name=MONO, size=(size or 10.5) - 1, color=(0x60, 0x30, 0x30), bold=bold_all or None)
        else:
            set_font(par.add_run(tok), size=size, bold=True if bold_all else None)


def shade(cell, hexcolor):
    tcpr = cell._element.get_or_add_tcPr()
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear")
    sh.set(qn("w:color"), "auto")
    sh.set(qn("w:fill"), hexcolor)
    tcpr.append(sh)


def table(doc, rows):
    rows = [r for r in rows if not re.match(r"^\|\s*:?-{2,}", r)]
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    n = max(len(c) for c in cells)
    t = doc.add_table(rows=len(cells), cols=n)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row in enumerate(cells):
        for j in range(n):
            c = t.cell(i, j)
            c.text = ""
            p = c.paragraphs[0]
            add_inline(p, row[j] if j < len(row) else "", size=9, bold_all=(i == 0))
            if i == 0:
                shade(c, "D9E2F3")
    doc.add_paragraph()


def main(src, dst):
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = EN
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), CN)
    for s in ("Heading 1", "Heading 2", "Heading 3", "Title"):
        hs = doc.styles[s]
        hs.font.name = EN
        hs.element.rPr.rFonts.set(qn("w:eastAsia"), CN)
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Cm(2.0)
        sec.top_margin = sec.bottom_margin = Cm(2.0)
    lines = Path(src).read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"!\[(.*?)\]\((.+?)\)\s*$", ln.strip())
        if m:                                            # 插图：路径相对 md 文件，宽度占满版心，图注在下
            img = (Path(src).parent / m.group(2)).resolve()
            if img.exists():
                doc.add_picture(str(img), width=Cm(17))
                if m.group(1):
                    cap = doc.add_paragraph()
                    set_font(cap.add_run(m.group(1)), size=9, color=(0x55, 0x55, 0x55))
            i += 1
            continue
        if ln.startswith("```"):                       # 代码块：等宽、照原样
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.left_indent = Cm(0.5)
                set_font(p.add_run(lines[i]), name=MONO, cn=CN, size=9)
                i += 1
            i += 1
            continue
        if ln.startswith("|"):
            blk = []
            while i < len(lines) and lines[i].startswith("|"):
                blk.append(lines[i])
                i += 1
            table(doc, blk)
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1))
            h = doc.add_heading(level=0 if lvl == 1 else lvl - 1)
            add_inline(h, m.group(2))
            i += 1
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", ln)
        if m:
            depth = len(m.group(1)) // 2
            if m.group(2)[0].isdigit():                 # 编号照抄原文：Word 的 List Number 会跨列表连续编号
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.75 * (depth + 1))
                p.paragraph_format.first_line_indent = Cm(-0.6)
                add_inline(p, f"{m.group(2)} {m.group(3)}")
            else:
                p = doc.add_paragraph(style="List Bullet" + (f" {min(depth + 1, 3)}" if depth else ""))
                add_inline(p, m.group(3))
            i += 1
            continue
        if ln.startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.8)
            add_inline(p, ln.lstrip("> ").strip())
            for r in p.runs:
                r.italic = True
            i += 1
            continue
        if ln.strip():
            p = doc.add_paragraph()
            add_inline(p, ln.strip())
        i += 1
    doc.save(dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
