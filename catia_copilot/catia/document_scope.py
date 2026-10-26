# -*- coding: utf-8 -*-
"""CATIA 文档身份描述与匹配工具。

本模块只处理可序列化的文档身份数据，不保存 COM 对象，也不负责切换活动文档。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentIdentity:
    """一次 CATIA 文档观察结果。未保存文档可能没有 full_name。"""

    name: str
    full_name: str = ""
    part_number: str = ""
    document_type: str = ""

    @property
    def key(self) -> str:
        """优先返回路径，否则返回名称和零件号组合。"""
        if self.full_name:
            return self.full_name
        if self.part_number:
            return f"{self.name}|{self.part_number}"
        return self.name

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "name": self.name,
            "full_name": self.full_name,
            "part_number": self.part_number,
            "document_type": self.document_type,
        }


def describe_document(document) -> DocumentIdentity:
    """从 CATIA 文档 COM 对象提取稳定、可序列化身份。"""
    def text(property_name):
        try:
            return str(getattr(document, property_name, "") or "")
        except Exception:
            return ""
    name = text("Name")
    full_name = text("FullName")
    document_type = text("Type")
    part_number = ""
    try:
        part_number = str(document.Product.PartNumber or "")
    except Exception:
        pass
    return DocumentIdentity(
        name=name,
        full_name=full_name,
        part_number=part_number,
        document_type=document_type,
    )


def matches_document(identity: DocumentIdentity, requested: str | None) -> bool:
    """判断用户提供的文档标识是否匹配当前文档。"""
    if not requested:
        return True
    token = requested.strip()
    return token in {
        identity.key,
        identity.name,
        identity.full_name,
        identity.part_number,
    }


def resolve_document(app, doc=None, active=False, expected_type=None):
    """解析已打开文档，不通过激活文档改变调用目标。"""
    from .document import get_document_type
    if doc is not None and active:
        raise ValueError('--doc 与 --active 互斥')
    documents = app.Documents
    if not documents.Count:
        raise ValueError('没有已打开的文档')
    if doc is None:
        target = app.ActiveDocument
        if target is None:
            raise ValueError('没有活动文档')
    else:
        if not str(doc).strip():
            raise ValueError('文档标识不能为空')
        candidates = [documents.Item(i) for i in range(1, documents.Count + 1)]
        matches = [item for item in candidates if matches_document(describe_document(item), doc)]
        if not matches:
            raise ValueError(f'文档不存在或未打开：{doc}')
        if len(matches) != 1:
            raise ValueError(f'文档标识歧义：{[describe_document(item).to_dict() for item in matches]}')
        target = matches[0]
    if expected_type and get_document_type(target) != expected_type:
        raise ValueError(f'目标必须为 {expected_type}')
    return target
