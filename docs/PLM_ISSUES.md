# PLM 端问题记录

本文件记录在 CATIA-Copilot ↔ DocdokuPLM 对接过程中发现的**PLM 服务端**问题或限制，
供后续与 PLM 管理员/开发人员对接时参考。

---

## 问题列表

### PLM-01：STEP 几何文件上传接口未验证

**状态**：待验证  
**发现时间**：2026-05

**描述**  
`PlmApiClient.upload_step()` 已实现 multipart POST 到端点：
```
POST /workspaces/{ws}/parts/{pn}-{ver}/iterations/{iter}/geometry
```
但在真实 DocdokuPLM（Payara 5.194）环境下，该端点从未实际调用过，
响应格式、权限要求、文件大小限制均未验证。

**影响**  
STEP 几何上传功能目前在 UI 层硬编码关闭（`upload_step=False`），
用户无法使用。

**待确认**
- 端点路径和 HTTP 方法是否正确（官方 REST 文档）
- 服务端是否要求 `Content-Disposition` 中的 `name` 字段为特定值
- 文件大小是否有限制，是否需要分片上传

---

### PLM-02：属性更新无增量检测（性能问题）

**状态**：已知限制，暂缓处理  
**发现时间**：2026-05

**描述**  
当前同步逻辑对已存在的零件每次都执行 checkout → update_iteration → checkin，
即使 PLM 中的属性与 CATIA 完全一致也不跳过。

增量检测方案需要先 GET 当前迭代属性与 CATIA 值做对比，再决定是否 checkout。
在端到端联调尚未完整跑通之前，增量逻辑会增加排查难度，故暂缓。

**影响**  
大型 BOM（100+ 节点）每次全量同步会产生大量不必要的 checkout/checkin 操作，
可能触发 PLM 服务端的并发锁或审计日志膨胀。

**待确认**
- PLM 服务端对频繁 checkout/checkin 是否有速率限制或并发限制
- 是否有 PATCH 接口可绕过 checkout 流程直接更新属性（部分 PLM 版本支持）

---

### PLM-03：子组件引用版本与最新版本不同步

**状态**：已修复（客户端侧），PLM 端行为待验证  
**发现时间**：2026-05  
**修复版本**：待发布

**描述**  
原实现中，`child_components` 使用 `create_part` 返回的版本（通常为首次创建的版本），
多次同步后若零件产生了新版本，父级的子组件列表不会更新到最新版本。

客户端修复方案：在后序遍历中，子节点同步完成后调用 `_get_latest_version()` 
取最新版本，再写入 `child_components`。

**待确认**
- DocdokuPLM 的 `update_iteration` 接口：`components` 字段中的版本号
  是否必须精确匹配，还是会自动解析为最新版本
- 若零件在 PLM 中存在多个版本（A、B、C...），父级引用旧版本是否会导致
  BOM 结构视图展示错误版本

---

### PLM-05：`POST /part-templates` 返回 500 NullPointerException

**发现时间**：2026-05  
**端点**：`POST /workspaces/{ws}/part-templates`  
**现象**：调用创建零件模板接口时，服务端抛出：
```
Unhandled system error: PartTemplateResource.createPartMasterTemplate
threw java.lang.NullPointerException in PartTemplateResource.java at line 166
```
**影响**：无法使用零件模板，同步时所有零件以无模板方式创建。  
**客户端处理**：捕获 500 错误，打印警告后继续同步，不影响零件创建/更新主流程。  
**待跟进**：需 PLM 管理员检查 `PartTemplateResource.java:166` 处空指针原因（可能是工作区配置缺失某必填字段）。

---

### PLM-04：checkout 被其他用户锁定时无服务端通知机制

**状态**：客户端已改进（用户名显示）  
**发现时间**：2026-05  
**改进版本**：feat/plm-sync-improvements-v3

**描述**  
当某个零件已被其他用户 checkout（锁定），服务端返回 403/400，
当前客户端从 `GET /parts/{pn}-{ver}` 的 `checkOutUser` 字段读取锁定者用户名，
并在同步日志表格中以橙色显示（如 `跳过-被@admin`），同时在 errors 汇总中附上锁定者名称。

**待确认**
- `PUT /parts/{pn}-{ver}/checkout` 的 403 响应体结构（当前通过独立 GET 查询，而非解析 checkout 失败响应）
- 是否有 `GET /parts/{pn}-{ver}/lock` 接口可提前查询锁定状态（可减少一次 GET 请求）

---

### PLM-06：`GET /parts/{pn}-{ver}` 在数据异常时抛 500 NullPointerException

**状态**：✅ 服务端已修复（2026-05-20），客户端防御性读取保留  
**发现时间**：2026-05  
**端点**：`GET /workspaces/{ws}/parts/{partNumber}-{version}`

**现象**  
~~请求含空格零件编号（如 `front wing final`）时~~（已更新，见下）  
集成测试发现：**所有** `GET /parts/{pn}-{ver}` 请求（包括零件/版本完全不存在的情况）
均触发此 NPE，返回 500。零件是否含空格不是必要条件。

