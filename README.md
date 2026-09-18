[English](README.md) | [简体中文](README.zh-CN.md)

# CATIA Copilot

> A CATIA V5 assistant tool for engineering teams, designed to streamline daily operations and boost productivity.

**Version:** 2.2.0 &nbsp;|&nbsp; **Release Date:** 2026-07-01 &nbsp;|&nbsp; **Author:** CHEN Weibo

> Changelog: see [CHANGELOG.md](CHANGELOG.md).

---

## Features

### AI Copilot

| Feature | Description |
|---------|-------------|
| **AI Chat Assistant** | Streaming chat panel with multi-session management, Markdown rendering, and tool-call cards; AI can directly operate CATIA (read/write attributes, BOM, export, modeling, etc.) |
| **Multi-Provider Support** | Supports OpenAI, Anthropic, Gemini, and more; Settings dialog allows configuring API keys, base URLs, and models |
| **AI-Driven Modeling** | AI generates Python modeling scripts and executes them dynamically; supports sketches, extrusions, rotations, chamfers, patterns, and other features; failures automatically return tracebacks for AI self-correction |

### Workbenches

| Feature | Description |
|---------|-------------|
| **BOM Workbench** | Edit BOM attributes (part number, nomenclature, definition, version, source, and custom user attributes) in a table; write back to CATIA with one click |
| **Mass Properties Workbench** | Traverses the product tree, aggregates mass properties (mass / center of gravity / moment of inertia) per part, auto-sums by hierarchy, and exports to Excel; supports hierarchical and summary BOM modes with multiple unit options |
| **PLM Workbench** | DocDoku PLM connection, BOM diff comparison, Push/Pull, and sync history |

### Export

| Feature | Description |
|---------|-------------|
| **Export BOM from Product** | Extracts complete BOM information from CATProduct and exports to Excel (.xlsx) |
| **Export PDF from Drawing** | Batch-exports CATDrawing files to PDF with customizable file prefix |
| **Export STP from Product/Part** | Batch-exports CATPart or CATProduct files to STEP format |

### Drawings

| Feature | Description |
|---------|-------------|
| **New Drawing** | Generates a new drawing in CATIA for the active part/product based on CATDrawing templates in the `drawing_templates` folder |
| **Refresh Drawing** | Refreshes parameters of the active CATDrawing to match its associated part/product (part number, nomenclature, version, and custom attributes) |

### Tools

| Feature | Description |
|---------|-------------|
| **Copy Font to CATIA Directory** | Copies ChangFangSong.ttf to the CATIA TrueType font directory |
| **Copy ISO.xml to CATIA Directory** | Copies ISO.xml standard file to the CATIA drafting standards directory |
| **Apply Part Template** | Batch-adds standard user-defined attributes (material code, material name, etc.) to CATPart files |
| **Quick Assemble Fasteners / Plate Nuts** | Uses VBA macros to batch-assemble fasteners or plate nuts into product holes with optional flip direction |
| **Switch between Drawing / Part** | Auto-detects the active document type and opens the associated drawing or part/product |
| **Find Referenced Documents** | Finds all referencing documents of a file via CATIA COM with multiple search strategies |
| **Run Macro** | Auto-scans `.catvbs` / `.catscript` / `.catvba` files in the macros folder and runs them directly |

### Other

- **CATIA 3D View Embedded Menu** — Embeds a function menu button in the top-right corner of every 3D view for quick access; supports drag-to-reposition; menu is organized by workbench / export / drawing / tool sections
- **CATIA Connection Indicator** (Status Bar) — Polls COM connection status every 5 seconds; tri-color display: green (connected, all functions available), orange (connection issue), red (disconnected)
- **CATIA Connection Diagnostics** — View detailed diagnostic reports (CATIA version, open document count, active document, and repair suggestions)
- **Native Theme** — Uses the Windows 11 system renderer; appearance automatically follows the system light/dark mode, matching the CATIA V5 interface style
- **gen_py Auto-Cleanup** — Automatically deletes early-binding cache under `%LOCALAPPDATA%\Temp\gen_py\` at startup to prevent COM connection pollution
- **Log Window** — View operation records and error messages
- **Help Documentation** — Built-in help docs accessible via the Help → Documentation menu

---

## Requirements

- **OS:** Windows 10 / 11
- **Python:** 3.10 or later
- **CATIA V5 R28:** File export and other features require CATIA to be running (communicated via COM automation interface)

---

## Installation / Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/RayDutchman/CATIA-Copilot.git
cd CATIA-Copilot

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Running

```bash
python main.py
```

---

## Building a Windows Executable

Official releases use Nuitka + Inno Setup, avoiding the need to copy Python source files as data into the installer:

```powershell
# Generate Nuitka standalone directory
.\build_nuitka.ps1

