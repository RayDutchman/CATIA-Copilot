# PLM 工作台业务逻辑文档

> 文件：`catia_copilot/ui/plm_workbench.py`（~3696 行）

---

## 一、整体架构

`PlmWorkbench(QDialog)` 是非模态独立窗口，通过 `QTabWidget` 分 2 个 Tab：

| Tab | 内容 |
|-----|------|
| 0 - 同步 | 工具栏 → 同步选项面板 → 差异表（三面板对比）→ 状态栏 |
| 1 - 设置 | 内嵌 QTabWidget: 连接配置 / 标签规则 / 同步历史 |

**依赖模块：**

| 模块 | 职责 |
|------|------|
| `workspace_scanner.py` | 本地文件扫描 + `.plm_parts_cache.json` 读写 |
| `api_client.py` | PLM REST API 封装 |
| `sync.py` | BOM 提取 + PLM 同步核心逻辑 |
| `bom_collect_v3.py` | CATIA COM 提取 BOM 树 |

---

## 二、核心数据结构

### 2.1 差异表列索引 `_DC_*`（16 列）

| 索引 | 常量 | 标题 | 数据来源 |
|------|------|------|---------|
| 0 | `_DC_SEL` | (checkbox) | 用户交互 |
| 1 | `_DC_WARN` | (warn icon) | 计算生成 |
| 2 | `_DC_PN` | 零件编号 | 本地文件 |
| 3 | `_DC_VER` | 版本 | PLM 缓存 |
| 4 | `_DC_ITER` | 迭代 | PLM 缓存 |
| 5 | `_DC_LVER` | 本地版本 | 本地文件 CATIA 属性 |
| 6 | `_DC_LITER` | 本地迭代 | 本地文件 CATIA 属性 |
| 7 | `_DC_NAME` | 零件名称 | PLM 缓存 |
| 8 | `_DC_TYPE` | 类型 | 本地文件 |
| 9 | `_DC_AUTHOR` | 作者 | PLM 缓存 |
| 10 | `_DC_LMTIME` | 本地修改时间 | 本地文件 mtime |
| 11 | `_DC_PMTIME` | PLM 修改时间 | PLM 缓存（详见 2.3） |
| 12 | `_DC_LCST` | 生命周期状态 | PLM 缓存 |
| 13 | `_DC_COUT` | 签出者 | PLM 缓存 |
| 14 | `_DC_FILES` | 📎 | PLM 缓存 |
| 15 | `_DC_DIFF` | 状态 | `_compute_diff_status()` |

### 2.2 差异状态 `_ST_*`

| 常量 | 显示 | 含义 |
|------|------|------|
| `_ST_UNKNOWN` | ? | 未知 |
| `_ST_OK` | ✓ 一致 | 本地=PLM |
| `_ST_LOCAL_NEW` | ↑ 本地新 | 本地有更改，应 Push |
| `_ST_PLM_NEW` | ↓ PLM新 | PLM 有更新，应 Pull |
| `_ST_LOCAL_ONLY` | 仅本地 | 仅本地存在 |
| `_ST_PLM_ONLY` | 仅PLM | 仅 PLM 存在 |
| `_ST_NO_SYNC` | ⚠ 无法同步 | 文件未保存/不可读 |

### 2.3 PLM 修改时间数据源

`extract_part_summary`（`api_client.py`）从最新迭代取值：

```
优先 modificationDate  →  update_iteration PUT 完成时写入
回退 checkInDate      →  checkin_part PUT 完成时写入
```

### 2.4 `_diff_rows` 行数据

```python
{
    "pn":     str,           # 零件号
    "local":  LocalPartInfo, # 本地文件信息
    "plm":    dict,          # PLM 缓存摘要
    "status": str,           # _ST_* 状态
    "row":    int,           # 表格行号
}
```

### 2.5 `_plm_cache` 缓存格式

```python
{
    "number":              "BevelGear",       # str
    "version":             "A",               # str
    "lastIterationNumber": 4,                 # int
    "name":                "锥齿轮",           # str
    "checkOutUser":        "admin",           # str
    "modificationDate":    "2026-06-17T...",  # str (ISO, 实际为 modificationDate 优先)
    "authorLogin":         "admin",           # str
    "lifecycleState":      "WIP",             # str
    "tags":                ["tag_id"],        # list[str]
}
```

