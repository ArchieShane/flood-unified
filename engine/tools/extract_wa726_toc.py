import PyPDF2
from pathlib import Path

pdf_file = Path(r'D:\阿布扎比\排水智能体\20261019\WA-726-1.pdf')
reader = PyPDF2.PdfReader(pdf_file)

print(f"WA-726-1 设计手册分析")
print(f"{'='*80}\n")
print(f"总页数: {len(reader.pages)}\n")

# 读取前 30 页找目录
print("查找目录章节...")
print("="*80)

toc_pages = []
for i in range(min(30, len(reader.pages))):
    text = reader.pages[i].extract_text()

    # 检查是否是目录页
    if any(keyword in text.lower() for keyword in ['table of contents', 'contents', 'chapter']):
        toc_pages.append(i)
        print(f"\n第 {i+1} 页（目录页）：")
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        # 打印包含章节编号的行
        for line in lines:
            # 章节行通常有数字开头或包含 "Chapter" 等
            if any(c.isdigit() for c in line[:5]) or 'chapter' in line.lower() or 'section' in line.lower():
                print(f"  {line}")

# 如果没找到目录，尝试直接读取关键章节
if not toc_pages:
    print("\n未找到明确的目录页，尝试搜索关键章节...")

    keywords = ['design criteria', 'facility', 'pipe', 'pump', 'storage', 'pond', 'detention', 'manhole']

    for i in range(0, min(50, len(reader.pages)), 5):
        text = reader.pages[i].extract_text().lower()
        for kw in keywords:
            if kw in text:
                print(f"  第 {i+1} 页提到: {kw}")
                break

print("\n" + "="*80)
print("保存完整目录供后续分析...")

# 保存前 30 页的文本用于分析
output_file = Path(r'D:\阿布扎比\排水智能体\docs\WA-726-1_TOC.txt')
with open(output_file, 'w', encoding='utf-8', errors='ignore') as f:
    f.write(f"WA-726-1 前 30 页内容\n")
    f.write("="*80 + "\n\n")

    for i in range(min(30, len(reader.pages))):
        f.write(f"\n{'='*80}\n")
        f.write(f"第 {i+1} 页\n")
        f.write(f"{'='*80}\n")
        text = reader.pages[i].extract_text()
        f.write(text)
        f.write("\n\n")

print(f"已保存到: {output_file}")
