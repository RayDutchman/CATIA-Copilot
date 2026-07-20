# CATIA-Copilot × myPDM 融合方案设计文档

**日期**: 2026-07-20
**版本**: 1.0

---

## 1. 概述

### 1.1 目标

将 myPDM 项目的 CAD入口 功能合并到 CATIA-Copilot 的 PLM 工作台中，整体切换为对接 myPDM 后端服务器，实现从 CATIA 桌面端到 myPDM PDM 系统的完整工作流。

### 1.2 关键决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| PLM 后端 | 整体切换为 myPDM 后端 | 统一数据源，避免双后端维护 |
| CATIA 交互 | 复用现有 pycatia | 不引入 cad_bridge，减少依赖，同进程调用性能更好 |
| 文件上传 | pycatia 导出本地 → HTTP 直传 | 不经过中间层，简单直接 |
| 工作台结构 | 保持 5 标签页，连接+同步标签页重点改造 | 保留框架，最小化改动 |

### 1.3 涉及项目

| 项目 | 路径 | 角色 |
|------|------|------|
| CATIA-Copilot | `D:\OpenCode\CATIA-Copilot` | 目标项目，桌面应用 |
| myPDM | `D:\OpenCode\myPDM` | 后端 API 来源，功能参考 |

---

## 2. 架构总览

```
┌──────────────────────────────────────┐     HTTPS/JWT      ┌─────────────────┐
│  CATIA-Copilot (PySide6 桌面应用)      │◄──────────────────►│  myPDM 后端      │
│                                      │                    │  (FastAPI)      │
│  ┌─ PLM 工作台 ────────────────────┐  │                    │                 │
│  │  Tab 1: 连接 (myPDM 登录)       │  │  POST /auth/token  │  PostgreSQL     │
│  │  Tab 2: CAD入口·同步 (核心)      │  │  GET  /auth/me     │  Redis          │
│  │  Tab 3: 标签管理                │  │  POST /auth/refresh│                 │
│  │  Tab 4: 产品管理                │  │  /api/parts/*      │                 │
│  │  Tab 5: 操作历史                │  │  /api/attachments/*│                 │
│  └────────────────────────────────┘  │                    │                 │
│                                      │                    │                 │
│  ┌─ CATIA COM 层 (pycatia) ───────┐  │                    │                 │
│  │  assembly_reader.py  [新增]     │  │                    │                 │
│  │  property_rw.py      [新增]     │  │                    │                 │
│  │  file_exporter.py    [新增]     │  │                    │                 │
│  │  bom_collect.py      [已有]     │◄─ COM ─────────────►│  CATIA V5/V6    │
│  │  connection.py       [已有]     │  │                    │                 │
│  └────────────────────────────────┘  │                    │                 │
└──────────────────────────────────────┘                    └─────────────────┘
```

---

## 3. 认证与权限

### 3.1 认证流程

myPDM 使用 JWT (HS256) 认证：

- **登录**: `POST /api/auth/token`（Content-Type: application/x-www-form-urlencoded）
  - 参数: `username`, `password`
  - 返回: `{access_token, refresh_token, token_type: "bearer"}`
- **用户信息**: `GET /api/auth/me`（Bearer token）
  - 返回: `{id, username, real_name, role, department, phone, status, created_at, updated_at}`
- **令牌刷新**: `POST /api/auth/refresh`
  - 参数: `{refresh_token}`
  - 返回: 新 token 对

### 3.2 Token 生命周期

| 令牌 | 有效期 | 密钥 | 存储 |
|------|--------|------|------|
| access_token | 480 分钟 (8h) | JWT_SECRET (HS256) | 内存 |
| refresh_token | 7 天 | JWT_SECRET (HS256) | QSettings 持久化 |

### 3.3 角色与权限

4 个角色: `admin`, `engineer`, `production`, `guest`

权限矩阵（本地硬编码，来源 `myPDM/permissions/permissions.json`）：

