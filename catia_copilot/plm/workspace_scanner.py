"""
PLM 工作台 — 本地工作区扫描模块。

职责：
  1. 扫描工作区根目录（一层）中的 .CATPart / .CATProduct 文件
  2. 通过 CATIA COM 读取每个文件的属性（Part Number、Nomenclature 等）
  3. 管理本地 PLM parts 缓存文件（work_dir/.plm_parts_cache.json）

关键函数：
  load_catia_file(catia_app, file_path)  — 若文件已在内存中则直接返回，否则 Open
  scan_workspace(work_dir)               — 扫描根目录，返回 LocalPartInfo 列表
  load_plm_cache(work_dir)               — 读取 .plm_parts_cache.json
  save_plm_cache(work_dir, cache)        — 写入 .plm_parts_cache.json
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 工作区缓存文件名
_PLM_CACHE_FILENAME = ".plm_parts_cache.json"
_PLM_CACHE_VERSION  = 1

# 扫描目标扩展名（小写）
_SCAN_EXTENSIONS = {".catpart", ".catproduct"}


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LocalPartInfo:
    """本地文件的属性快照，通过 CATIA COM 读取。"""

    part_number: str        # Part Number（UserRefProperties 或 Product.PartNumber）
    filepath: str           # 完整 Windows 路径
    filename: str           # 仅文件名（含扩展名）
    file_type: str          # "CATProduct" | "CATPart"
    mtime: datetime         # 文件系统修改时间（UTC-naive，本地时间）
    plm_version: str        # UserRefProperties PLM_Version（空字符串表示未设置）
    plm_iteration: int      # UserRefProperties PLM_Iteration（0 表示未设置）
    nomenclature: str       # Nomenclature / name
    is_saved: bool          # doc.Saved（False 表示有未保存修改）
    is_readable: bool       # COM 是否可访问（False 表示轻量化/加载失败）
    no_file: bool           # 从未保存到磁盘（仅在内存中的新文档）
    com_doc: Any = field(default=None, repr=False)  # COM 文档对象（缓存，可为 None）


# ─────────────────────────────────────────────────────────────────────────────
# load_catia_file：智能载入，不强制打开独立窗口
# ─────────────────────────────────────────────────────────────────────────────

def load_catia_file(catia_app, file_path: str):
    """确保文件在 CATIA 内存中，返回文档 COM 对象。

    与 utils.open_catia_file 的区别：
    - 若文件已在 Documents 集合中（无论是否有独立窗口），直接返回，不调用 Open
    - 若文件不在集合中，调用 documents.Open() 打开独立文档窗口

    这样对于已被某个 CATProduct 加载的子零件，不会额外开窗口。

    Args:
        catia_app:   CATIA Application COM 对象（来自 get_catia_v5_application()）
        file_path:   要载入的文件的 Windows 绝对路径字符串

    Returns:
        CATIA Document COM 对象

    Raises:
        RuntimeError: documents.Open() 返回 None 时
    """
    import os
    documents = catia_app.Documents
    # 标准化：转小写 + 反斜杠统一，避免路径格式不一致导致匹配失败
    fp_norm = os.path.normcase(os.path.abspath(file_path))

    # 先在集合中查找（大小写不敏感、路径格式不敏感）
    for i in range(1, documents.Count + 1):
        try:
            d = documents.Item(i)
            d_norm = os.path.normcase(os.path.abspath(str(d.FullName or "")))
            if d_norm == fp_norm:
                logger.debug(f"load_catia_file: 已在内存中，跳过 Open → {file_path}")
                return d
        except Exception:
            pass

    # 不在集合中，需要 Open
    logger.debug(f"load_catia_file: 不在内存中，调用 documents.Open → {file_path}")
    doc = documents.Open(file_path)
    if doc is None:
        raise RuntimeError(
            f"documents.Open() 返回 None，CATIA 可能无法打开该文件：{file_path}"
        )
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# _read_part_attrs：从 COM doc 读取零件属性
# ─────────────────────────────────────────────────────────────────────────────

def _read_part_attrs(doc) -> dict:
    """从 CATIA COM 文档读取零件属性，返回字典。

    返回键：
        part_number (str)
        nomenclature (str)
        plm_version (str)
        plm_iteration (int)
        is_saved (bool)
        no_file (bool)
        is_readable (bool)
    """
    result = {
        "part_number":   "",
        "nomenclature":  "",
        "plm_version":   "",
        "plm_iteration": 0,
        "is_saved":      True,
        "no_file":       False,
        "is_readable":   True,
    }

    try:
        # 使用 document.py 中经过验证的类型判断函数
        from catia_copilot.catia.document import get_document_type
        doc_kind = get_document_type(doc)  # PartDocument / ProductDocument / DrawingDocument / Unknown

        if doc_kind == "PartDocument":
            root_obj = doc.Part
        elif doc_kind == "ProductDocument":
            root_obj = doc.Product
        else:
            # Drawing 或 Unknown：标记不可读但不阻断
            result["is_readable"] = False
            return result

        # 读取 Part Number
        try:
            result["part_number"] = str(root_obj.PartNumber or "")
        except Exception:
            pass

        # 读取 Nomenclature
        try:
            result["nomenclature"] = str(root_obj.get_Nomenclature() or "")
        except Exception:
            try:
                result["nomenclature"] = str(root_obj.Nomenclature or "")
            except Exception:
                pass

        # 读取 UserRefProperties（PLM_Version / PLM_Iteration）
        try:
            props = root_obj.UserRefProperties
            try:
                p = props.GetItem("PLM_Version")
                result["plm_version"] = str(p.Value or "")
            except Exception:
                pass
            try:
                p = props.GetItem("PLM_Iteration")
                val = p.Value
                result["plm_iteration"] = int(val) if val else 0
            except Exception:
                pass
        except Exception:
            pass

        # 读取 Saved 状态
        try:
            result["is_saved"] = bool(doc.Saved)
        except Exception:
            pass

        # 检测 no_file（从未保存到磁盘）
        try:
            full_name = str(doc.FullName or "")
            result["no_file"] = not bool(full_name) or not os.path.exists(full_name)
        except Exception:
            result["no_file"] = True

    except Exception as exc:
        logger.debug(f"_read_part_attrs 读取失败：{exc}")
        result["is_readable"] = False

    return result


# ─────────────────────────────────────────────────────────────────────────────
# scan_workspace：扫描工作区根目录
# ─────────────────────────────────────────────────────────────────────────────

def scan_workspace(
    work_dir: str,
    catia_app=None,
    progress_callback=None,
) -> list[LocalPartInfo]:
    """扫描工作区根目录（一层），返回 LocalPartInfo 列表。

    处理流程：
    1. 列出 work_dir 根目录中所有 .CATPart / .CATProduct 文件
    2. 优先处理 .CATProduct（打开后其子孙会被加载到内存）
    3. 对每个文件调用 load_catia_file() 确保在 CATIA 内存中
    4. 通过 COM 读取属性，构造 LocalPartInfo

    Args:
        work_dir:          工作区根目录路径
        catia_app:         CATIA Application COM 对象（为 None 时从环境获取）
        progress_callback: 可选，progress_callback(done: int, total: int, filename: str)

    Returns:
        LocalPartInfo 列表（CATProduct 在前，CATPart 在后，均按文件名排序）

    Raises:
        FileNotFoundError: work_dir 不存在
        RuntimeError:      CATIA 未运行或无法连接
    """
    work_path = Path(work_dir)
    if not work_path.is_dir():
        raise FileNotFoundError(f"工作区目录不存在：{work_dir}")

    # 获取 CATIA 应用对象
    if catia_app is None:
        try:
            from catia_copilot.catia.connection import get_catia_v5_application
            catia_app = get_catia_v5_application()
        except Exception as exc:
            raise RuntimeError(f"无法连接到 CATIA V5：{exc}") from exc

    # 扫描根目录（一层，不递归）
    all_files: list[Path] = []
    try:
        for entry in work_path.iterdir():
            if entry.is_file() and entry.suffix.lower() in _SCAN_EXTENSIONS:
                all_files.append(entry)
    except Exception as exc:
        raise RuntimeError(f"扫描工作区目录失败：{exc}") from exc

    # 排序：CATProduct 优先，同类按文件名排序
    products = sorted(
        [f for f in all_files if f.suffix.lower() == ".catproduct"],
        key=lambda p: p.name.lower(),
    )
    parts = sorted(
        [f for f in all_files if f.suffix.lower() == ".catpart"],
        key=lambda p: p.name.lower(),
    )
    ordered_files = products + parts
    total = len(ordered_files)

    results: list[LocalPartInfo] = []

    for idx, fpath in enumerate(ordered_files):
        fp_str = str(fpath)
        if progress_callback:
            progress_callback(idx, total, fpath.name)

        # 读文件系统 mtime
        try:
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime)
        except Exception:
            mtime = datetime.now()

        file_type = "CATProduct" if fpath.suffix.lower() == ".catproduct" else "CATPart"

        # 尝试通过 COM 读属性
        com_doc = None
        attrs: dict = {}
        try:
            com_doc = load_catia_file(catia_app, fp_str)
            attrs   = _read_part_attrs(com_doc)
        except Exception as exc:
            logger.warning(f"scan_workspace: 无法加载 {fpath.name}：{exc}")
            attrs = {
                "part_number":   fpath.stem,   # 回退：用文件名 stem 作为 pn
                "nomenclature":  "",
                "plm_version":   "",
                "plm_iteration": 0,
                "is_saved":      True,
                "no_file":       not fpath.exists(),
                "is_readable":   False,
            }

        info = LocalPartInfo(
            part_number   = attrs.get("part_number", "") or fpath.stem,
            filepath      = fp_str,
            filename      = fpath.name,
            file_type     = file_type,
            mtime         = mtime,
            plm_version   = attrs.get("plm_version", ""),
            plm_iteration = attrs.get("plm_iteration", 0),
            nomenclature  = attrs.get("nomenclature", ""),
            is_saved      = attrs.get("is_saved", True),
            is_readable   = attrs.get("is_readable", True),
            no_file       = attrs.get("no_file", False),
            com_doc       = com_doc,
        )
        results.append(info)
        logger.debug(
            f"scan_workspace: {fpath.name} → pn={info.part_number} "
            f"ver={info.plm_version}/{info.plm_iteration} saved={info.is_saved}"
        )

    if progress_callback:
        progress_callback(total, total, "")

    logger.info(f"scan_workspace: 扫描完成，共 {len(results)} 个文件（{work_dir}）")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# PLM parts 本地缓存（.plm_parts_cache.json）
# ─────────────────────────────────────────────────────────────────────────────

def load_plm_cache(work_dir: str) -> dict[str, dict]:
    """读取工作区的 PLM parts 缓存文件。

    缓存格式：
    {
      "version": 1,
      "updated_at": "2026-06-16T12:43:16",
      "parts": {
        "BevelGear": {
          "number": "BevelGear",
          "name": "锥齿轮",
          "version": "A",
          "lastIterationNumber": 4,
          "checkOutUser": null,
          "modificationDate": "2026-06-16T12:43:16",
          "authorLogin": "admin",
          "lifecycleState": "发布",
          "tags": []
        }
      }
    }

    Returns:
        {pn: part_dict} 字典，若文件不存在则返回空字典
    """
    cache_path = Path(work_dir) / _PLM_CACHE_FILENAME
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != _PLM_CACHE_VERSION:
            logger.warning(f"PLM 缓存版本不匹配，忽略：{cache_path}")
            return {}
        return dict(data.get("parts", {}))
    except Exception as exc:
        logger.warning(f"读取 PLM 缓存失败：{cache_path} — {exc}")
        return {}


def save_plm_cache(work_dir: str, parts: dict[str, dict]) -> None:
    """将 PLM parts 信息写入工作区缓存文件。

    Args:
        work_dir: 工作区根目录
        parts:    {pn: part_dict} 字典（来自 api_client 返回的数据）
    """
    cache_path = Path(work_dir) / _PLM_CACHE_FILENAME
    data = {
        "version":    _PLM_CACHE_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "parts":      parts,
    }
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"PLM 缓存已保存：{cache_path}（{len(parts)} 条）")
    except Exception as exc:
        logger.warning(f"写入 PLM 缓存失败：{cache_path} — {exc}")


def merge_plm_cache(work_dir: str, new_parts: dict[str, dict]) -> dict[str, dict]:
    """将新查询到的 PLM parts 合并到现有缓存（新数据覆盖旧数据）并保存。

    Returns:
        合并后的完整 {pn: part_dict} 字典
    """
    existing = load_plm_cache(work_dir)
    existing.update(new_parts)
    save_plm_cache(work_dir, existing)
    return existing