服务端日志报：
```
NullPointerException at ProductManagerBean.java:3509/3511
isCheckoutByAnotherUser threw NullPointerException
```

**根因分析**（源码）  
`ProductManagerBean.java:3508` 的 `isCheckoutByAnotherUser` 方法缺少 null guard：
```java
// 有 bug 的代码
return partRevision.isCheckedOut()
    && !partRevision.getCheckOutUser().equals(user);  // getCheckOutUser() 可能为 null

// 正确写法
return partRevision.isCheckedOut()
    && partRevision.getCheckOutUser() != null
    && !partRevision.getCheckOutUser().equals(user);
```
数据库中存在 `isCheckedOut=true` 但 `checkOutUser=null` 的不一致记录时触发此 bug。
含空格的零件名本身不是直接原因，但 URL 路径切割歧义可能命中这类异常记录。

**影响**  
受影响的零件在同步时被跳过并计入 `result.failed`，同步日志显示
`"PLM 服务端内部错误(500)，跳过此零件"`。

**客户端处理**（已实施）
- `_sync_node`：HTTP 500 单独提示，与其他查询失败区分
- `_get_checkout_owner`：`(result.get("checkOutUser") or {}).get("login")` 防御性读取（保留，参见 REST-API-Notes.md 建议）
- `_get_latest_version`：服务端修复前曾对 500+NPE 执行 `continue`（视为不存在），已于修复后移除，现 500 直接 raise

**待跟进**  
~~PLM 管理员需修复 `ProductManagerBean.java:3508` 添加 null guard~~（已修复）

---

### PLM-07：undocheckout 存在两个限制

**状态**：已确认，客户端已适配  
**发现时间**：2026-05  
**端点**：`PUT /workspaces/{ws}/parts/{pn}-{ver}/undocheckout`

**限制 1：不支持撤销他人签出**  
Admin 账号对他人已 checkout 的零件调用 undocheckout，服务端返回：
```
HTTP 400: 无法撤销……非自己签出的目录
```
即使是 admin 也无法强制撤销，`FORCE_UNDO` 策略在当前配置下无效。

**限制 2：第一次迭代（iter=1）无法 undocheckout**  
新建零件（iter=1，从未 checkin 过）调用 undocheckout 返回：
```
HTTP 400: 该操作已经一被执行
```
必须使用 **checkin** 来释放 checkout 状态。

**影响**  
- `FORCE_UNDO` 策略退化为 SKIP（他人 checkout 的零件只能跳过）
- 客户端 cleanup 逻辑必须用 checkin 而非 undocheckout 来释放锁

**待跟进**  
需 PLM 管理员确认是否有专用的"管理员强制撤销"端点或角色配置。

---

## 已解决的 PLM 端问题（归档）

### 已解决-01：localhost 解析为 IPv6 导致连接超时 21 秒

**解决时间**：2026-05  
**现象**：Windows 将 `localhost` 优先解析为 `::1`（IPv6），
Payara 5.194 仅监听 IPv4，导致每次 TCP 连接等待超时后才回落到 `127.0.0.1`，
每个零件请求耗时 63 秒以上。  
**解决方案**：PLM 服务地址统一使用 `127.0.0.1` 而非 `localhost`。

### 已解决-03：Python urllib 默认 User-Agent 被 Cloudflare Bot Protection 拦截（403）

**解决时间**：2026-05-21  
**现象**：使用公网地址（经过 Cloudflare Tunnel/CDN）访问 PLM 时，
`urllib` 发出的所有请求（包括 `/auth/login`）均返回 `HTTP 403 Forbidden`，
而用 `requests` 库或浏览器访问同一地址完全正常。

**根因**  
Python `urllib` 的默认 User-Agent 为 `Python-urllib/3.x`，
Cloudflare Bot Protection（"Browser Integrity Check"）识别此 UA 为自动化脚本并直接拒绝。  
`requests` 库默认 UA 为 `python-requests/2.x.x`，Cloudflare 对此放行。

**复现路径**
```python
# 403（被 Cloudflare 拦截）
urllib.request.urlopen(req)  # UA: Python-urllib/3.14

# 200（正常）
requests.post(url, ...)      # UA: python-requests/2.31.0
```

**修复**（`api_client.py` `_headers()` 方法，commit `7b86a16`）  
在所有请求的基础头中固定设置：
```python
"User-Agent": "Mozilla/5.0 (compatible; CATIACopilot/1.0)"
```
`_headers()` 是唯一构造请求头的入口，`_request()` 和 `upload_step()` 均通过它获取头，
因此一处修改覆盖全部端点，无需逐一修改。

**注意**  
内网直连（不经过 Cloudflare）时，此 UA 设置无副作用，对 Payara 服务端完全透明。

---

### 已解决-02：JWT token 在响应头而非响应体

**解决时间**：2026-05  
**现象**：DocdokuPLM Payara 版本将 JWT 放在响应头 `jwt:` 字段，
而非响应体的 `{"jwt": "..."}` 中，导致登录后所有请求被当作未认证（401）。  
**解决方案**：登录逻辑优先读取响应头 `jwt` 字段，再逐级回落到响应体、
`Authorization` 头、Cookie、Basic Auth。
