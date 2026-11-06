import PyPDF2
from pathlib import Path

pdf_file = Path(r'D:\阿布扎比\排水智能体\20261019\WA-726-1.pdf')
reader = PyPDF2.PdfReader(pdf_file)

output_file = Path(r'D:\阿布扎比\排水智能体\docs\WA-726-1_设施设计要求摘要.txt')

with open(output_file, 'w', encoding='utf-8', errors='ignore') as f:
    f.write("WA-726-1 设施设计要求摘要\n")
    f.write("="*80 + "\n\n")

    # 关键章节和页码
    sections = {
        'Stormwater Ponds (调蓄池)': (50, 55),  # 第51-56页附近
        'Pump Stations (泵站)': (55, 60),
        'Manholes and Access (检查井)': (38, 42),
        'Gravity Pipe Design (重力管)': (33, 38),
        'Pressure Pipe Design (压力管)': (55, 60),
    }

    for section_name, (start, end) in sections.items():
        f.write(f"\n{'='*80}\n")
        f.write(f"{section_name}\n")
        f.write(f"{'='*80}\n\n")

        print(f"\n提取: {section_name} (第 {start+1}-{end+1} 页)")

        for i in range(start, min(end, len(reader.pages))):
            text = reader.pages[i].extract_text()

            # 检查是否包含相关内容
            relevant = False
            for keyword in ['design', 'criteria', 'requirement', 'spacing', 'depth', 'diameter', 'slope', 'minimum', 'maximum']:
                if keyword.lower() in text.lower():
                    relevant = True
                    break

            if relevant:
                f.write(f"\n--- 第 {i+1} 页 ---\n")
                f.write(text)
                f.write("\n\n")

    # 另外提取关键表格页（通常在40-80页）
    f.write(f"\n{'='*80}\n")
    f.write("关键表格和参数页\n")
    f.write(f"{'='*80}\n\n")

    for i in range(30, min(80, len(reader.pages))):
        text = reader.pages[i].extract_text().lower()

        # 查找表格特征
        if 'table' in text or 'minimum' in text or 'maximum' in text:
            # 检查是否有数字参数
            if any(str(d) in text for d in ['100', '150', '200', '300', '500', '1000', '0.5', '1.0', '2.0']):
                f.write(f"\n--- 第 {i+1} 页（可能包含参数表）---\n")
                f.write(reader.pages[i].extract_text())
                f.write("\n\n")

print(f"\n提取完成，保存到: {output_file}")
print("\n现在打印调蓄池设计要点...")

# 打印调蓄池章节的关键内容
print("\n" + "="*80)
print("调蓄池设计要点（第51-56页摘要）")
print("="*80)

for i in range(50, min(56, len(reader.pages))):
    text = reader.pages[i].extract_text()
    if 'pond' in text.lower() or 'storage' in text.lower() or 'detention' in text.lower():
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for line in lines[:30]:
            if any(kw in line.lower() for kw in ['depth', 'side slope', 'freeboard', 'outlet', 'volume', 'area']):
                print(f"  {line}")
