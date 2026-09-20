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
    name = str(getattr(document, "Name", "") or "")
    full_name = str(getattr(document, "FullName", "") or "")
    document_type = str(getattr(document, "Type", "") or "")
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
