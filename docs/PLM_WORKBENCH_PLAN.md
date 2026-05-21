# PLM 工作台（PLM Workbench）开发计划

**文档版本**：v1.0  
**创建日期**：2026-05-21  
**状态**：实施中

---

## 一、目标

在现有 `PlmSyncDialog`（BOM 同步对话框）基础上，新建一个独立的 **PLM 工作台窗口**
（`PlmWorkbench`），将所有 PLM 对接功能整合到一处。  
原 `PlmSyncDialog` 保持不变，待新窗口验收通过后再决定其去留。

---

## 二、窗口形式

- **类型**：`QMainWindow`，非模态独立窗口（通过 `main_window._show_dialog` 管理单例）
- **入口**：`main_window.py` "BOM" Tab 新增按钮 `"PLM 工作台"`，位于"同步 BOM 到 PLM"按钮下方
- **尺寸**：900 × 640，最小 800 × 500
- **主题**：通过 `theme_manager.register()` 注册，随主窗口主题联动

---

## 三、布局：左侧导航 + 右侧内容区

```
┌──────────────────────────────────────────────────────────┐
│  PLM 工作台                                    [─][□][×]  │
├──────────┬───────────────────────────────────────────────┤
│          │                                               │
│ [连接]   │                                               │
│          │           右侧内容区（QStackedWidget）         │
│ [同步]   │                                               │
│          │                                               │
│ [标签]   │                                               │
│          │                                               │
│ [产品]   │                                               │
│          │                                               │
│ [历史]   │                                               │
│          │                                               │
└──────────┴───────────────────────────────────────────────┘
```

左侧导航为垂直按钮列表（`QPushButton`，`checkable=True`，宽度固定 80px），
点击切换右侧 `QStackedWidget` 的页面。

---

## 四、各页面详细设计

### 4.1 连接页（Connection）

**功能**：配置 PLM 连接参数、测试连接、显示当前用户信息。

**控件布局**：
```
QGroupBox "PLM 连接配置"
  QFormLayout
    服务端地址：  QLineEdit（默认 http://127.0.0.1:8001/...）
    用户名：      QLineEdit
    密码：        QLineEdit（Password 模式）
    工作区：      QLineEdit（默认 Workspace_0）
  QHBoxLayout
    [保存配置]  [测试连接]

QGroupBox "当前连接状态"（测试连接后显示）
  QLabel  登录用户：xxx
  QLabel  工作区：Workspace_0
  QLabel  工作区用户（共N人）：
  QListWidget  [alice, admin, bob, ...]（只读，从 GET /users 拉取）
```

**持久化**：与现有 `PlmSyncDialog` 共用同一组 `QSettings` key
（`CATIACompanion/PlmConfig`），两窗口配置互通。

**逻辑**：
- "测试连接"按钮：后台线程执行 `login()` + `GET /workspaces` + `GET /users`，
  成功后填充用户信息区，失败显示错误文本
- 连接状态用一个颜色点（绿/红/灰）显示在左侧导航"连接"按钮旁

---

### 4.2 同步页（Sync）

**功能**：从 CATIA 提取 BOM 并同步到 PLM，支持增量判断（G-01/G-03）。

**控件布局**：
```
QGroupBox "同步选项"
  QFormLayout
    不存在的零件：    ● 新建  ○ 跳过
    已签入的零件：    ● 跳过  ○ 签出后更新
    他人已签出：      ● 跳过  ○ 强制覆盖（禁用）
    更新后操作：      ● 自动签入  ○ 保留签出
    增量判断：        ☑ 仅同步有变化的零件（取消勾选=强制全量）
    STEP 上传：       ☐ 同步完成后上传 STEP 几何文件
    注册产品：        ☐ 同步完成后将顶层装配体注册为 PLM Product

QHBoxLayout（预设按钮）
  [新建模式]  [更新模式]

[开始同步]

─────────────────────────────────────────────────
状态标签（"就绪" / "正在同步..." / "同步完成"）
QProgressBar（不定进度，同步中可见）

QTableWidget  同步结果表格
  列：零件号 | 操作 | 状态 | 备注
  颜色：新建=绿，更新=蓝，跳过=灰，失败=红，他人锁定=橙

汇总行（"新建3 更新5 跳过2 未变化4 失败1"）
```

**增量判断逻辑（G-01/G-03）**：
1. 同步开始时，先调用 `GET /parts?start=0&count=500` 拉取工作区全量零件，
   缓存为 `{part_number: {"version":..., "attrs": {...}}}` 字典
2. BOM 每节点：PLM 不存在 → 按策略新建；已存在 → 对比 CATIA 属性与缓存属性，
   完全一致 → 标记"无变化-跳过"（不产生新迭代），有差异 → 按策略 checkout/update
3. `SyncOptions` 新增 `incremental: bool = True`；
   `SyncResult` 新增 `unchanged: int = 0`

**STEP 上传逻辑（G-05）**：
- 勾选后，BOM 同步完成后对每个 CATPart 节点：
  CATIA 导出 `.stp` 到临时目录 → `upload_step()` → 删除临时文件
- 上传结果在同步结果表格中单独展示

**Product 注册逻辑（G-04）**：
- 勾选后，整体同步完成后调用 `POST /products`，以顶层装配体的 Part Number 为
  `designItemNumber`，Nomenclature 为 `designItemName`
- 已存在则跳过，日志提示

---

