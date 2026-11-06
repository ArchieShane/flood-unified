#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Convert markdown L2+suggested methodology docs to Word format."""
import re
from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

def md_to_docx(md_file, docx_file):
    doc = Document()

    # Set normal style
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)

    content = Path(md_file).read_text(encoding='utf-8')

    # Split by lines
    lines = content.split('\n')
    in_table = False
    table_lines = []
    in_code = False
    code_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip horizontal rules
        if line.strip() == '---':
            i += 1
            continue

        # Code block
        if line.strip().startswith('```'):
            if not in_code:
                in_code = True
                code_lines = []
            else:
                # End code block
                para = doc.add_paragraph()
                para.style = 'Normal'
                run = para.add_run('\n'.join(code_lines))
                run.font.name = 'Consolas'
                run.font.size = Pt(9)
                in_code = False
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Headers
        if line.startswith('# '):
            p = doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith('## '):
            p = doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith('### '):
            p = doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith('#### '):
            p = doc.add_heading(line[5:].strip(), level=4)

        # Table detection
        elif '|' in line and not in_table:
            # Start of table
            table_lines = [line]
            in_table = True
        elif in_table:
            if '|' in line:
                table_lines.append(line)
            else:
                # End of table - process it
                process_table(doc, table_lines)
                table_lines = []
                in_table = False
                # Process current line
                if line.strip():
                    add_paragraph(doc, line)

        # Regular paragraphs
        elif line.strip():
            add_paragraph(doc, line)

        i += 1

    # Handle any remaining table
    if in_table and table_lines:
        process_table(doc, table_lines)

    doc.save(docx_file)
    print(f"Generated: {docx_file}")

def process_table(doc, lines):
    """Process markdown table into Word table."""
    if len(lines) < 2:
        return

    # Parse header
    header_cells = [c.strip() for c in lines[0].split('|') if c.strip()]

    # Skip separator line
    # Parse data rows
    data_rows = []
    for line in lines[2:]:
        if '|' in line:
            cells = [c.strip() for c in line.split('|') if c.strip()]
            if cells:
                data_rows.append(cells)

    if not data_rows:
        return

    # Create table
    table = doc.add_table(rows=1 + len(data_rows), cols=len(header_cells))
    table.style = 'Light Grid Accent 1'

    # Header row
    for j, cell_text in enumerate(header_cells):
        cell = table.rows[0].cells[j]
        cell.text = cell_text
        cell.paragraphs[0].runs[0].font.bold = True

    # Data rows
    for i, row_data in enumerate(data_rows):
        for j, cell_text in enumerate(row_data):
            if j < len(table.rows[i + 1].cells):
                table.rows[i + 1].cells[j].text = cell_text

def add_paragraph(doc, text):
    """Add a paragraph with inline formatting."""
    para = doc.add_paragraph()

    # Split by bold markers
    parts = re.split(r'(\*\*[^*]+\*\*)', text)

    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            # Bold text
            run = para.add_run(part[2:-2])
            run.font.bold = True
        elif part.startswith('`') and part.endswith('`'):
            # Code
            run = para.add_run(part[1:-1])
            run.font.name = 'Consolas'
            run.font.size = Pt(10)
        else:
            # Regular text - handle inline code
            code_parts = re.split(r'(`[^`]+`)', part)
            for cp in code_parts:
                if cp.startswith('`') and cp.endswith('`'):
                    run = para.add_run(cp[1:-1])
                    run.font.name = 'Consolas'
                    run.font.size = Pt(10)
                elif cp:
                    para.add_run(cp)

if __name__ == '__main__':
    docs_dir = Path(__file__).parent.parent / 'docs'

    # English version
    md_to_docx(
        docs_dir / 'L2_Suggested_Methodology_EN.md',
        docs_dir / 'L2_Suggested_Methodology_EN.docx'
    )

    # Chinese version
    md_to_docx(
        docs_dir / 'L2建议措施方法说明_中文_v2.md',
        docs_dir / 'L2建议措施方法说明_中文.docx'
    )
