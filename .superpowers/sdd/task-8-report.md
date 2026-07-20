# Task 8 完成报告：PLM 工作台 Tab1 连接改造为 myPDM JWT 登录

**文件**：`catia_copilot/ui/plm_workbench.py`

## 修改摘要

将连接 Tab 从原来的 DocdokuPLM 连接方式改造为 myPDM JWT 登录方式。

### Step 1 — 导入 ✅
在 `PlmApiClient` 导入后添加 `MyPdmApiClient` 和 `MyPdmApiError` 导入。

### Step 2 — 默认配置 ✅
将默认的 `_DEFAULT_BASE_URL`、`_DEFAULT_LOGIN`、`_DEFAULT_PASSWORD`、`_DEFAULT_WORKSPACE` 改为 myPDM 对应的空默认值。

### Step 3 — `_ConnectWorker` 类 ✅
完全替换为新的 myPDM 连接 worker，使用 `MyPdmApiClient` 进行登录，返回 `current_user` 信息。

### Step 4 — `_pdm_client` 属性 ✅
在 `PlmWorkbench.__init__` 中添加 `self._pdm_client: MyPdmApiClient | None = None`。

### Step 5 — 标签文案 ✅
QGroupBox 标题从 "PLM 连接配置" 改为 "myPDM 连接配置"。

### Step 6 — 隐藏 workspace 字段 + 添加登录按钮 ✅
- 隐藏 workspace 输入框（myPDM 不需要）
- btn_row 添加"登录"按钮，保留原有的"保存配置"、"测试连接"和"→ 前往同步"

### Step 7 — 替换工作区详情 ✅
将下半部分的"工作区详情"QGroupBox（含 `_lbl_ws_detail` 和 `_tbl_users` 表格）替换为"连接日志" QGroupBox（含 `_txt_conn_log` QPlainTextEdit）。

### Step 8 — 添加用户信息显示区 ✅
在上半 top_row 和下半连接日志之间添加"用户信息" QGroupBox，含 `_lbl_user_info` QLabel。

### Step 9 — `_on_test_conn` 方法 ✅
重写为使用 `MyPdmApiClient.health()` 检查后端可达性。

### Step 10 — 登录方法 ✅
添加三个新方法：
- `_on_login_conn()` — 执行 myPDM 登录
- `_on_conn_login_success()` — 登录成功回调，显示用户信息和权限摘要
- `_on_conn_login_failure()` — 登录失败回调
- `_on_reauth_required()` — JWT 过期回调

### 额外清理 ✅
删除了孤立的 `_on_conn_ok` 方法（引用了已移除的 `_tbl_users` 和 `_lbl_ws_detail`）。

## 验证

- `python -m py_compile` 通过，语法正确
- 导入检查确认 `catia_copilot.plm.my_pdm_api_client` 模块存在
- 所有 10 个步骤均已执行