| 权限 | admin | engineer | production | guest |
|------|-------|----------|------------|-------|
| parts:read | ✓ | ✓ | ✓ | ✓ |
| parts:create | ✓ | ✓ | | |
| parts:update | ✓ | ✓ | | |
| parts:delete | ✓ | | | |
| parts:checkout | ✓ | ✓ | | |
| parts:checkin | ✓ | ✓ | | |
| parts:undocheckout | ✓ | ✓ | | |
| attachments:upload | ✓ | ✓ | | |
| attachments:download | ✓ | ✓ | ✓ | ✓ |

客户端根据角色推导可用权限，控制 UI 按钮启用/禁用。

---

## 4. 标签页设计

### 4.1 Tab 1 — 连接

**左侧 — 配置表单:**
- 服务端地址（默认 `https://<myPDM-host>:8443/api`）
- 用户名
- 密码
- 操作按钮: "保存配置"、"测试连接"、"登录"

**右侧 — 连接日志:** QPlainTextEdit 只读

**登录成功后 — 用户信息卡片:**
- 真实姓名、用户名、角色标签（admin=红, engineer=蓝, production=绿, guest=灰）
- 部门、电话
- 权限摘要: 列出角色对应的关键权限（零件创建、签入签出、附件上传等）

**逻辑:**
- "测试连接" → GET `/api/health` 验证后端可达
- "登录" → POST `/api/auth/token` → GET `/api/auth/me`
- access_token 保存内存，refresh_token 保存 QSettings
- 所有后续 API 请求自动携带 `Authorization: Bearer <access_token>`
- 401 自动触发 refresh → 重试

### 4.2 Tab 2 — CAD入口·同步（核心）

双模式设计，顶部选项卡切换。

#### 模式一: CAD入口向导（三步）

**步骤① — 连接CATIA:**
- "检测 CATIA"按钮 → pycatia 检测 CATIA COM 是否可用 + 活动文档信息
- 显示: 桥接状态（始终可用，因为 pycatia 内嵌）、CATIA 状态、文档名称、文档类型
- "读取装配结构"按钮 → 递归遍历装配树（含变换矩阵、全部属性、源文件路径）
- 执行 `flattenTree()`: 同父节点下同件号实例合并，用量累加
- 进入步骤②

**步骤② — BOM匹配表格:**

进入时自动调用 `POST /api/parts/cad/bom-match` 批量匹配 PDM。

汇总栏: 已匹配(绿) | 可新建(黄) | 冲突(红) | 已签出(蓝)

表格列定义:

| 列 | 说明 | 可编辑 | 背景色 |
|----|------|--------|--------|
| 层级 | 装配树路径 `0.1.2` | 否 | 默认 |
| 件号 | PartNumber | 是（写回CATIA） | 默认 |
| 用量 | 实例数量 | 否 | 默认 |
| 版本 | Revision（内置属性） | 是 | 天蓝 |
| 定义 | Definition（内置属性） | 是 | 天蓝 |
| 术语 | Nomenclature（内置属性） | 是 | 天蓝 |
| 描述 | DescriptionRef（内置属性） | 是 | 天蓝 |
| 规格型号等 | 用户自定义属性（动态列） | 是 | 绿色 |
| CAD附件 | 计数 + 上传按钮 | — | 蓝色 |
| 生产附件 | PDF/STP 上传按钮 | — | 琥珀色 |
| PDM匹配 | 匹配到的零部件信息 | — | 默认 |
| 匹配状态 | 已匹配/可新建/冲突/未知 | — | 默认 |
| 签出状态 | 未签出/已签出/他人签出 | — | 默认 |
| 操作 | 按钮组 | — | 默认 |

操作按钮矩阵:

| 匹配状态 | 签出状态 | 可用操作 |
|----------|----------|----------|
| new | — | "创建零件" |
| matched | not_checked_out | "签出"、"属性←" |
| matched | checked_out | "签入"、"属性→"、"撤销签出" |
| matched | other_checked_out | "属性←"（只读） |

关键操作逻辑:

