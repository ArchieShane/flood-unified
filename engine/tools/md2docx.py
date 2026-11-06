#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Convert Markdown to Word DOCX format."""

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import re
import sys


def process_table(doc, table_lines):
    """Process markdown table and add to document."""
    # Remove separator line
    clean_lines = [l for l in table_lines if not re.match(r'^\|[\s\-:]+\|', l)]
    if len(clean_lines) < 2:
        return

    # Parse rows
    rows = []
    for line in clean_lines:
        cells = [c.strip() for c in line.split('|')[1:-1]]
        rows.append(cells)

    if not rows:
        return

    # Create table
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = 'Light Grid Accent 1'

    # Fill table
    for i, row_data in enumerate(rows):
        row = table.rows[i]
        for j, cell_text in enumerate(row_data):
            row.cells[j].text = cell_text
            # Bold header row
            if i == 0:
                for paragraph in row.cells[j].paragraphs:
                    for run in paragraph.runs:
                        run.font.bold = True


def md_to_docx(md_file, docx_file):
    """Convert markdown file to Word document."""
    doc = Document()

    # Set default font
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Microsoft YaHei'
    font.size = Pt(11)

    with open(md_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    i = 0
    in_code_block = False
    code_lines = []
    in_table = False
    table_lines = []

    while i < len(lines):
        line = lines[i].rstrip()

        # Code block
        if line.startswith('```'):
            if not in_code_block:
                in_code_block = True
                code_lines = []
            else:
                # End code block
                if code_lines:
                    p = doc.add_paragraph('\n'.join(code_lines))
                    p.style = 'Normal'
                    p_format = p.paragraph_format
                    p_format.left_indent = Inches(0.5)
                    for run in p.runs:
                        run.font.name = 'Consolas'
                        run.font.size = Pt(9)
                        run.font.color.rgb = RGBColor(0, 0, 128)
                in_code_block = False
                code_lines = []
            i += 1
            continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        # Table detection
        if '|' in line and line.strip().startswith('|'):
            if not in_table:
                in_table = True
                table_lines = [line]
            else:
                table_lines.append(line)
            i += 1
            # Check if next line is not a table
            if i >= len(lines) or '|' not in lines[i]:
                # Process table
                process_table(doc, table_lines)
                in_table = False
                table_lines = []
            continue

        # Headers
        if line.startswith('# '):
            p = doc.add_heading(line[2:], level=1)
        elif line.startswith('## '):
            p = doc.add_heading(line[3:], level=2)
        elif line.startswith('### '):
            p = doc.add_heading(line[4:], level=3)
        elif line.startswith('#### '):
            p = doc.add_heading(line[5:], level=4)
        # Horizontal rule
        elif line.strip() == '---':
            doc.add_paragraph('_' * 80)
        # Bullet list
        elif line.startswith('- ') or line.startswith('* '):
            text = line[2:]
            p = doc.add_paragraph(text, style='List Bullet')
        # Numbered list
        elif re.match(r'^\d+\. ', line):
            text = re.sub(r'^\d+\. ', '', line)
            p = doc.add_paragraph(text, style='List Number')
        # Empty line
        elif line.strip() == '':
            doc.add_paragraph()
        # Normal paragraph
        else:
            p = doc.add_paragraph(line)

        i += 1

    doc.save(docx_file)
    print(f'Converted {md_file} -> {docx_file}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        # Convert the two DMT documents
        md_to_docx(
            'docs/DMT_Stormwater_Volume_Formula_科学性分析_20261029.md',
            'docs/DMT_Stormwater_Volume_Formula_科学性分析_20261029.docx'
        )
        md_to_docx(
            'docs/DMT公式与我们工作的关系对比_20261029.md',
            'docs/DMT公式与我们工作的关系对比_20261029.docx'
        )
    else:
        # Convert specified file
        md_file = sys.argv[1]
        docx_file = md_file.replace('.md', '.docx')
        md_to_docx(md_file, docx_file)