# Generate Inno Setup installer
.\build_nuitka_installer.ps1
```

Output directory: `..\CATIA-Copilot-dist-nuitka\`.

`build.spec` + `build.ps1` are retained for legacy PyInstaller development builds but are not part of the official release workflow.

Official release packages include `resources/`, `macros/`, `drawing_templates/`, and other assets; the incomplete `part_templates/` is not included in the current release.

---

## Project Structure

```
CATIA-Copilot/
├── main.py                          # Application entry point
├── catia_copilot/
│   ├── constants.py                 # Constants and configuration
│   ├── logging_setup.py             # Logging initialization
│   ├── utils.py                     # Utility functions
│   ├── catia/                       # CATIA COM automation logic
│   │   ├── conversion.py            #   Drawing/Part export
│   │   ├── template.py              #   Part template application
│   │   ├── bom_collect.py           #   BOM data collection
│   │   ├── bom_export.py            #   BOM export to Excel
│   │   ├── bom_write.py             #   BOM attribute write-back to CATIA
│   │   ├── dependencies.py          #   Dependency lookup
│   │   ├── drawing_operations.py    #   Drawing operations
│   │   ├── mass_props_collect.py    #   Mass properties collection
│   │   └── utils.py                 #   COM utility functions
│   └── ui/                          # PySide6 UI
│       ├── main_window.py           #   Main window
│       ├── catia_embed.py           #   3D view embedded menu
│       ├── catia_sidebar.py         #   CATIA docked sidebar
│       ├── convert_dialog.py        #   File conversion dialog
│       ├── export_bom_dialog.py     #   BOM export dialog
│       ├── bom_edit_dialog.py       #   BOM workbench dialog
│       ├── mass_props_dialog.py     #   Mass properties workbench dialog
│       ├── find_deps_dialog.py      #   Find referenced documents dialog
│       ├── plm_workbench.py         #   DocDoku PLM workbench
│       ├── plm_workbench_mypdm.py   #   myPDM workbench (UI entry not enabled in current release)
│       ├── help_dialog.py           #   Help documentation dialog
│       ├── theme_manager.py         #   Theme management (dark/light/native)
│       └── log_window.py            #   Log window
├── build.spec                       # PyInstaller build configuration
├── requirements.txt                 # Python dependencies
├── pyproject.toml                   # Project metadata
├── resources/                       # Icons and other assets
├── macros/                          # Macro scripts folder
├── drawing_templates/               # Drawing templates folder
├── setup.iss                        # Inno Setup installer script
└── .github/workflows/release.yml    # GitHub Actions release workflow
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| [PySide6](https://pypi.org/project/PySide6/) | Qt 6 GUI framework |
| [openpyxl](https://pypi.org/project/openpyxl/) | Excel file read/write |
| [pywin32](https://pypi.org/project/pywin32/) | CATIA V5 COM automation |

---

## Custom Property Linkage (`PRESET_USER_REF_PROPERTIES`)

The `PRESET_USER_REF_PROPERTIES` list in `catia_copilot/constants.py` defines the built-in user-defined property names (material code, material name, specification, material source, data status, inventory category, weight, remarks). After manually editing this list, the **Python side** takes effect automatically on restart; **VBA macros and documentation** require manual synchronization.

### Auto-linked after modification (Python layer)

| File | Role |
|------|------|
| `catia_copilot/catia/template.py` | Writes user properties to CATPart one by one according to the list when applying part templates |
| `catia_copilot/ui/bom_edit_dialog.py` | BOM edit dialog: filters saved visible columns, builds full column set, renders attribute checkboxes, generates display headers |
| `catia_copilot/ui/export_bom_dialog.py` | BOM export dialog: builds "available columns / selected columns" lists |

### Manual synchronization required

| File | Reason |
|------|--------|
| `macros/generate_drawing.catvbs` (lines 85–88) | Hardcoded attribute name array in VBA macro, independent of the Python list; also contains a `"材料"` (material) field not in `PRESET_USER_REF_PROPERTIES` |
| `macros/refresh_drawing_info.catvbs` (lines 119–121) | Same — another independent VBA attribute name array |
| `catia_copilot/ui/help_dialog.py` (lines 76–77, 590–591) | Hardcoded attribute names in help window HTML text; affects display text only, not functionality |
| `README.md` (this file) | Feature table descriptions of attribute names need manual update |

> **Summary:** After modifying `PRESET_USER_REF_PROPERTIES` in `constants.py`, all Python read/write logic auto-updates on restart. The only manual synchronization needed is the two independent attribute name arrays in the VBA macro files, plus help documentation and README descriptions.

---

## Contact

- **Developer:** CHEN Weibo
- **Email:** thucwb@gmail.com

> Before using the release version, please verify the licensing scope of CATIA, fonts, macros, and third-party tools.