- **属性编辑**: 内联编辑 → pycatia 即时写回 CATIA → syncRowsByPartNumber() 同步同件号所有实例行
- **签出**: POST `/api/parts/revisions/{id}/checkout`
- **签入**: POST `/api/parts/revisions/{id}/checkin`
- **属性→** (推送PDM): PUT `/api/parts/{id}` 更新固定字段 + 自定义字段 → 装配体自动 POST `/api/parts/revisions/{id}/cad/bom-sync` 推送子项 BOM（含变换矩阵）
- **属性←** (拉取): GET PDM 信息 → 写规格型号到 CATIA
- **创建零件**: POST `/api/parts/` {code, name, spec, type}
- **CAD源文件上传**: pycatia 获取文件路径 → HTTP multipart 上传到 POST `/api/parts/revisions/{id}/attachments` (category=cad, overwrite=true) + 同名 CATDrawing
- **PDF上传**: pycatia 导出 CATDrawing → PDF 到临时目录 → HTTP 上传 (category=production)
- **STP上传**: pycatia 导出 STP 到临时目录 → HTTP 上传 (category=production)
- **重新匹配**: 重新读取 CATIA 装配树 + 重新 PDM 匹配

批量操作: "重新匹配"、"全部签入"、"批量推送属性"

行颜色:
- `match_status=new` → 黄色背景
- `checkout_status=checked_out` → 蓝色背景

**步骤③ — 完成摘要:**
- 显示大号对勾 + "操作完成"
- "本次共处理 N 个零部件"
- "关闭"按钮 → 刷新 → 返回

#### 模式二: 批量BOM同步

保留现有同步功能，后端切换到 myPDM API:

- 同步选项面板:
  - 不存在的零件: 新建 / 跳过
  - 已签入的零件: 签出后更新 / 跳过
  - 他人已签出: 跳过
  - 更新后操作: 自动签入 / 保留签出
- 复选框:
  - 增量同步
  - 上传 CATIA 文件
  - 上传 STP 几何文件
  - 上传图纸 PDF
- 预设按钮: "新建模式"、"更新模式"
- 树形结果视图 + 进度条

### 4.3 Tab 3 — 标签管理

- 通过 myPDM API 获取/更新零件标签
- CATIA 属性值 → myPDM 标签自动映射规则（本地 QSettings 持久化）
- 同步完成后根据"设计状态"属性自动打标签
- 保留现有 UI: 标签列表 + 映射规则表格

### 4.4 Tab 4 — 产品管理

- 保留现有 UI: 产品列表 + 新建产品表单
- 对接 myPDM 的顶层装配体注册 API
- "从当前 BOM 自动填入"功能保留

### 4.5 Tab 5 — 操作历史

完全不变。本地 QSettings `("CATIACompanion", "PlmSyncHistory")` 持久化，最多 20 条。包含时间、新建/更新/跳过/失败数量、用户、模式。

---

## 5. 新增模块

### 5.1 API 客户端层

**`plm/my_pdm_api_client.py`** — 替代 `plm/api_client.py`

封装所有 myPDM 后端 API:

| 方法 | HTTP | 路径 | 用途 |
|------|------|------|------|
| login | POST | /auth/token | JWT 登录 |
| refresh_token | POST | /auth/refresh | 刷新令牌 |
| get_me | GET | /auth/me | 当前用户信息 |
| health | GET | /health | 健康检查 |
| list_parts | GET | /parts | 零件列表 |
| create_part | POST | /parts | 创建零件 |
| get_part | GET | /parts/{id} | 零件详情 |
| update_part | PUT | /parts/{id} | 更新零件 |
| delete_part | DELETE | /parts/{id} | 删除零件 |
| checkout | POST | /parts/revisions/{id}/checkout | 签出 |
| checkin | POST | /parts/revisions/{id}/checkin | 签入 |
| undocheckout | POST | /parts/revisions/{id}/undocheckout | 撤销签出 |
| cad_bom_match | POST | /parts/cad/bom-match | CAD BOM 匹配 |
| cad_bom_sync | POST | /parts/revisions/{id}/cad/bom-sync | CAD BOM 子项同步 |
| list_attachments | GET | /parts/revisions/{id}/attachments | 附件列表 |
| upload_attachment | POST | /parts/revisions/{id}/attachments | 整包上传附件 |
| delete_attachment | DELETE | /parts/revisions/{id}/attachments/{att_id} | 删除附件 |
| get_cad_naming | GET | /settings/cad-naming | CAD 命名前缀配置 |

