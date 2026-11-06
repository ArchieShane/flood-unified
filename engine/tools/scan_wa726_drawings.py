import PyPDF2
from collections import defaultdict
from pathlib import Path

pdf_file = Path(r'D:\阿布扎比\排水智能体\20261019\WA-726-2.pdf')
reader = PyPDF2.PdfReader(pdf_file)

print(f'WA-726-2 标准图集内容扫描')
print('='*80 + '\n')
print(f'总页数: {len(reader.pages)}\n')

# 扫描所有页
drawings = []
keywords = {
    'manhole': '检查井',
    'pump station': '泵站',
    'wet well': '集水井',
    'detention': '调蓄',
    'pond': '池',
    'inlet': '雨水口',
    'pipe': '管道',
    'chamber': '井室',
    'valve': '阀门',
    'outfall': '出水口'
}

for i in range(len(reader.pages)):
    text = reader.pages[i].extract_text()

    for kw, cn in keywords.items():
        if kw in text.lower():
            # 提取图纸信息
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            title_line = None
            for line in lines[:30]:
                if any(marker in line for marker in ['DWG', 'DETAIL', 'TYPICAL', 'SECTION']):
                    title_line = line
                    break

            if title_line:
                drawings.append({
                    'page': i+1,
                    'keyword': kw,
                    'cn': cn,
                    'title': title_line[:100]
                })
                break

# 按关键词分组
by_type = defaultdict(list)
for d in drawings:
    by_type[d['keyword']].append(d)

print(f'找到 {len(drawings)} 张相关图纸:\n')

for kw in sorted(by_type.keys()):
    items = by_type[kw]
    cn = items[0]['cn']
    print(f'\n【{cn.upper()} ({kw.upper()})】: {len(items)} 张')
    for item in items[:8]:
        print(f"  第 {item['page']:3d} 页: {item['title']}")

# 保存详细列表
output_file = Path(r'D:\阿布扎比\排水智能体\docs\WA-726-2_标准图集目录.txt')
with open(output_file, 'w', encoding='utf-8', errors='ignore') as f:
    f.write('WA-726-2 标准图集详细目录\n')
    f.write('='*80 + '\n\n')

    for kw in sorted(by_type.keys()):
        items = by_type[kw]
        cn = items[0]['cn']
        f.write(f'\n{cn} ({kw}): {len(items)} 张\n')
        f.write('-'*80 + '\n')
        for item in items:
            f.write(f"第 {item['page']} 页: {item['title']}\n")

print(f'\n详细目录已保存: {output_file}')