### 4.3 标签页（Tags）

**功能**：管理工作区标签，配置 CATIA 属性到 Tag 的自动映射规则（G-02）。

**控件布局**：
```
QGroupBox "工作区标签"
  QListWidget  现有标签列表（GET /tags，只读）
  [刷新标签列表]

QGroupBox "自动打标签规则（BOM 同步时生效）"
  说明：根据零件"设计状态"属性值自动为零件打 Tag
  QTableWidget  规则表格
    列：CATIA 属性值 | PLM 标签 | 操作（[删除]）
  QHBoxLayout
    QLineEdit（CATIA属性值）  QComboBox（从PLM标签选择）  [添加规则]

QGroupBox "手动批量操作"
  QHBoxLayout
    QComboBox（选择标签）  [批量添加到所有零件]  [从所有零件移除]
```

**逻辑**：
- 映射规则持久化到 `QSettings`（`CATIACompanion/PlmTagRules`）
- 同步时在 `_do_update_and_checkin()` 后读取规则，调用 `update_part_tags()`
- 批量操作：后台线程拉取全量零件后逐个 PUT

---

### 4.4 产品页（Products）

**功能**：查看和管理 PLM 中的产品（Product）配置（G-04）。

**控件布局**：
```
QGroupBox "PLM 产品列表"
  QTableWidget
    列：产品ID | 根零件号 | 中文名 | 描述
  [刷新]  [新建产品]  [删除]

"新建产品"区域（内联展开）
  QFormLayout
    产品 ID：    QLineEdit
    根零件号：   QLineEdit
    说明：       QLineEdit
  [确认新建]
```

---

### 4.5 历史页（History）

**功能**：记录并展示最近 20 次同步操作结果（G-10）。

**控件布局**：
```
QListWidget  历史记录列表（左侧，最新置顶）
  每项：[时间] 新建N 更新N 失败N

QTextEdit  详细日志（右侧，只读，objectName="logView"）
  点击左侧条目后展示该次完整日志

[清空历史]（需二次确认）
```

**持久化**：`QSettings`（`CATIACompanion/PlmSyncHistory`），最多 20 条。

---

## 五、后端（api_client.py）新增方法

| 方法 | HTTP | 说明 |
|------|------|------|
| `list_parts(workspace, max_count=500)` | `GET /parts?start=0&count=N` | 全量零件列表 |
| `update_part_tags(workspace, pn, ver, tags)` | `PUT /parts/{pn}-{ver}` | 更新 tags（先 GET 再合并 PUT）|
| `list_products(workspace)` | `GET /products` | 获取产品列表 |
| `create_product(workspace, product_id, design_item_number, description)` | `POST /products` | 创建产品 |
| `list_tags(workspace)` | `GET /tags` | 工作区所有标签 |
| `list_users(workspace)` | `GET /users` | 工作区用户列表 |
| `get_part_detail(workspace, pn, ver)` | `GET /parts/{pn}-{ver}` | 零件详情（含 instanceAttributes）|

---

## 六、sync.py 修改点

| 修改项 | 详情 |
|--------|------|
| `SyncOptions` 新增字段 | `incremental`, `upload_step_files`, `register_product`, `tag_rules` |
| `SyncResult` 新增字段 | `unchanged`, `step_uploaded`, `product_registered` |
| 增量判断 | 同步前 `list_parts()` 建立缓存，属性对比决定是否操作 |
| Tag 写入 | `_do_update_and_checkin()` 后追加 `update_part_tags()` |
| STEP 上传 | checkin 后若 `upload_step_files=True`，CATIA COM 导出 + `upload_step()` |
| Product 注册 | `sync_bom_to_plm()` 末尾，若 `register_product=True`，`create_product()` |

---

## 七、main_window.py 修改点

- "BOM" Tab 新增按钮 `"PLM 工作台"`（位于"同步 BOM 到 PLM"下方）
- 新增槽函数 `_open_plm_workbench()`，调用 `_show_dialog`
- 工作台同步时联动 `_connection_timer`

---

## 八、新建/修改文件清单

| 文件 | 操作 | 预估行数 |
|------|------|----------|
| `catia_copilot/ui/plm_workbench.py` | 新建 | ~750 行 |
| `catia_copilot/plm/api_client.py` | 修改（+7个方法） | +~140 行 |
| `catia_copilot/plm/sync.py` | 修改（扩展逻辑） | +~180 行 |
| `catia_copilot/ui/main_window.py` | 修改（+按钮+槽） | +~20 行 |
| `docs/PLM_WORKBENCH_PLAN.md` | 新建 | 本文件 |

---

## 九、已知限制与风险

| 风险 | 说明 |
|------|------|
| `update_part_tags` 需 GET 再 PUT | DocdokuPLM `PUT /parts/{pn}-{ver}` 会覆盖整个 revision，需先 GET 现有字段再合并 tags 后 PUT |
| PLM-01（STEP 端点）未验证 | `upload_step` 的端点路径未在真实环境测试，G-05 实现后需优先手动验证 |
| STEP 导出依赖 CATIA COM | 需 CATIA 保持打开状态，COM 调用须在主线程完成 |
| 批量打 Tag 无并发 | 零件数量大时串行 PUT，暂不并发，避免 PLM 限流 |

---

## 十、测试验收清单

> 由开发方在实施完成后填写具体测试项，交付用户按清单逐条验收。

（见实施完成后附录）
