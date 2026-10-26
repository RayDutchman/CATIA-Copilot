"""CATIA 工具参数契约，供 CLI 查询与参数解析。"""
from catia_copilot.catia.modeling_contract import build_run_modeling_script_description
from catia_copilot.catia.bom_schema import bom_schema

tools_schema = [
{
        "type": "function",
        "function": {
            "name": "check_catia_connection",
            "description": "Check the CATIA V5 COM connection. Status is connected, broken (CATIA is running but COM connection failed, usually due to permissions), or disconnected (CATIA is not running).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
{
        "type": "function",
        "function": {
            "name": "diagnose_catia_connection",
            "description": "Diagnose the CATIA V5 COM connection. Returns status, doc_count, active_doc, catia_process_running, error, and related fields. Use this to investigate connection issues; use check_catia_connection for routine checks.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
{
        "type": "function",
        "function": {
            "name": "open_catia_file",
            "description": "Open a CATPart, CATProduct, or CATDrawing in CATIA. If it is already open, activate it instead of opening a duplicate. Returns an error if the file does not exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Full Windows file path, for example D:\\project\\part.CATPart",
                    },
                    "foreground": {
                        "type": "boolean",
                        "description": "Whether to bring the CATIA window to the foreground; defaults to true",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
*bom_schema,
{
        "type": "function",
        "function": {
            "name": "write_bom_to_catia",
            "description": "Write edited property values to custom properties on parts in the CATIA document. Do not call save_catia_document afterward; tell the user to save. If file_path is null, use the active document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct path; pass null to use the active document",
                    },
                    "pn_data": {
                        "type": "object",
                        "description": "Values to write, formatted as {Part Number: {column: new_value}}. Columns may be standard fields (Nomenclature/Definition/Revision/Source) or custom property names. Include only fields to change; omitted fields remain unchanged.",
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "custom_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Custom-property column names in pn_data (excluding standard columns). List custom columns explicitly here; names not declared here are not inferred from a preset list.",
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
            "description": "Batch-export CATDrawing drawings to PDF. Output filename: {prefix}{source_stem}{suffix}.pdf. Defaults to DR_{source_stem}.pdf. Returns {success, exported_count, total}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of CATDrawing file paths",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "Output directory; defaults to the source directory",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "Output filename prefix; defaults to DR_",
                    },
                    "suffix": {
                        "type": "string",
                        "description": "Output filename suffix; defaults to empty",
                    },
                    "update_before_export": {
                        "type": "boolean",
                        "description": "Update the drawing before export; defaults to false",
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
            "description": "Batch-export CATPart/CATProduct files to STEP (.stp). Output filename: {prefix}{source_stem}{suffix}.stp. Defaults to MD_{source_stem}.stp. Returns {success, exported_count, total}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of CATPart or CATProduct file paths",
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "Output directory; defaults to the source directory",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "Output filename prefix; defaults to MD_",
                    },
                    "suffix": {
                        "type": "string",
                        "description": "Output filename suffix; defaults to empty",
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
            "description": "Find direct dependencies of a file. CATProduct returns direct child paths; CATDrawing returns part/product paths linked by views. With activate=true (default), open the file if needed; activate=false searches open documents only. Returns {dependency_count, dependencies}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "Full path of the target file",
                    },
                    "activate": {
                        "type": "boolean",
                        "description": "Whether to open and activate the target document automatically; defaults to true. When false, search open documents only, without opening files; useful for batch traversal.",
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
            "description": "Find open documents that directly reference a target file. Checks open CATProduct and CATDrawing documents only and does not open files. Useful for checking references before deleting a part. Returns {dependency_count, dependencies}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "Full path of the target file",
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
                "Collect mass properties for every node in a CATProduct tree (mass in kg, center of gravity in mm, inertia in kg*mm^2). "
                "Center-of-gravity and inertia coordinates are transformed to the root product coordinate system. "
                "Data comes from CATIA SPA saved inertia measurements; fields such as Weight are null when a part has no saved measurement. "
                "If file_path is null, use the active document. "
                "Returns {row_count, rows}; each row contains fields such as Level/Type/Part Number/Weight/CogX/CogY/CogZ/Ixx."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "CATProduct path; pass null to use the active document",
                    },
                    "read_mode": {
                        "type": "string",
                        "enum": ["all", "first", "last"],
                        "description": "How to read repeated instances of the same part: all (default) returns every instance on a separate row; first reads only the first; last reads only the last.",
                    },
                    "skip_hidden": {
                        "type": "boolean",
                        "description": "Whether to skip hidden parts; defaults to false",
                    },
                    "summary_only": {
                        "type": "boolean",
                        "description": "When true, return only the root node (Level=0) mass summary with Weight/CogX/CogY/CogZ/Ixx/Iyy/Izz/Ixy/Ixz/Iyz. Useful when only total mass and center of gravity are needed. Defaults to false.",
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
                "Create a drawing from the active CATPart or CATProduct by copying a template. "
                "Before calling, confirm the target part/product is active using active_document from get_open_documents. "
                "The drawing is saved in the same directory as the part."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_path": {
                        "type": "string",
                        "description": "Full path to the CATDrawing template",
                    },
                    "property_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Property names to synchronize from the part to the drawing title block. "
                            "Defaults to the existing business properties 物料编码, 材料, and 重量. "
                            "Additional custom property names may be specified."
                        ),
                    },
                    "property_values": {
                        "type": "object",
                        "description": (
                            "Mapping of property names to values, for example {物料编码: ABC-001, 材料: 铝合金}. "
                            "If omitted or empty, keep existing CATIA property values unchanged."
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
                "Refresh title-block parameters in the active CATDrawing by synchronizing values from its referenced part. "
                "Unlike generate_drawing, this updates an existing active drawing and does not create a file. "
                "Before calling, confirm the active document is a CATDrawing using get_open_documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "property_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Property names to synchronize from the referenced part to the title block. "
                            "Defaults to existing business properties 物料编码, 材料, and 重量."
                        ),
                    },
                    "property_values": {
                        "type": "object",
                        "description": "Property values to override, for example {重量: 1.23}",
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
            "description": "Batch-add preset custom properties to CATPart files. Existing properties are not overwritten; only missing properties are added. When keep_open=false (default), close files after processing to release memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of CATPart file paths",
                    },
                    "keep_open": {
                        "type": "boolean",
                        "description": "Keep files open in CATIA after processing; defaults to false",
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
                "Return full paths for all open CATIA documents and the active document path. "
                "Returns {active_document (full path or null), open_documents (items with name/path/type)}. "
                "type is PartDocument / ProductDocument / DrawingDocument / Other. "
                "Call this before operations that require accurate file paths."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
{
        "type": "function",
        "function": {
            "name": "save_catia_document",
            "description": "Save a CATIA document to disk. If file_path is null, save the active document. Call this after write-back operations such as write_bom_to_catia or changes will be lost when CATIA closes. Returns {success, saved_path} or {error}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": ["string", "null"],
                        "description": "Full path of the document to save; pass null to save the active document",
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
                "Heuristically find CATPart/CATProduct paths for a CATDrawing. Strategies, in priority order: "
                "pn_param_open_docs matches the drawing PartNumber parameter against open documents; "
                "pn_param_scan_dirs scans ancestor directories for matching filenames; "
                "same_name_scan_dirs searches for a part with the drawing filename; "
                "strip_prefix_scan_dirs searches after removing the drawing filename prefix; "
                "doc_file_links reads referenced parts from generative-view COM links (drawing must be open). "
                "Returns {matches: [paths]}, or an empty list if none are found."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "drawing_path": {
                        "type": "string",
                        "description": "Full path to the CATDrawing file",
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
                        "description": "Search strategies to use; defaults to all strategies",
                    },
                    "max_parent_levels": {
                        "type": "integer",
                        "description": "Maximum number of ancestor directory levels to search; defaults to 2",
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
                "Heuristically find CATDrawing paths for a CATPart/CATProduct. Strategies, in priority order: "
                "pn_param_open_drws matches PartNumber parameters in open drawings; "
                "pn_param_scan_drws scans ancestor directories for a matching PartNumber filename; "
                "same_name_scan_dirs searches for a drawing with the part filename; "
                "strip_prefix_scan_dirs matches after removing a drawing filename prefix; "
                "doc_file_links checks whether open drawing view links reference the part. "
                "Returns {matches: [paths]}, or an empty list if none are found."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "part_path": {
                        "type": "string",
                        "description": "Full path to the CATPart or CATProduct file",
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
                        "description": "Search strategies to use; defaults to all strategies",
                    },
                    "max_parent_levels": {
                        "type": "integer",
                        "description": "Maximum number of ancestor directory levels to search; defaults to 2",
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
                "Read standard and custom properties from a CATIA document. "
                "If file_path is null, read the active document. "
                "Returns file_path, doc_type, standard (Part Number / Nomenclature / Revision / Definition / Source / Description), "
                "and user_defined. Use this to inspect part revision, nomenclature, and custom properties before editing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Full path to the target .CATPart or .CATProduct. Omit or pass null to read the active document."
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
                "Write standard and/or custom properties to a CATIA document. If file_path is null, use the active document. "
                "standard supports Part Number, Nomenclature, Revision, Definition, Source, and Description. "
                "Description is written through DescriptionRef. With save=true, save immediately; otherwise call save_catia_document. "
                "Returns written_user_defined, skipped, and saved. "
                "Custom property names and values may be chosen as needed; no company preset list or enum restriction is applied by this tool. "
                "Follow the organization's conventions provided by the applicable Skill."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Full path to the target .CATPart or .CATProduct. Omit or pass null to use the active document."
                        ),
                    },
                    "standard": {
                        "type": "object",
                        "description": (
                            "Standard properties to write. Supported keys: Part Number, Nomenclature, Revision, Definition, Source, Description. "
                            "Description is written through DescriptionRef. Source accepts Unknown, Made, or Bought. "
                            "Example: {\"Revision\": \"B\", \"Description\": \"main shaft bracket\"}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "user_defined": {
                        "type": "object",
                        "description": (
                            "Custom properties to write. Property names and values are not restricted to a preset list; follow the organization's conventions provided by the applicable Skill."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "save": {
                        "type": "boolean",
                        "description": "Save the document immediately after writing; defaults to false",
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
                            "Complete Python script containing def build(ctx):. Call modeling APIs through ctx; no imports are needed."
                        ),
                    },
                    "target_document_id": {
                        "type": "string",
                        "description": (
                            "Optional CATIA document identifier: a name returned by get_open_documents, full path, or PartNumber. "
                            "Execution is rejected if it does not match the active document."
                        ),
                    },
                    "verification": {
                        "type": "object",
                        "description": "Optional model acceptance criteria: required_features, feature_count, mass_kg, and cog_mm.",
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

from .inspection_schema import inspection_schema
tools_schema.extend(inspection_schema)
