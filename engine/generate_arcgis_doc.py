#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 ArcGIS Utility Network 工具体系关系说明 Word 文档
"""
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

def add_heading(doc, text, level=1):
    """添加标题并设置格式"""
    h = doc.add_heading(text, level=level)
    # 设置中文字体
    for run in h.runs:
        run.font.name = 'Microsoft YaHei'
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
    return h

def add_paragraph(doc, text, bold=False, color=None):
    """添加段落并设置格式"""
    p = doc.add_paragraph(text)
    for run in p.runs:
        run.font.name = 'Microsoft YaHei'
        run.font.size = Pt(11)
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
        if bold:
            run.bold = True
        if color:
            run.font.color.rgb = color
    return p

def add_bullet_point(doc, text, level=0):
    """添加项目符号列表"""
    p = doc.add_paragraph(text, style='List Bullet' if level == 0 else 'List Bullet 2')
    for run in p.runs:
        run.font.name = 'Microsoft YaHei'
        run.font.size = Pt(10.5)
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
    return p

def add_numbered_point(doc, text):
    """添加编号列表"""
    p = doc.add_paragraph(text, style='List Number')
    for run in p.runs:
        run.font.name = 'Microsoft YaHei'
        run.font.size = Pt(10.5)
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
    return p

def main():
    doc = Document()

    # 文档标题
    title = doc.add_heading('ArcGIS Utility Network 工具体系关系说明', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.name = 'Microsoft YaHei'
        run.font.size = Pt(22)
        run.font.bold = True
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')

    # 概述
    add_heading(doc, '概述', 1)
    add_paragraph(doc,
        'ArcGIS Utility Network（公共设施网络）生态包含四类工具组件，它们在不同层次上协同工作，'
        '支撑从数据建模、模板部署、行业方案到生产运维的完整流程。理解这四者的关系，'
        '对于正确部署和维护排水管网 GIS 系统至关重要。')

    doc.add_paragraph()  # 空行

    # ========== 第一部分：核心工具 ==========
    add_heading(doc, '一、ArcGIS Toolbox 自带的 Utility Network Tools', 1)
    add_paragraph(doc, '【核心基础层】', bold=True, color=RGBColor(0, 102, 204))

    add_heading(doc, '性质', 2)
    add_paragraph(doc, 'ArcGIS Pro 内置的原生工具集，是 Utility Network 的底层操作接口。')

    add_heading(doc, '核心功能', 2)
    add_bullet_point(doc, '创建 Utility Network：从空白企业级地理数据库初始化网络结构')
    add_bullet_point(doc, '配置网络规则：定义连通性规则（Connectivity Rules）和结构附着规则（Structural Attachment Rules）')
    add_bullet_point(doc, '拓扑验证：验证网络拓扑关系，检查连通性错误')
    add_bullet_point(doc, '追踪分析：上下游追踪、隔离分析、最短路径分析')
    add_bullet_point(doc, '子网管理：定义和更新子网（如泵站服务区、压力分区）')
    add_bullet_point(doc, 'Asset Group 和 Asset Type 管理：定义设施分类体系')

    add_heading(doc, '工具位置', 2)
    add_paragraph(doc, 'ArcGIS Pro → Geoprocessing → Data Management Tools → Utility Network')

    add_heading(doc, '典型工具示例', 2)
    add_bullet_point(doc, 'Create Utility Network：创建新网络')
    add_bullet_point(doc, 'Add Rule To Utility Network：添加网络规则')
    add_bullet_point(doc, 'Validate Network Topology：验证拓扑')
    add_bullet_point(doc, 'Trace：执行网络追踪')
    add_bullet_point(doc, 'Update Subnetwork：更新子网定义')

    add_heading(doc, '定位', 2)
    p = add_paragraph(doc, '这是所有其他工具的')
    run = p.add_run('基础 API 层')
    run.bold = True
    run.font.color.rgb = RGBColor(204, 0, 0)
    run.font.size = Pt(11)
    p.add_run('，其他三类工具都是在此基础上的封装或扩展。')

    doc.add_page_break()

    # ========== 第二部分：打包工具 ==========
    add_heading(doc, '二、Utility Network Package Tools', 1)
    add_paragraph(doc, '【模板部署工具层】', bold=True, color=RGBColor(0, 102, 204))

    add_heading(doc, '性质', 2)
    add_paragraph(doc, 'Esri 提供的配置打包和部署工具，用于快速复制和部署预配置的网络模型。')

    add_heading(doc, '核心作用', 2)
    add_paragraph(doc, '将已配置好的 Utility Network 模型（包括 Domain 定义、Asset Package、网络规则等）'
                       '打包成标准格式（.zip 或 .unpk），供其他项目或数据库快速初始化使用。')

    add_heading(doc, '主要命令', 2)

    p = doc.add_paragraph()
    run = p.add_run('Create Utility Network Package')
    run.bold = True
    run.font.name = 'Consolas'
    run.font.size = Pt(10)

    add_bullet_point(doc, '功能：导出现有 Utility Network 的完整配置')
    add_bullet_point(doc, '输出：.unpk 或 .zip 文件')
    add_bullet_point(doc, '包含内容：Domain 值、Asset Group/Type 定义、网络规则、符号系统')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('Apply Utility Network Package')
    run.bold = True
    run.font.name = 'Consolas'
    run.font.size = Pt(10)

    add_bullet_point(doc, '功能：将配置包导入到新的企业级地理数据库')
    add_bullet_point(doc, '输入：.unpk 或 .zip 文件')
    add_bullet_point(doc, '效果：自动创建所有必需的 Domain、Featureclass、网络规则')

    add_heading(doc, '使用场景', 2)
    add_numbered_point(doc, '企业内多个项目需要使用相同的网络配置')
    add_numbered_point(doc, '从开发环境迁移配置到生产环境')
    add_numbered_point(doc, '应用 Esri 官方或第三方提供的行业标准模板')

    add_heading(doc, '定位', 2)
    p = add_paragraph(doc, '对原生 Utility Network Tools 的')
    run = p.add_run('封装 + 批量操作')
    run.bold = True
    run.font.color.rgb = RGBColor(204, 0, 0)
    p.add_run('，实现配置的可复用性和标准化。')

    doc.add_page_break()

    # ========== 第三部分：行业方案包 ==========
    add_heading(doc, '三、Stormwater_Utility_Network_Essentials.zip', 1)
    add_paragraph(doc, '【雨水行业开箱即用方案】', bold=True, color=RGBColor(0, 102, 204))

    add_heading(doc, '性质', 2)
    add_paragraph(doc, 'Esri 官方提供的雨水排水行业标准配置包，是一个完整的、可直接使用的数据模型实例。')

    add_heading(doc, '包内容详解', 2)

    p = doc.add_paragraph()
    run = p.add_run('1. 预定义的 Asset Group 和 Asset Type')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, 'Pipes（管道）：重力管、压力管')
    add_bullet_point(doc, 'Manholes（检查井）：标准井、跌水井')
    add_bullet_point(doc, 'Pump Stations（泵站）')
    add_bullet_point(doc, 'Outfalls（出水口）：排放口、溢流口')
    add_bullet_point(doc, 'Detention Ponds（滞留池）')
    add_bullet_point(doc, 'Channels（明渠）')
    add_bullet_point(doc, 'Inlets（雨水口）')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('2. Domain 值定义')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, '管材类型：混凝土、PVC、HDPE、钢管等')
    add_bullet_point(doc, '管径规格：标准管径系列')
    add_bullet_point(doc, '设施状态：运行中、规划中、废弃等')
    add_bullet_point(doc, '所有权：市政、私有、联邦等')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('3. 网络规则')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, 'Connectivity Rules：定义哪些设施类型可以连接（如管道-检查井-管道）')
    add_bullet_point(doc, 'Structural Attachment Rules：定义附属关系（如雨水口附着到管道）')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('4. 符号系统和地图模板')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, '预配置的图层符号，按设施类型和状态自动着色')
    add_bullet_point(doc, '标准的标注表达式')
    add_bullet_point(doc, '行业标准的地图布局模板')

    add_heading(doc, '使用方式', 2)
    add_numbered_point(doc, '下载 Stormwater_Utility_Network_Essentials.zip')
    add_numbered_point(doc, '在 ArcGIS Pro 中打开 Utility Network Package Tools')
    add_numbered_point(doc, '运行 Apply Utility Network Package 命令')
    add_numbered_point(doc, '选择 Essentials.zip 作为输入')
    add_numbered_point(doc, '指定目标企业级地理数据库')
    add_numbered_point(doc, '工具自动创建完整的雨水网络数据模型')

    add_heading(doc, '适用场景', 2)
    add_bullet_point(doc, '新建雨水排水 GIS 项目，需要快速搭建标准数据模型')
    add_bullet_point(doc, '现有数据需要迁移到 Utility Network 架构')
    add_bullet_point(doc, '需要符合行业标准的设施分类和命名规范')

    add_heading(doc, '定位', 2)
    p = add_paragraph(doc, '这是一个')
    run = p.add_run('开箱即用的行业数据模型实例')
    run.bold = True
    run.font.color.rgb = RGBColor(204, 0, 0)
    p.add_run('，通过 Utility Network Package Tools 部署，基于原生 Utility Network Tools 运行。')

    doc.add_page_break()

    # ========== 第四部分：管理框架 ==========
    add_heading(doc, '四、UtilityDataManagement Support', 1)
    add_paragraph(doc, '【企业级运维与治理层】', bold=True, color=RGBColor(0, 102, 204))

    add_heading(doc, '性质', 2)
    add_paragraph(doc, '通常指 ArcGIS Utility Network Management 扩展模块或相关支持工具集，'
                       '面向生产环境的数据管理和运维需求。')

    add_heading(doc, '核心功能模块', 2)

    p = doc.add_paragraph()
    run = p.add_run('1. 版本管理（Branch Versioning）')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, '支持多用户并发编辑')
    add_bullet_point(doc, '变更审批工作流')
    add_bullet_point(doc, '版本冲突检测和解决')
    add_bullet_point(doc, '历史版本回溯')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('2. 数据质量检查')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, '自定义数据验证规则')
    add_bullet_point(doc, '几何合法性检查')
    add_bullet_point(doc, '属性完整性校验')
    add_bullet_point(doc, '拓扑一致性审查')
    add_bullet_point(doc, '生成质量报告')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('3. 工作流自动化')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, '与 ArcGIS Workflow Manager 集成')
    add_bullet_point(doc, '任务分配和跟踪')
    add_bullet_point(doc, '审批流程自动化')
    add_bullet_point(doc, '变更通知和日志')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('4. 企业级数据导入/迁移')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, '批量数据导入工具')
    add_bullet_point(doc, '数据转换和清洗')
    add_bullet_point(doc, '从其他系统（如 CAD、Excel、旧 GIS）迁移')
    add_bullet_point(doc, '数据映射和字段匹配')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('5. 性能监控和优化')
    run.bold = True
    run.font.size = Pt(11)

    add_bullet_point(doc, '网络拓扑索引优化')
    add_bullet_point(doc, '查询性能分析')
    add_bullet_point(doc, '数据库空间管理')

    add_heading(doc, '使用场景', 2)
    add_bullet_point(doc, '多部门、多用户协同编辑同一个 Utility Network')
    add_bullet_point(doc, '需要严格的数据审批和变更管理流程')
    add_bullet_point(doc, '从遗留系统大规模迁移数据到 Utility Network')
    add_bullet_point(doc, '持续的数据质量监控和治理')

    add_heading(doc, '定位', 2)
    p = add_paragraph(doc, '面向')
    run = p.add_run('生产环境运维 + 数据治理')
    run.bold = True
    run.font.color.rgb = RGBColor(204, 0, 0)
    p.add_run('的企业级管理框架，是日常维护和长期运营的支撑体系。')

    doc.add_page_break()

    # ========== 关系总结 ==========
    add_heading(doc, '四者关系总结', 1)

    add_heading(doc, '层次结构', 2)

    table = doc.add_table(rows=5, cols=3)
    table.style = 'Light Grid Accent 1'

    # 表头
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = '层次'
    hdr_cells[1].text = '工具/组件'
    hdr_cells[2].text = '作用范围'

    # 数据行
    row1 = table.rows[1].cells
    row1[0].text = '第 1 层\n基础能力'
    row1[1].text = 'Utility Network Tools\n（原生工具）'
    row1[2].text = '创建、配置、分析、验证'

    row2 = table.rows[2].cells
    row2[0].text = '第 2 层\n模板部署'
    row2[1].text = 'Utility Network Package Tools\n（打包工具）'
    row2[2].text = '配置打包、快速部署、标准化复用'

    row3 = table.rows[3].cells
    row3[0].text = '第 3 层\n行业方案'
    row3[1].text = 'Stormwater Essentials.zip\n（雨水方案包）'
    row3[2].text = '开箱即用的数据模型和规则'

    row4 = table.rows[4].cells
    row4[0].text = '第 4 层\n运维治理'
    row4[1].text = 'UtilityDataManagement Support\n（管理框架）'
    row4[2].text = '版本控制、质量检查、工作流、迁移'

    # 设置表格字体
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.name = 'Microsoft YaHei'
                    run.font.size = Pt(9)
                    run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')

    doc.add_paragraph()

    add_heading(doc, '典型工作流', 2)

    add_numbered_point(doc, '安装 ArcGIS Pro → 自动包含 Utility Network Tools（第 1 层基础能力）')
    add_numbered_point(doc, '下载 Stormwater_Utility_Network_Essentials.zip（第 3 层行业方案）')
    add_numbered_point(doc, '使用 Utility Network Package Tools 的 Apply Package 命令（第 2 层部署工具）')
    add_numbered_point(doc, '将 Essentials.zip 导入到企业数据库 → 自动创建完整数据模型')
    add_numbered_point(doc, '导入实际数据（管道、检查井等）')
    add_numbered_point(doc, '使用 Utility Network Tools 进行日常分析（追踪、验证等）')
    add_numbered_point(doc, '使用 UtilityDataManagement Support 进行版本管理、质量检查、工作流控制（第 4 层运维治理）')

    doc.add_page_break()

    # ========== 项目应用建议 ==========
    add_heading(doc, '对阿布扎比排水智能体项目的应用建议', 1)

    add_heading(doc, '背景', 2)
    add_paragraph(doc, '项目当前面临的数据治理问题（参考 L1/L2 layers audit 和 data governance standard）：')
    add_bullet_point(doc, '坐标系统混用（WGS84 与当地坐标系）')
    add_bullet_point(doc, '设施重复')
    add_bullet_point(doc, '未验证的 L1（已建）设施')
    add_bullet_point(doc, '汇水区边界重复')
    add_bullet_point(doc, 'L1/L2/L3 设施状态分类不清晰')

    doc.add_paragraph()

    add_heading(doc, '建议步骤', 2)

    p = doc.add_paragraph()
    run = p.add_run('步骤 1：评估 Stormwater Essentials.zip')
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0, 102, 204)

    add_bullet_point(doc, '检查其 Asset Type 定义是否匹配客户的 22 个水力模型的设施分类')
    add_bullet_point(doc, '对比项目的 tools/dgov 标准与 Esri 的 Domain 定义')
    add_bullet_point(doc, '识别差异点（如泵站的 L1/L2 状态区分、geocells 类型）')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('步骤 2：定制化扩展')
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0, 102, 204)

    add_paragraph(doc, 'Essentials 不会完全覆盖项目需求，需要以下定制：')

    add_bullet_point(doc, '添加自定义 Asset Group：')
    add_bullet_point(doc, '  - Geocells（地质单元）', level=1)
    add_bullet_point(doc, '  - Planned Ponds（规划滞留池）', level=1)

    add_bullet_point(doc, '扩展 Domain 值：')
    add_bullet_point(doc, '  - 设施状态：L1（已建）、L2（规划-蓄水措施）、L3（规划-泵站/运输）', level=1)
    add_bullet_point(doc, '  - 来源标记：Parsons 方案、客户已建、本项目建议', level=1)

    add_bullet_point(doc, '添加自定义字段：')
    add_bullet_point(doc, '  - 设施编号（如 PND_40、PS_M61_01）', level=1)
    add_bullet_point(doc, '  - 汇水区编码（如 M61、D24）', level=1)
    add_bullet_point(doc, '  - 模型来源（22 个水力模型中的哪一个）', level=1)

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('步骤 3：数据迁移路径')
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0, 102, 204)

    add_paragraph(doc, '推荐流程：')

    code_para = doc.add_paragraph('现有 Shapefile/GeoPackage\n'
                                   '  ↓\n'
                                   '按 tools/dgov 标准清洗\n'
                                   '  - 统一坐标系\n'
                                   '  - 去除重复要素\n'
                                   '  - 验证 L1 设施\n'
                                   '  - 规范化属性字段\n'
                                   '  ↓\n'
                                   '使用 Append 工具导入 Utility Network\n'
                                   '  ↓\n'
                                   '运行 Validate Network Topology\n'
                                   '  ↓\n'
                                   '修正拓扑错误\n'
                                   '  ↓\n'
                                   '启用版本管理（Branch Versioning）')
    code_para.style = 'No Spacing'
    for run in code_para.runs:
        run.font.name = 'Consolas'
        run.font.size = Pt(9)

    doc.add_paragraph()

    add_paragraph(doc, '关键注意事项：', bold=True)
    add_bullet_point(doc, '不要直接将未治理的数据导入 Utility Network')
    add_bullet_point(doc, '未清洗的数据会触发大量拓扑错误，难以修复')
    add_bullet_point(doc, '先用 tools/dgov 完成数据质量检查，再迁移')

    doc.add_paragraph()

    p = doc.add_paragraph()
    run = p.add_run('步骤 4：集成到工作流')
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0, 102, 204)

    add_bullet_point(doc, '使用 Utility Network Tools 的追踪功能：')
    add_bullet_point(doc, '  - 从热点位置上溯，找到汇水管网', level=1)
    add_bullet_point(doc, '  - 从规划泵站下溯，确定服务范围', level=1)

    add_bullet_point(doc, '导出到 SWMM 建模：')
    add_bullet_point(doc, '  - 从 Utility Network 导出管网几何和属性', level=1)
    add_bullet_point(doc, '  - 转换为 SWMM .inp 格式', level=1)
    add_bullet_point(doc, '  - 运行一二维耦合计算', level=1)

    add_bullet_point(doc, '结果反馈到 GIS：')
    add_bullet_point(doc, '  - ANUGA 模拟的积水深度栅格', level=1)
    add_bullet_point(doc, '  - 管道超载情况', level=1)
    add_bullet_point(doc, '  - 更新设施状态（如从 L2 规划变为 L2 建议）', level=1)

    doc.add_paragraph()

    add_heading(doc, '预期效益', 2)

    add_numbered_point(doc, '标准化的数据模型，符合行业规范')
    add_numbered_point(doc, '拓扑一致性保证，减少建模错误')
    add_numbered_point(doc, '版本管理支持多人协作')
    add_numbered_point(doc, '与 SWMM/ANUGA 建模流程集成')
    add_numbered_point(doc, '可追溯的数据变更历史')

    doc.add_page_break()

    # ========== 参考资料 ==========
    add_heading(doc, '参考资料', 1)

    add_heading(doc, '项目文档', 2)
    add_bullet_point(doc, 'docs/数据治理方案_RD-A试点验证与迭代_20261021.md')
    add_bullet_point(doc, 'docs/L1L2图层质量检查_20261021.md')
    add_bullet_point(doc, 'docs/数据治理方案_RD-A试点验证与迭代_20261021.docx')

    add_heading(doc, '项目工具', 2)
    add_bullet_point(doc, 'tools/dgov/ - 数据治理工具集')
    add_bullet_point(doc, 'tools/facility_register.py - 设施台账生成')
    add_bullet_point(doc, 'tools/dataqc/ - 数据质量检查工具')

    add_heading(doc, 'Esri 官方资源', 2)
    add_bullet_point(doc, 'ArcGIS Pro Utility Network 文档')
    add_bullet_point(doc, 'Utility Network Package Tools 参考')
    add_bullet_point(doc, 'Stormwater Utility Network Essentials 下载页')

    doc.add_paragraph()
    doc.add_paragraph()

    # 文档尾部
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run('─────────────────────────────\n文档生成日期：2026-10-21')
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(128, 128, 128)
    run.font.name = 'Microsoft YaHei'
    run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')

    # 保存文档
    output_path = r'D:\阿布扎比\排水智能体\docs\ArcGIS_Utility_Network_工具关系说明.docx'
    doc.save(output_path)
    print(f'文档已生成：{output_path}')

if __name__ == '__main__':
    main()
