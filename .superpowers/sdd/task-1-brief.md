### Task 1: myPDM API 数据模型 (Schemas)

**Files:**
- Create: `catia_copilot/plm/my_pdm_schemas.py`

**Interfaces:**
- Produces: `TokenResponse`, `UserResponse`, `PartMasterResponse`, `PartRevisionResponse`, `BomMatchRequest`, `BomMatchResponse`, `BomSyncRequest`, `CadNamingConfig`, `AttachmentResponse`, `PartCreateRequest`, `PartCreateResponse`, `UserRole` 枚举, `MYPDM_PERMISSIONS` 权限矩阵, `has_permission()` 函数

- [ ] **Step 1: 创建完整的 schemas 文件**

在 `D:\OpenCode\CATIA-Copilot\catia_copilot\plm\my_pdm_schemas.py` 创建文件，包含以下所有代码：

```python
"""
myPDM API 响应数据模型。

使用 dataclass 定义所有 API 请求/响应结构，提供类型安全和 IDE 提示。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UserRole(str, Enum):
    """myPDM 角色枚举。"""
    ADMIN = "admin"
    ENGINEER = "engineer"
    PRODUCTION = "production"
    GUEST = "guest"


# myPDM 权限矩阵（本地硬编码，来源 myPDM/permissions/permissions.json）
# 每个权限对应的允许角色列表
MYPDM_PERMISSIONS: dict[str, list[str]] = {
    "parts:read":              ["admin", "engineer", "production", "guest"],
    "parts:create":            ["admin", "engineer"],
    "parts:update":            ["admin", "engineer"],
    "parts:delete":            ["admin"],
    "parts:checkout":          ["admin", "engineer"],
    "parts:checkin":           ["admin", "engineer"],
    "parts:undocheckout":      ["admin", "engineer"],
    "parts:force_checkin":     ["admin"],
    "parts:cascade_checkout":  ["admin", "engineer"],
    "parts:cascade_checkin":   ["admin", "engineer"],
    "parts.doc:read":          ["admin", "engineer", "production", "guest"],
    "parts.doc:link":          ["admin", "engineer"],
    "parts.doc:unlink":        ["admin", "engineer"],
    "attachments:list":        ["admin", "engineer"],
    "attachments:upload":      ["admin", "engineer"],
    "attachments:download":    ["admin", "engineer", "production", "guest"],
    "attachments:delete":      ["admin", "engineer"],
    "bom:tree":                ["admin", "engineer", "production"],
    "bom:create_relation":     ["admin", "engineer"],
    "bom:delete_relation":     ["admin"],
    "parts.bom:manage":        ["admin", "engineer"],
}


def has_permission(role: str, permission: str) -> bool:
    """检查指定角色是否拥有某项权限。"""
    allowed = MYPDM_PERMISSIONS.get(permission, [])
    return role in allowed


@dataclass
class TokenResponse:
    """POST /api/auth/token 响应。"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@dataclass
class UserResponse:
    """GET /api/auth/me 响应。"""
    id: str
    username: str
    real_name: str
    role: str
    department: str | None = None
    phone: str | None = None
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class BomMatchItem:
    """BOM 匹配请求中的单个零部件项。"""
    code: str
    version: str | None = None


@dataclass
class BomMatchRequest:
    """POST /api/parts/cad/bom-match 请求。"""
    items: list[BomMatchItem]


@dataclass
class BomMatchResult:
    """单个零部件的匹配结果。"""
    code: str
    version: str
    match_status: str          # "matched" | "new" | "conflict"
    master_id: str | None = None
    revision_id: str | None = None
    name: str | None = None
    spec: str | None = None
    checkout_status: str | None = None   # "not_checked_out" | "checked_out" | "other_checked_out"
    checkout_user: str | None = None
    latest_version: str | None = None


@dataclass
class BomMatchResponse:
    """POST /api/parts/cad/bom-match 响应。"""
    results: list[BomMatchResult]


@dataclass
class BomSyncChild:
    """BOM 同步请求中的子项。"""
    code: str
    name: str | None = None
    spec: str | None = None
    quantity: int = 1
    instances: list[dict] = field(default_factory=list)  # [{matrix: [float*12], label: str}]


@dataclass
class BomSyncRequest:
    """POST /api/parts/revisions/{id}/cad/bom-sync 请求。"""
    children: list[BomSyncChild]


@dataclass
class BomSyncResponse:
    """POST /api/parts/revisions/{id}/cad/bom-sync 响应。"""
    created: int = 0
    updated: int = 0
    skipped: int = 0
    details: list[dict] = field(default_factory=list)


@dataclass
class CadNamingConfig:
    """GET /api/settings/cad-naming 响应。"""
    pdfPartPrefix: str = ""
    pdfAssemblyPrefix: str = ""
    stpPrefix: str = ""


@dataclass
class AttachmentResponse:
    """附件信息。"""
    id: str
    file_name: str
    file_size: int = 0
    category: str = "cad"


@dataclass
class PartCreateRequest:
    """POST /api/parts/ 创建零件请求。"""
    code: str
    name: str = ""
    spec: str = ""
    type: str = "part"   # "part" | "assembly"


@dataclass
class PartCreateResponse:
    """POST /api/parts/ 响应。"""
    id: str
    code: str
    name: str = ""
    version: str = "A"
```

- [ ] **Step 2: 验证模块可导入**

Run: `python -c "from catia_copilot.plm.my_pdm_schemas import TokenResponse, UserResponse, has_permission; print('OK')"`

Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add catia_copilot/plm/my_pdm_schemas.py
git commit -m "feat: 新增 myPDM API 数据模型 schemas"
```