---

## 三、差异表填充流程 `_populate_diff_table`

### 3.1 数据准备阶段

```
1. 从 self._local_parts 建立 local_map: {pn: LocalPartInfo}
2. 合并所有 pn（本地 + 缓存），去重保序
3. 遍历 each pn:
   - 取 local = local_map.get(pn)
   - 取 plm  = self._plm_cache.get(pn)
   - 调用 _compute_diff_status(local, plm)
   - 若 pn in self._sync_just_pushed → 强制 _ST_OK
   - 填充 16 列单元格
   - 追加到 self._diff_rows
4. 清空 self._sync_just_pushed
```

### 3.2 差异状态计算 `_compute_diff_status`

```
入参: local: LocalPartInfo, plm: dict (from cache)

流程:
  local==None and plm==None  → _ST_UNKNOWN
  local==None                → _ST_PLM_ONLY
  plm==None                  → _ST_LOCAL_ONLY
  local 不可操作（未保存等）   → _ST_NO_SYNC
  版本/迭代号不同:
    (loc_ver, loc_iter) > (plm_ver, plm_iter) → _ST_LOCAL_NEW
    (loc_ver, loc_iter) < (plm_ver, plm_iter) → _ST_PLM_NEW
  版本/迭代号相同:
    比较 local.mtime vs plm.modificationDate
    |local_utc - plm_mtime| ≤ 60s → _ST_OK
    local_utc > plm_mtime → _ST_LOCAL_NEW
    else                  → _ST_PLM_NEW
```

### 3.3 时间比较容差说明

`_DIFF_TIME_TOLERANCE_SEC = 60`（模块级常量，行 93）

**为什么需要容差**：Push 流程中，每个零件在 Phase 1 获得 `modificationDate`（`update_iteration` 时），而本地 mtime 在 Phase 2（`_write_plm_attrs_to_catia(save=True)`）才被刷新。对于处理顺序靠前的零件，两个时间差可达几分钟，容差避免误判。

**容差后仍能检测的场景**：
- 用户保存文件后未 Push：local mtime >> PLM modificationDate => `_ST_LOCAL_NEW`
- 他人 Push 新版本后未 Pull：PLM modificationDate >> local mtime => `_ST_PLM_NEW`

---

## 四、Push 流程（同步到 PLM）

### 4.1 完整时序

```
用户点击 "⬆ Push 选中"
  → _on_sync_start()
    1. 收集勾选行 → push_rows
    2. 【实时查询 PLM】search_parts_summary → 更新 row_data["plm"] + 重算 status
    3. 检查未保存文件
    4. 检查 PLM_NEW 状态（用实时数据）→ 弹确认框
    5. 构建 SyncOptions + push_map
    6. 记录 _sync_just_pushed = set(push_map.keys())
    7. 启动 _SyncWorker（后台线程）

  → _SyncWorker.run()
    Phase 1（逐节点，深度后序遍历）:
      Node A: checkout → update_iteration → uploads
      Node B: checkout → update_iteration → uploads
      ...
      Node Z: checkout → update_iteration → uploads
    Phase 2（批量签入）:
      for each ticket: checkin → _write_plm_attrs(save=True)
    → emit sync_done(result)

  → _on_sync_done(result)
    1. 更新摘要显示
    2. 保存同步历史
    3. 若有新创建或更新 → QTimer.singleShot(800, _on_refresh_plm_status)

  → _on_refresh_plm_status
    → _PlmStatusWorker（后台查询 PLM）

  → _on_plm_status_done
    → merge_plm_cache（持久化缓存）
    → _populate_diff_table
      → pn in _sync_just_pushed → _ST_OK（绕过时间比较）
      → 清除 _sync_just_pushed
```

### 4.2 Push 前置实时查询

在 `_on_sync_start` 中 L1570-1595：

```
1. 创建 PlmApiClient + login
2. search_parts_summary(workspace, push_rows_pns)
3. 逐行对比 缓存plm vs 实时plm 的 version/iteration
   若有变化 → 更新 row_data["plm"] + 重算 status + 更新缓存
4. 用更新后的 status 检查 PLM_NEW
```