Token 管理:
- `_access_token`: 内存
- `_refresh_token`: QSettings
- 请求拦截: 自动附加 `Authorization: Bearer {token}`
- 401 响应: 自动 refresh → 重试 → 失败则触发重新登录

**`plm/my_pdm_schemas.py`** — 数据模型

Pydantic/dataclass 模型: `TokenResponse`, `UserResponse`, `PartResponse`, `BomMatchRequest`, `BomMatchResponse`, `BomSyncRequest`, `CadNamingConfig` 等。

### 5.2 CATIA COM 层

**`catia/assembly_reader.py`** — 装配树读取

对标 myPDM cad_bridge 的 `catia.assembly.read_tree`:

```python
def read_assembly_tree(product_doc=None):
    """
    递归读取 CATIA 装配体产品结构树。
    返回:
    {
        "instance_name": str,      # 实例名称
        "part_number": str,         # 件号 (builtin.PartNumber)
        "path": str,                # 路径索引 (如 "0.1.2")
        "is_assembly": bool,        # 是否为装配体
        "doc_path": str,            # 源文档完整路径
        "builtin": dict,            # 内置属性 {PartNumber, Revision, Definition, Nomenclature, DescriptionRef}
        "user_properties": dict,    # 用户自定义属性
        "matrix": [float] * 12,     # 变换矩阵 (3x4)
        "children": [...]           # 递归子节点
    }
    """
```

**`catia/property_rw.py`** — 属性读写

```python
def read_properties(instance_path: str) -> dict:
    """读取指定路径实例的全部属性（内置+自定义）"""

def write_property(instance_path: str, prop_name: str, value: Any) -> bool:
    """写入属性到 CATIA，自动区分内置/自定义属性"""
```

**`catia/file_exporter.py`** — 文件导出

```python
def export_stp(instance_path: str, output_path: str) -> str:
    """导出零部件为 STP 格式"""

def export_pdf(drawing_path: str, output_path: str) -> str:
    """将 CATDrawing 转换为 PDF"""
```

### 5.3 UI 组件

**`ui/cad_match_table.py`** — BOM匹配表格控件

基于 `QTableWidget` + 自定义 delegate 实现内联编辑、颜色渲染、动态列、操作按钮。

**`ui/cad_connect_step.py`** — CAD入口步骤①控件

CATIA 状态检测 + 装配树读取 UI。

**`ui/cad_complete_step.py`** — CAD入口步骤③控件

完成摘要 UI。

**`ui/flatten_tree.py`** — 装配树扁平化

从 myPDM `src/components/CADWorkspace/flattenTree.ts` 移植：

```python
def flatten_tree(assembly_tree: dict) -> list[dict]:
    """
    递归扁平化 CATIA 装配树。
    同父节点下同件号实例合并为一行，用量累加，所有变换矩阵保留。
    """
```

**`ui/sync_rows.py`** — 属性同步

从 myPDM `src/components/CADWorkspace/syncRows.ts` 移植：

```python
def sync_rows_by_part_number(rows: list[dict], changed_row: dict, property_name: str, value: Any) -> list[dict]:
    """
    按 PartNumber 同步同零部件的所有实例行属性更新。
    PartNumber 为空时回退为仅按 path 更新当前行。
    """
```

---

## 6. 数据流

### 6.1 CAD入口向导完整流程

