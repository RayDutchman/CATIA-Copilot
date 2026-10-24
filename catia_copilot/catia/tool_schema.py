"""CATIA 工具参数契约，供 CLI 查询与参数解析。"""
from catia_copilot.catia.modeling_contract import build_run_modeling_script_description

tools_schema = [
{
        "type": "function",
        "function": {
            "name": "check_catia_connection",
            "description": ("检查 CATIA V5 的 COM 连接状态。""返回值：connected=连接正常；broken=CATIA 进程存在但 COM 连接失败（通常是权限问题）；disconnected=CATIA 未运行。"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
{
        "type": "function",
        "function": {
            "name": "diagnose_catia_connection",
            "description": ("对 CATIA V5 COM 连接进行详细诊断。""返回字段包括：status（连接状态）、""doc_count（已打开文档数）、active_doc（活动文档名）、""catia_process_running（进程是否存在）、error（错误描述）等。""适用于排查连接异常；日常使用 check_catia_connection 即可。"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
{
        "type": "function",
        "function": {
            "name": "open_catia_file",
            "description": ("在 CATIA 中打开指定文件（CATPart / CATProduct / CATDrawing）。""若文件已打开则激活并切换到该文档，不会重复打开。""文件不存在时返回 error。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件的完整 Windows 路径，例如 D:\\project\\part.CATPart",
                    },
                    "foreground": {
                        "type": "boolean",
                        "description": "是否将 CATIA 窗口切换到前台（置顶显示），默认 true",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "collect_bom",
            "description": (
                "采集 CATIA CATProduct 的 BOM（物料清单），返回层级零件清单。"
                "file_path 为 null 时使用当前活动文档。"
                "返回 {row_count, rows}，每行包含 Level/Type/Part Number/Nomenclature/"
                "Definition/Revision/Source/Description/Quantity 等标准列，"
                "以及 custom_columns 中指定的用户自定义属性列。"
                "summarize=true 时返回去重汇总结果（无 Level 列，Quantity 为全树累计）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct 文件路径，传 null 使用当前活动文档",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("要采集的标准列名列表。""可选值：Level, Type, Part Number, Nomenclature, Definition, Revision, Source, Description, Quantity。""默认采集全部标准列。"),
                    },
                    "custom_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("用户自定义属性列名列表（通过 CATIA 零件属性对话框写入的属性）。""预设属性名包括：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。"),
                    },
                    "summarize": {
                        "type": "boolean",
                        "description": "是否折叠为平面汇总（去重 + 累计数量），默认 false",
                    },
                    "include_assemblies": {
                        "type": "boolean",
                        "description": "汇总模式下是否包含产品行，默认 false（仅在 summarize=true 时生效）",
                    },
                    "sort_column": {
                        "type": ["string", "null"],
                        "description": "汇总模式下的排序列名，默认按 Part Number 排序（仅在 summarize=true 时生效）",
                    },
                },
                "required": [],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "export_bom_to_excel",
            "description": ("将 CATProduct 的 BOM 导出为 Excel（.xlsx）或 CSV 文件。""输出文件名规则：{原文件名}_BOM.xlsx（层级模式）或 {原文件名}_汇总BOM.xlsx（汇总模式）。""传 [null] 使用当前活动文档。返回 {success, exported_files}（导出文件路径列表）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": ["string", "null"]},
                        "description": "CATProduct 文件路径列表（传 [null] 使用当前活动文档）",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "输出目录路径，不填则与源文件同目录",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("要导出的标准列名列表。""可选值：Level, Type, Part Number, Nomenclature, Definition, Revision, Source, Description, Quantity。""默认导出全部标准列。"),
                    },
                    "summarize": {
                        "type": "boolean",
                        "description": "是否导出汇总模式（去重+累计数量），默认 false",
                    },
                    "summary_include_assemblies": {
                        "type": "boolean",
                        "description": "汇总模式下是否包含产品行，默认 false（仅在 summarize=true 时生效）",
                    },
                    "summary_sort_column": {
                        "type": ["string", "null"],
                        "description": "汇总模式下的排序列名，默认按 Part Number 排序（仅在 summarize=true 时生效）",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["xlsx", "csv"],
                        "description": "输出格式，默认 xlsx",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "write_bom_to_catia",
            "description": ("将编辑后的属性值批量写回 CATIA 文档中各零件的用户自定义属性。""注意：写回后无须调用 save_catia_document 保存文件，文字提示用户保存即可。""file_path 为 null 时操作当前活动文档。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct 文件路径，传 null 使用当前活动文档",
                    },
                    "pn_data": {
                        "type": "object",
                        "description": ("要写入的数据，格式：{零件编号(Part Number): {列名: 新值}}。""列名可以是标准列（Nomenclature/Definition/Revision/Source）或用户自定义属性名。""只需包含需要修改的字段，未提供的字段保持不变。"),
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "custom_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("pn_data 中包含的用户自定义属性列名列表（非标准列）。""预设属性名包括：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。""不在此列表中的列名会被当作标准属性处理。"),
                    },
                },
                "required": ["pn_data"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "convert_to_pdf",
            "description": ("批量将 CATDrawing 图纸文件导出为 PDF。""输出文件名规则：{prefix}{原文件名（不含扩展名）}{suffix}.pdf。""默认 prefix=DR_，suffix 为空，即 DR_{原文件名}.pdf。""返回 {success, exported_count, total}。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CATDrawing 文件路径列表",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "输出目录，不填则与源文件同目录",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "输出文件名前缀，默认 DR_",
                    },
                    "suffix": {
                        "type": "string",
                        "description": "输出文件名后缀，默认空",
                    },
                    "update_before_export": {
                        "type": "boolean",
                        "description": "导出前是否先更新图纸，默认 false",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "convert_to_step",
            "description": ("批量将 CATPart / CATProduct 导出为 STEP（.stp）格式。""输出文件名规则：{prefix}{原文件名（不含扩展名）}{suffix}.stp。""默认 prefix=MD_，suffix 为空，即 MD_{原文件名}.stp。""返回 {success, exported_count, total}。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CATPart 或 CATProduct 文件路径列表",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "输出目录，不填则与源文件同目录",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "输出文件名前缀，默认 MD_",
                    },
                    "suffix": {
                        "type": "string",
                        "description": "输出文件名后缀，默认空",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "find_dependencies",
            "description": ("正向依赖查询：返回指定文件直接引用的子文档路径列表。""CATProduct 返回直接子件路径；CATDrawing 返回视图链接的零件/产品路径。""activate=true（默认）时若文件未打开则自动打开；""activate=false 时只查询已打开文档，适合批量遍历场景。""返回 {dependency_count, dependencies}。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "目标文件的完整路径",
                    },
                    "activate": {
                        "type": "boolean",
                        "description": ("是否自动打开并激活目标文档，默认 true。""设为 false 时只在已打开文档中查找，不打开新文件，适合批量遍历。"),
                    },
                },
                "required": ["target_path"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "find_reverse_dependencies",
            "description": ("反向依赖查询：在当前 CATIA 中已打开的文档里，查找哪些文档直接引用了指定文件。""只检查已打开的 CATProduct 和 CATDrawing，不会主动打开新文件。""典型用途：删除零件前确认哪些产品/图纸引用了它。""返回 {dependency_count, dependencies}。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "目标文件的完整路径",
                    },
                },
                "required": ["target_path"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "collect_mass_props",
            "description": (
                "采集 CATProduct 产品树中所有节点的质量特性（质量 kg、重心 mm、转动惯量 kg*mmm^2），"
                "重心和惯量坐标已变换到根产品坐标系。"
                "数据来源：CATIA SPA 工具写入的惯量包络体保持测量参数；"
                "若零件未做保持测量，对应行的 Weight 等字段为 null。"
                "file_path 为 null 时使用当前活动文档。"
                "返回 {row_count, rows}，每行含 Level/Type/Part Number/Weight/CogX/CogY/CogZ/Ixx 等字段。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct 文件路径，传 null 使用当前活动文档",
                    },
                    "read_mode": {
                        "type": "string",
                        "enum": ["all", "first", "last"],
                        "description": ("同一零件有多个实例（重复使用）时的读取策略：""all=读取全部实例（默认，每个实例单独一行）；""first=只读第一个实例；last=只读最后一个实例。"),
                    },
                    "skip_hidden": {
                        "type": "boolean",
                        "description": "是否跳过隐藏零件，默认 false",
                    },
                    "summary_only": {
                        "type": "boolean",
                        "description": ("为 true 时只返回根节点（Level=0，即整个产品）的质量特性摘要，""字段：Weight/CogX/CogY/CogZ/Ixx/Iyy/Izz/Ixy/Ixz/Iyz。""适合只需要总重量和重心的场景，大幅减少 token 消耗。默认 false。"),
                    },
                },
                "required": [],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "generate_drawing",
            "description": (
                "从当前活动的 CATPart 或 CATProduct 生成新图纸（基于模板复制）。"
                "调用前请确认目标零件/产品已在 CATIA 中激活（通过 get_open_documents 确认 active_document）。"
                "生成的图纸保存在与零件相同的目录下。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_path": {
                        "type": "string",
                        "description": "CATDrawing 模板文件的完整路径",
                    },
                    "property_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "要从零件同步到图纸标题栏的属性名列表。"
                            "默认同步：物料编码、材料、重量。"
                            "可扩展为其他用户自定义属性名，如：零件类型、设计状态、规格型号等。"
                        ),
                    },
                    "property_values": {
                        "type": "object",
                        "description": (
                            "属性名到属性值的映射，例如 {物料编码: ABC-001, 材料: 铝合金}。"
                            "不提供或留空时，保留 CATIA 文档中已有的属性值，不做修改。"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["template_path"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "refresh_drawing",
            "description": (
                "刷新当前活动 CATDrawing 的标题栏参数，从关联零件同步属性值。"
                "与 generate_drawing 的区别：refresh_drawing 作用于已存在的图纸（当前活动文档），"
                "不创建新文件；generate_drawing 从模板创建新图纸。"
                "调用前请确认活动文档是 CATDrawing（通过 get_open_documents 确认）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "property_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "要从关联零件同步到图纸标题栏的属性名列表。"
                            "默认同步：物料编码、材料、重量。"
                        ),
                    },
                    "property_values": {
                        "type": "object",
                        "description": "需要覆盖的属性值，例如 {重量: 1.23}",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": [],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "apply_part_template",
            "description": ("为 CATPart 文件批量添加/刷写标准用户自定义属性。""标准属性包括：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。""已存在的属性不会被覆盖，只添加缺失的属性。""keep_open=false（默认）时处理完成后关闭文件以释放内存。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CATPart 文件路径列表",
                    },
                    "keep_open": {
                        "type": "boolean",
                        "description": "处理后是否保持文件在 CATIA 中打开，默认 false",
                    },
                },
                "required": ["file_paths"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "get_open_documents",
            "description": (
                "返回当前 CATIA 中所有已打开文档的完整路径列表及活动文档路径。"
                "返回 {active_document（完整路径或 null）, open_documents（列表，每项含 name/path/type）}。"
                "type 取值：PartDocument / ProductDocument / DrawingDocument / Other。"
                "AI 在执行任何需要文件路径的操作前，应先调用此工具获取准确路径。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
{
        "type": "function",
        "function": {
            "name": "save_catia_document",
            "description": ("保存 CATIA 文档到磁盘。file_path 为 null 时保存当前活动文档。""write_bom_to_catia 等写回操作完成后必须调用此工具，否则修改在关闭 CATIA 后丢失。""返回 {success, saved_path} 或 {error}。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "要保存的文档完整路径，传 null 保存当前活动文档",
                    },
                },
                "required": [],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "find_part_for_drawing",
            "description": (
                "给定 CATDrawing 路径，启发式查找对应的 CATPart/CATProduct 文件路径列表。"
                "策略说明（按优先级）："
                "pn_param_open_docs=读图纸 PartNumber 参数，在已打开文档中匹配；"
                "pn_param_scan_dirs=读图纸 PartNumber 参数，在上级目录中扫描同名文件；"
                "same_name_scan_dirs=用图纸文件名在上级目录中扫描同名零件文件；"
                "strip_prefix_scan_dirs=去掉图纸文件名前缀后扫描；"
                "doc_file_links=通过图纸生成式视图的 COM 链接直接读取关联零件（需图纸已打开）。"
                "返回 {matches: [路径列表]}，未找到时为空列表。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "drawing_path": {
                        "type": "string",
                        "description": "CATDrawing 文件的完整路径",
                    },
                    "strategies": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "pn_param_open_docs",
                                "pn_param_scan_dirs",
                                "same_name_scan_dirs",
                                "strip_prefix_scan_dirs",
                                "doc_file_links",
                            ],
                        },
                        "description": "要使用的查找策略列表，默认使用全部策略",
                    },
                    "max_parent_levels": {
                        "type": "integer",
                        "description": "目录向上搜索的最大层级数，默认 2",
                    },
                },
                "required": ["drawing_path"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "find_drawing_for_part",
            "description": (
                "给定 CATPart/CATProduct 路径，启发式查找对应的 CATDrawing 文件路径列表。"
                "策略说明（按优先级）："
                "pn_param_open_drws=在已打开图纸中找 PartNumber 参数与零件编号匹配的图纸；"
                "pn_param_scan_drws=在上级目录中扫描文件名与零件 PartNumber 相同的图纸；"
                "same_name_scan_dirs=在上级目录中扫描文件名与零件文件名相同的图纸；"
                "strip_prefix_scan_dirs=扫描上级目录中去掉前缀后与零件文件名匹配的图纸；"
                "doc_file_links=遍历已打开图纸，反查其视图链接是否指向该零件（需图纸已打开）。"
                "返回 {matches: [路径列表]}，未找到时为空列表。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "part_path": {
                        "type": "string",
                        "description": "CATPart 或 CATProduct 文件的完整路径",
                    },
                    "strategies": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "pn_param_open_drws",
                                "pn_param_scan_drws",
                                "same_name_scan_dirs",
                                "strip_prefix_scan_dirs",
                                "doc_file_links",
                            ],
                        },
                        "description": "要使用的查找策略列表，默认使用全部策略",
                    },
                    "max_parent_levels": {
                        "type": "integer",
                        "description": "目录向上搜索的最大层级数，默认 2",
                    },
                },
                "required": ["part_path"],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "get_document_properties",
            "description": (
                "读取 CATIA 文档的属性，包括标准属性和用户自定义属性。"
                "file_path 为 null 时读取当前活动文档。"
                "返回字段：file_path（文档路径）、doc_type（文档类型）、"
                "standard（标准属性字典，含 Part Number / Nomenclature / Revision / "
                "Definition / Source / Description）、"
                "user_defined（用户自定义属性字典）。"
                "典型用途：查看零件的版本号、命名规范、自定义属性；"
                "在修改属性前先读取当前值作为参考。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "目标文档的完整路径（.CATPart 或 .CATProduct）。"
                            "不填或传 null 则读取当前活动文档。"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "set_document_properties",
            "description": (
                "写入 CATIA 文档的属性（标准属性和/或用户自定义属性）。"
                "file_path 为 null 时操作当前活动文档。"
                "standard 支持的键：Part Number、Nomenclature、Revision、Definition、Source、Description。"
                "Description 通过 DescriptionRef 写入，经实测可写。"
                "save=true 时写入后立即保存文档；默认 false，需手动调用 save_catia_document。"
                "written_user_defined（已写入的自定义属性列表）、"
                "skipped（跳过的属性列表）、saved（是否已保存）。"
                "【重要】user_defined 的键必须来自预设列表："
                "零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。"
                "不得自行创建预设列表以外的字段名。"
                "设计状态的合法值：草稿 / 冻结 / 发布 / 废弃。"
                "存货类别的合法值：物料-复材件 / 物料-金属件 / 物料-标准件 / 物料-非标件 / "
                "物料-钣金件 / 物料-塑胶件 / 物料-橡胶件 / 物料-电子件 / 物料-泡沫 / "
                "物料-软包 / 物料-辅材 / 物料-组件 / 物料-虚拟件 / 半成品-组件 / 成品-整机。"
                "用户要求写入不在预设列表中的字段名时，必须先告知用户并询问确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "目标文档的完整路径（.CATPart 或 .CATProduct）。"
                            "不填或传 null 则操作当前活动文档。"
                        ),
                    },
                    "standard": {
                        "type": "object",
                        "description": (
                            "要写入的标准属性字典。"
                            "支持的键：Part Number、Nomenclature、Revision、Definition、Source、Description。"
                            "Description 通过 DescriptionRef 写入，经实测可写。"
                            "Source 的合法值：Unknown、Made、Bought。"
                            "例：{\"Revision\": \"B\", \"Description\": \"主轴支架\"}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "user_defined": {
                        "type": "object",
                        "description": (
                            "要写入的用户自定义属性字典。"
                            "键必须来自预设列表：零件类型、设计状态、材料、重量、物料编码、存货类别、规格型号、备注。"
                            "设计状态合法值：草稿 / 冻结 / 发布 / 废弃。"
                            "存货类别合法值：物料-复材件 / 物料-金属件 / 物料-标准件 / 物料-非标件 / "
                            "物料-钣金件 / 物料-塑胶件 / 物料-橡胶件 / 物料-电子件 / 物料-泡沫 / "
                            "物料-软包 / 物料-辅材 / 物料-组件 / 物料-虚拟件 / 半成品-组件 / 成品-整机。"
                            "例：{\"设计状态\": \"冻结\", \"存货类别\": \"物料-金属件\", \"材料\": \"Q235\"}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "save": {
                        "type": "boolean",
                        "description": "写入后是否立即保存文档，默认 false",
                    },
                },
                "required": [],
            },
        },
    },
{
        "type": "function",
        "function": {
            "name": "run_modeling_script",
            "description": build_run_modeling_script_description(),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": (
                            "完整的 Python 脚本字符串，必须包含 def build(ctx): 函数。"
                            "通过 ctx 调用所有建模 API，不需要任何 import 语句。"
                        ),
                    },
                    "target_document_id": {
                        "type": "string",
                        "description": (
                            "可选。目标 CATIA 文档标识，可传 get_open_documents 返回的文档名、"
                            "完整路径或 PartNumber。若与当前活动文档不匹配，工具拒绝执行。"
                        ),
                    },
                    "verification": {
                        "type": "object",
                        "description": "可选的模型验收条件，支持 required_features、feature_count、mass_kg、cog_mm。",
                        "properties": {
                            "required_features": {"type": "array", "items": {"type": "string"}},
                            "feature_count": {"type": "integer"},
                            "mass_kg": {
                                "type": "object",
                                "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
                            },
                            "cog_mm": {
                                "type": "object",
                                "properties": {
                                    "min": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                                    "max": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                                },
                            },
                        },
                    },
                },
                "required": ["script"],
            },
        },
    }
]