**目的**：确保 PLM_NEW 警告基于 PLM 实时数据，而非可能过时的缓存。

---

## 五、Pull 流程（从 PLM 拉取）

### 5.1 勾选行 Pull `_on_pull_selected`

```
1. 遍历勾选行 → 从 diff_rows 收集 (pn, ver, iter)（缓存数据）
2. 创建 PlmApiClient + login
3. 【实时查询 PLM】search_parts_summary → 用最新 version/iteration 覆盖 checked 列表
4. 遍历 checked → list_part_attachments 获取文件列表
5. 启动 _PullWorker 批量下载到 work_dir/{pn}/
```

### 5.2 BOM Pull `_on_pull`（弹出 _PullDialog）

使用 `_PullWorker.MODE_BOM` → `MODE_DOWNLOAD` 两步流程，递归展开 BOM 树后下载。

---

## 六、PLM 缓存系统

### 6.1 本地缓存文件

**路径**: `{work_dir}/.plm_parts_cache.json`（Windows 上设为隐藏属性）

**版本控制**: `_PLM_CACHE_VERSION = 1`，版本不匹配时丢弃并 warning。

**读写逻辑**（`workspace_scanner.py`）：

| 函数 | 操作 | 调用方 |
|------|------|--------|
| `load_plm_cache` | 读文件 → JSON → 校验版本 → 返回{ } | 加载工作区时 |
| `save_plm_cache` | dict → JSON → 写文件 → 设隐藏属性 | 新增 PLM Part 完成时 |
| `merge_plm_cache` | load → update → save | 刷新 PLM 状态时 |

### 6.2 缓存生命周期（读-改-写完整路径）

| # | 触发 | 文件操作 | 说明 |
|---|------|---------|------|
| 1 | ↺ 加载工作区 | `load_plm_cache` → 仅读 | 展示缓存状态 |
| 2 | ☁ 刷新 PLM 状态 | `merge_plm_cache` → 读→改→写 | 全量查询后合并 |
| 3 | + 新增 PLM Part | `merge_plm_cache` → 读→改→写 | 新增零件合并 |
| 4 | ⬆ Push 完成 | 自动触发链2 → `merge_plm_cache` | 刷新最新状态 |

### 6.3 缓存与实时数据的权衡

| 用途 | 数据源 | 时效性 |
|------|--------|--------|
| 差异表显示 | `_plm_cache`（文件缓存） | 上次查询时的快照 |
| PLM_NEW 检查 | Push 前实时 `search_parts_summary` | 实时 |
| Pull 版本/迭代选择 | Pull 前实时 `search_parts_summary` | 实时 |
| 增量跳过判断（sync.py） | `get_part_head()` 同步开始实时查询 | 实时 |

PLM 缓存 **仅用于 UI 展示**，所有关键决策（Push/Pull 前置检查、增量同步判断）都基于实时 PLM 查询。

---

## 七、关键常量与配置

### 7.1 升级策略 `_UPGRADE_*`

| 常量 | 含义 | 使用场景 |
|------|------|---------|
| `_UPGRADE_SKIP` | 不推送 | 跳过该零件 |
| `_UPGRADE_ITER` | +迭代 | 默认：同版本下新建迭代 |
| `_UPGRADE_VER` | +版本 | 提升版本号 |

### 7.2 同步模式 `SyncMode`

| 模式 | 阶段二行为 |
|------|-----------|
| `CHECKIN` | 全部 checkin |
| `KEEP_CHECKOUT` | 保留签出，不 checkin |

### 7.3 连接配置

通过 QSettings 持久化（`CATIACopilot/PlmConfig`），包括：
`base_url` / `login` / `password` / `workspace` / `work_dir`

### 7.4 标签规则

通过 QSettings + JSON 持久化（`CATIACopilot/PlmTagRules`），每条规则：
```python
{"catia_value": "WIP", "plm_tag": "tag_wip"}
```

---

## 八、Worker 类一览