```
1. 用户打开 Tab2 → 选择"CAD入口向导"
2. [步骤①] 点击"检测 CATIA"
   → pycatia 检测 CATIA COM → 显示状态
3. [步骤①] 点击"读取装配结构"
   → assembly_reader.read_assembly_tree() → flatten_tree() → BOMRow[]
4. [步骤②] 自动 PDM 匹配
   → POST /api/parts/cad/bom-match({items: [{code, version}, ...]})
   → 更新每行 match_status + checkout_status
5. [步骤②] 用户交互
   → 编辑属性 → property_rw.write_property() → sync_rows_by_part_number()
   → 签出/签入 → my_pdm_api_client.checkout/checkin()
   → 属性推送 → update_part() + cad_bom_sync()（装配体）
   → 文件上传 → file_exporter.export_stp/pdf() → upload_attachment()
6. [步骤②→③] 点击"完成"
   → 显示步骤③摘要
```

### 6.2 批量BOM同步流程

```
1. 用户打开 Tab2 → 选择"批量BOM同步"
2. 点击"从 CATIA 加载 BOM"
   → bom_collect / assembly_reader 读取 BOM
3. 配置同步选项
4. 点击"开始同步"
   → 遍历 BOM 树 → 调用 myPDM API 创建/更新/签入签出
   → 可选上传附件
5. 显示树形结果视图
```

---

## 7. 增量同步策略

复用现有设计的 `SyncOptions.incremental` 机制：
1. 调用 `list_parts()` 拉取 PDM 全量零件列表 → 构建 `plm_parts_cache`
2. 对每个 BOM 节点，对比 CATIA 属性与 PDM 缓存属性
3. 属性完全一致 → 标记"无变化-跳过"
4. 有变化 → 正常更新流程

---

## 8. 文件清单

### 新增文件

| 文件 | 行数估算 | 说明 |
|------|----------|------|
| `catia_copilot/plm/my_pdm_api_client.py` | ~400 | myPDM JWT 客户端 |
| `catia_copilot/plm/my_pdm_schemas.py` | ~150 | API 数据模型 |
| `catia_copilot/catia/assembly_reader.py` | ~300 | 装配树递归读取 |
| `catia_copilot/catia/property_rw.py` | ~200 | CATIA 属性读写 |
| `catia_copilot/catia/file_exporter.py` | ~150 | STP/PDF 导出 |
| `catia_copilot/ui/cad_match_table.py` | ~600 | BOM匹配表格控件 |
| `catia_copilot/ui/cad_connect_step.py` | ~150 | CAD入口步骤① |
| `catia_copilot/ui/cad_complete_step.py` | ~80 | CAD入口步骤③ |
| `catia_copilot/ui/flatten_tree.py` | ~80 | 装配树扁平化 |
| `catia_copilot/ui/sync_rows.py` | ~60 | 属性同步算法 |

### 修改文件

| 文件 | 改动范围 | 说明 |
|------|----------|------|
| `catia_copilot/ui/plm_workbench.py` | 大面积改造 | 5个标签页重新实现 |
| `catia_copilot/plm/sync.py` | 中等改造 | 适配 myPDM API |
| `catia_copilot/plm/__init__.py` | 小改 | 导出新模块 |
| `catia_copilot/constants.py` | 小改 | 新增 myPDM 相关常量 |

### 不再使用

| 文件 | 说明 |
|------|------|
| `catia_copilot/plm/api_client.py` | 由 my_pdm_api_client.py 替代 |
| `catia_copilot/ui/plm_sync_dialog.py` | 旧版对话框，合并到 workbench |

---

## 9. 兼容性与风险

| 风险 | 缓解 |
|------|------|
| myPDM 后端 API 变更 | 前端 API 调用集中到 my_pdm_api_client.py，单点修改 |
| pycatia COM 稳定性 | 复用经过验证的 connection.py，增加重试机制 |
| 大文件上传超时 | 整包上传限额 ~100MB，超大文件走分块上传（后期优化） |
| DocdokuPLM 历史数据 | PLM 工作台整体切换，不再连接 DocdokuPLM |
| 权限不足导致功能不可用 | 根据角色控制 UI，不可用功能灰显 + 提示 |

---

## 10. 后续扩展

- 分块上传支持（>100MB 文件）
- 多人签出冲突处理优化
- STP/PDF 导出进度条
- CATIA 属性字段映射配置可视化
