import PyPDF2
from pathlib import Path

pdf_dir = Path(r'D:\阿布扎比\排水智能体\20261019')

for pdf_file in sorted(pdf_dir.glob('WA-726-*.pdf')):
    print(f"\n{'='*60}")
    print(f"{pdf_file.name}")
    print(f"{'='*60}")

    try:
        reader = PyPDF2.PdfReader(pdf_file)
        print(f"总页数: {len(reader.pages)}")

        # 读取前 3 页
        for i in range(min(3, len(reader.pages))):
            text = reader.pages[i].extract_text()
            lines = [l.strip() for l in text.split('\n') if l.strip()]

            print(f"\n--- 第 {i+1} 页（前 15 行）---")
            for line in lines[:15]:
                print(f"  {line}")

        # 读取目录页（通常在前几页）
        if len(reader.pages) > 3:
            print(f"\n--- 第 4-5 页（可能是目录）---")
            for i in range(3, min(5, len(reader.pages))):
                text = reader.pages[i].extract_text()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                for line in lines[:20]:
                    if any(keyword in line.lower() for keyword in ['content', 'chapter', 'section', '目录', 'table']):
                        print(f"  {line}")

    except Exception as e:
        print(f"  读取失败: {e}")