| 类 | 后台操作 | 信号 |
|----|---------|------|
| `_ConnectWorker` | PLM 登录测试 | `success(login, users, ws_info)`, `failure(err)` |
| `_SyncWorker` | BOM 提取 + 同步 | `progress(msg)`, `sync_done(result)`, `upload_log(...)`, `error(err)` |
| `_TagsWorker` | 获取标签列表 | `success(tags)`, `failure(err)` |
| `_CreateTagWorker` | 创建新标签 | `success(label)`, `failure(err)` |
| `_PlmStatusWorker` | 批量查询 PLM 状态 | `done({pn: summary})`, `failure(err)`, `progress(done, total)` |
| `_WorkspaceScanWorker` | 本地工作区扫描 | `scan_done(parts)`, `failure(err)`, `progress(...)` |
| `_PullWorker` | 搜索/BOM/下载/预查询 | 多模式，输出搜索结果/BOM 树/文件进度/下载完成 |

所有 Worker 在 `_start_worker` 中统一管理生命周期（`QThread` + `finished` 清理）。

---

## 九、Dialog 类一览

| 类 | 功能 | 关键方法 |
|----|------|---------|
| `PlmWorkbench` | 主工作台 | 见全文 |
| `_SettingsDialog` | 连接配置 + 标签规则 CRUD | `_save_conn()`, `_add_rule()`, `_delete_rule()` |
| `_HistoryDialog` | 同步历史查看/清空 | `_refresh_history_list()`, `_on_clear_history()` |
| `_PullDialog` | BOM 展开 + 附件下载 | 搜索零件 → 递归 BOM → 对比本地 → 下载 |

---

## 十、时序与容差总结

### 10.1 同步时序（Push）

```
T0: 用户保存文件 → local.mtime = T0
T1: Push 开始
    逐节点（后序深度优先）:
      Node N: checkout → update_iteration → upload
      → PLM.modificationDate[N] = T1_N
T2: 批量 checkin → checkinDate = T2
    _write_plm_attrs(save=True) → local.mtime = T2_N
T3: 自动刷新 PLM 状态
    _populate_diff_table 对比:
      local.mtime ≈ T2_N, PLM.modificationDate = T1_N
      |T2_N - T1_N| ≤ 60s? （取决于节点处理顺序）
      刚 Push 的零件绕过比较 → _ST_OK
```

### 10.2 差异表时间列（列 10/11）含义

| 列 | 含义 | 来源 |
|----|------|------|
| 本地修改时间 | 文件系统 mtime | `local.mtime.strftime(...)` |
| PLM修改时间 | 最新迭代 modificationDate（→checkInDate） | `_format_plm_date(plm["modificationDate"])` |
| 时间比较 | 仅版本+迭代相同时触发 | `_compute_diff_status`（60s 容差） |

### 10.3 可能的问题场景

| 场景 | 影响 | 处理 |
|------|------|------|
| 缓存文件未加载 | 所有行无 PLM 数据 → 仅本地 | 需先加载工作区或刷新 |
| 缓存版本不匹配 | 缓存被丢弃 → 重新全量查询 | warning log |
| 同步写入文件失败 | local.mtime 停留在保存时间 | warning log，下次 Push 自动覆盖 |
| 旧缓存列名不对应 | 无（已不依赖缓存的列名判断） | — |

---

## 十一、历史会话重要修改记录

| 修改 | 日期 | 说明 |
|------|------|------|
| 实时 PLM 查询（Push 前） | 2026-06 | `_on_sync_start` 新增实时查询取代缓存状态判断 |
| 实时 PLM 查询（Pull 前） | 2026-06 | `_on_pull_selected` 新增实时版本/迭代查询 |
| `_sync_just_pushed` 机制 | 2026-06 | 刚 Push 的零件在自动刷新时绕过时间比较 |
| 时间容差 60s | 2026-06 | `_DIFF_TIME_TOLERANCE_SEC` 替代 1s 精确比较 |
| `modificationDate` 优先 | 2026-06 | `extract_part_summary` 交换字段优先级 |
| Windows 隐藏属性 | 2026-06 | `.plm_parts_cache.json` 写入后设 `FILE_ATTRIBUTE_HIDDEN` |
| 链3修复（新增 PLM Part） | 2026-06 | `save_plm_cache` 改为 `merge_plm_cache` 避免覆写 |
