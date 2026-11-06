import PyPDF2
from pathlib import Path

pdf_file = Path(r'D:\阿布扎比\排水智能体\20261019\WA-726-2.pdf')
reader = PyPDF2.PdfReader(pdf_file)

print('WA-726-2 关键图纸内容提取')
print('='*80 + '\n')

# 关键图纸页码
key_pages = {
    '检查井典型详图': [17, 18, 19, 20, 21],
    '泵站详图': [157, 158, 159],
    '调蓄池详图': [122, 141],
    '管道安装详图': [16, 47],
}

output_file = Path(r'D:\阿布扎比\排水智能体\docs\WA-726-2_关键图纸摘要.txt')

with open(output_file, 'w', encoding='utf-8', errors='ignore') as f:
    f.write('WA-726-2 关键图纸内容摘要\n')
    f.write('='*80 + '\n\n')

    for section, pages in key_pages.items():
        f.write(f'\n{"="*80}\n')
        f.write(f'{section}\n')
        f.write(f'{"="*80}\n\n')

        print(f'\n提取: {section}')

        for page_num in pages:
            if page_num <= len(reader.pages):
                text = reader.pages[page_num - 1].extract_text()

                f.write(f'\n--- 第 {page_num} 页 ---\n')
                f.write(text)
                f.write('\n\n')

                # 提取数字参数（尺寸）
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                dimensions = []
                for line in lines:
                    # 查找尺寸标注（通常包含数字和单位）
                    if any(unit in line for unit in ['mm', 'MM', 'm', 'M', 'dia', 'DIA', 'Ø', 'x']):
                        if any(c.isdigit() for c in line):
                            dimensions.append(line)

                if dimensions:
                    print(f'  第 {page_num} 页: 找到 {len(dimensions)} 处尺寸标注')

print(f'\n详细内容已保存: {output_file}')

# 现在尝试提取典型检查井的关键参数
print('\n' + '='*80)
print('检查井关键参数提取（第17-21页）')
print('='*80)

for i in range(16, 22):
    if i < len(reader.pages):
        text = reader.pages[i].extract_text()

        # 查找关键参数
        params = []
        keywords = ['diameter', 'dia', 'depth', 'cover', 'thickness', 'spacing', 'reinforcement', 'base']

        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for line in lines:
            # 查找包含关键词和数字的行
            if any(kw in line.lower() for kw in keywords):
                if any(c.isdigit() for c in line):
                    params.append(line)

        if params:
            print(f'\n第 {i+1} 页参数:')
            for p in params[:10]:  # 每页最多显示10条
                print(f'  {p[:80]}')
