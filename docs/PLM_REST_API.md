# DocdokuPLM REST API 参考（CATIA-Copilot 用）

## 1. 认证与会话

### 1.1 登录

```
POST /api/auth/login
Content-Type: application/json

{ "login": "admin", "password": "xxx" }
```

**成功响应 200**：
```json
{
  "login": "admin",
  "name": "John Doe",
  "email": "admin@example.com",
  "language": "en",
  "timeZone": "UTC",
  "admin": true,
  "enabled": true,
  "providerId": null
}
```

响应 Header 返回 `JWT: <token>`，后续请求携带 `Authorization: Bearer <token>`。

> `password`、`newPassword` 字段存在于 DTO 但序列化时为 null，响应中不携带密码。

**关键字段**：

| 字段 | 类型 | 含义 |
|------|------|------|
| `login` | string | 登录名，唯一标识 |
| `admin` | boolean | 是否为管理员 |
| `enabled` | boolean | 账号是否启用 |
| `providerId` | integer/null | OAuth 提供方 ID，本地账号为 null |

**失败情形**：

| HTTP 状态 | 原因 |
|-----------|------|
| 401 | 账号不存在或密码错误 |
| 403 | 账号已被禁用（`enabled=false`） |

### 1.2 登出

```
GET /api/auth/logout
```

返回 200，清除服务端 session（若有）。JWT 为无状态令牌，客户端需自行丢弃。

### 1.3 OAuth 提供方列表

```
GET /api/auth/providers
```

未配置时返回 `[]`，配置时：
```json
[{
  "id": 1,
  "name": "Google",
  "authority": "https://accounts.google.com/o/oauth2/auth",
  "scope": "openid email profile"
}]
```

---

## 2. 工作空间

### 2.1 获取当前用户工作空间列表

```
GET /api/workspaces
```

**返回**：
```json
{
  "administratedWorkspaces": [
    { "id": "Workspace_0", "description": "主工作空间", "folderLocked": false, "enabled": true }
  ],
  "allWorkspaces": [
    { "id": "Workspace_0", "description": "主工作空间", "folderLocked": false, "enabled": true }
  ]
}
```

- `administratedWorkspaces` — 当前用户作为管理员的工作空间
- `allWorkspaces` — 当前用户有权访问的全部工作空间

**WorkspaceDTO 字段**：

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | string | 工作空间 ID，对应 `{workspaceId}` 路径参数 |
| `description` | string | 描述 |
| `folderLocked` | boolean | 文件夹是否锁定（禁止新建子文件夹） |
| `enabled` | boolean | 工作空间是否启用 |

### 2.2 工作空间详情

```
GET /api/workspaces/{workspaceId}/details
```

**返回**：
```json
[{
  "id": "Workspace_0",
  "admin": "admin",
  "description": "主工作空间"
}]
```

### 2.3 创建工作空间

```
POST /api/workspaces
```

请求体与响应体均为 `WorkspaceDTO`。

**失败情形**：

| HTTP 状态 | 原因 |
|-----------|------|
| 403 | 非 admin 账号无权创建 |
| 409 | 工作空间 ID 已存在 |

---

## 3. 零件管理

### 3.1 搜索零件

#### 全文搜索

```
GET /workspaces/{workspaceId}/parts/search
```

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `q` | string | 通用搜索关键词 |
| `number` | string | 按编号搜索 |
| `name` | string | 按名称搜索 |
| `version` | string | 按版本搜索 |
| `author` | string | 按作者搜索 |
| `type` | string | 按类型搜索 |
| `createdFrom` | string | 创建时间起始 |
| `createdTo` | string | 创建时间结束 |
| `modifiedFrom` | string | 修改时间起始 |
| `modifiedTo` | string | 修改时间结束 |
| `tags` | string | 按标签搜索 |
| `content` | string | 按内容搜索 |
| `attributes` | string | 按属性搜索 |
| `from` | integer | 分页偏移 |
| `size` | integer | 分页大小 |
| `fetchHeadOnly` | boolean | 仅获取最新版本 |

#### 自定义查询

```
POST /workspaces/{workspaceId}/parts/queries
```

请求体为 `QueryDTO`（嵌套 `QueryRuleDTO`），支持字段：`pm.number`、`pm.name`、`pm.author` 等，运算符：`equal`、`begins_with`，逻辑条件：`AND`/`OR`。

查询参数：`save`（boolean，是否保存）、`export`（string，`JSON`/`XLS`）。

#### 已保存查询管理

| 操作 | 方法 | URL |
|------|------|-----|
| 列出已保存查询 | GET | `/workspaces/{workspaceId}/parts/queries` |
| 删除查询 | DELETE | `/workspaces/{workspaceId}/parts/queries/{queryId}` |
| 导出查询结果 | GET | `/workspaces/{workspaceId}/parts/queries/{queryId}/format/{export}` |

### 3.2 获取零件详情

```
GET /api/workspaces/{workspaceId}/parts/{partNumber}-{version}
```

**正常返回 200**：
```json
{
  "partKey": "PART-001-A",
  "number": "PART-001",
  "version": "A",
  "name": "零件名称",
  "lastIterationNumber": 1,
  "status": "WIP",
  "workspaceId": "Workspace_0",
  "standardPart": false,
  "publicShared": false,
  "attributesLocked": false,
  "checkOutUser": {
    "login": "admin",
    "name": "John Doe",
    "email": "admin@example.com",
    "workspaceId": "Workspace_0"
  },
  "checkOutDate": "2026-05-20T10:00:00Z",
  "author": { "login": "admin", "name": "John Doe" },
  "creationDate": "2026-05-01T00:00:00Z",
  "partIterations": [ "..." ],
  "acl": null,
  "workflow": null,
  "tags": [],
  "notifications": []
}
```

**关键字段说明**：

| 含义 | JSON key | 备注 |
|------|----------|------|
| 版本号 | `version` | 字符串，如 `"A"` |
| 最新迭代号 | `lastIterationNumber` | 整数 |
| 检出用户对象 | `checkOutUser` | 嵌套 UserDTO，未检出时为 `null` |
| 检出用户登录名 | `checkOutUser.login` | **不存在** `checkOutLogin` 顶级字段 |
| 检出时间 | `checkOutDate` | 未检出时为 `null` |
| 生命周期状态 | `status` | `WIP` / `RELEASED` / `OBSOLETE` |

> `PartRevisionDTO.java` 无 `@JsonbProperty` 自定义改名，JSON key 与 Java 字段名一致。

**不存在时**：HTTP 404（`EntityNotFoundException` → JAX-RS 异常映射器）。

**客户端防御性读取**：
```python
check_out_login = (data.get("checkOutUser") or {}).get("login")
```

### 3.3 创建零件

```
POST /api/workspaces/{workspaceId}/parts
Content-Type: application/json

{
  "number": "PART-001",
  "name": "零件名称"
}
```

- 创建后系统**自动 checkout**，`iteration = 1`，无需再调用 checkout
- 可选字段：`description`、`standardPart`、`templateId`、`workflowModelId`
- 必填字段仅 `number`

### 3.4 Checkout / Checkin

```
PUT /api/workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/checkout          ← checkout
PUT /api/workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/checkin          ← checkin
```

- Checkin 无请求体，返回更新后的 `PartRevisionDTO`

### 3.5 更新零件迭代（含 BOM 和装配位置）

```
PUT /api/workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/iterations/{partIteration}
```

**ANGLE 模式（欧拉角，弧度）**：
```json
{
  "iterationNote": "更新装配位置",
  "components": [{
    "component": { "number": "PART-001" },
    "amount": 1,
    "cadInstances": [{
      "tx": 10.0, "ty": 0.0, "tz": 5.0,
      "rx": 0.0, "ry": 1.5707963, "rz": 0.0,
      "rotationType": "ANGLE"
    }],
    "substitutes": []
  }]
}
```

**MATRIX 模式（3×3 旋转矩阵，行优先，适合 CATIA 导出）**：
```json
{
  "components": [{
    "component": { "number": "PART-002" },
    "cadInstances": [{
      "tx": 100.0, "ty": 50.0, "tz": 0.0,
      "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
      "rotationType": "MATRIX"
    }],
    "substitutes": []
  }]
}
```

**同一子零件多个实例（阵列）**：
```json
{
  "component": { "number": "BOLT-M8" },
  "cadInstances": [
    { "tx": 10.0, "ty": 0.0, "tz": 0.0, "rx": 0, "ry": 0, "rz": 0, "rotationType": "ANGLE" },
    { "tx": -10.0, "ty": 0.0, "tz": 0.0, "rx": 0, "ry": 0, "rz": 0, "rotationType": "ANGLE" }
  ]
}
```

---

## 4. 文档管理

### 获取文档详情

```
GET /api/workspaces/{workspaceId}/documents/{documentId}-{version}
```

**正常返回 200**：
```json
{
  "workspaceId": "Workspace_0",
  "id": "DOC-001",
  "documentMasterId": "DOC-001",
  "version": "A",
  "type": null,
  "author": { "login": "admin", "name": "John Doe" },
  "creationDate": "2026-05-01T00:00:00Z",
  "title": "文档标题",
  "description": "文档描述",
  "checkOutUser": null,
  "checkOutDate": null,
  "tags": [],
  "iterationSubscription": false,
  "stateSubscription": false,
  "documentIterations": [ "..." ],
  "workflow": null,
  "workflowId": null,
  "path": "/",
  "routePath": null,
  "lifeCycleState": null,
  "publicShared": false,
  "attributesLocked": false,
  "status": "WIP",
  "obsoleteDate": null,
  "obsoleteAuthor": null,
  "releaseDate": null,
  "releaseAuthor": null,
  "acl": null,
  "commentLink": null
}
```

**关键字段说明**：

| 含义 | JSON key | 备注 |
|------|----------|------|
| 文档 ID | `id` / `documentMasterId` | 两个字段值相同 |
| 版本号 | `version` | 字符串，如 `"A"` |
| 检出用户 | `checkOutUser` | 嵌套 UserDTO，未检出时为 `null` |
| 检出时间 | `checkOutDate` | 未检出时为 `null` |
| 生命周期状态 | `status` | `WIP` / `RELEASED` / `OBSOLETE` |
| 文档迭代列表 | `documentIterations` | 数组，最新迭代通过最后一项获取 |

> `checkOutUser` 和 `checkOutDate` 标注了 `@JsonbProperty(nillable = true)`，即使为 null 也会出现在 JSON 中，不会缺字段。
> 文档 DTO 有 `getLastIteration()` 但不直接序列化为顶级字段。

**不存在时**：HTTP 404。

**客户端防御性读取**：
```python
check_out_login = (data.get("checkOutUser") or {}).get("login")
last_iteration = (data.get("documentIterations") or [None])[-1]
```

---

## 5. 文件操作

### 5.1 上传 Native CAD 文件（触发 3D 转换）

```
PUT /api/workspaces/{workspaceId}/parts/{partNumber}/versions/{version}/iterations/{iteration}/nativecad
Content-Type: multipart/form-data
```

**支持格式**：`obj stl off ply 3ds wrl dae dxf lwo x ac cob scn ms3d stp step igs iges ifc`

**不支持**：`.CATPart` `.CATProduct`

> 另见另一种路径格式：`POST /files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/nativecad`

### 5.2 上传/下载附件

| 操作 | 方法 | URL |
|------|------|-----|
| 上传附件 | POST | `/files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/attachedfiles`（multipart，字段名 `upload`） |
| 下载附件 | GET | `/files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/attachedfiles/{fileName}`（可选参数：`type`、`output`、`uuid`、`token`；可选 Header：`Range`、`password`） |
| 下载附件（无子类型） | GET | `/files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/{fileName}` |

### 5.3 下载 Native CAD 文件

```
GET /files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/nativecad/{fileName}
```

---

## 6. BOM 与装配体

### 6.1 获取产品 BOM 树

```
GET /workspaces/{workspaceId}/products/{ciId}/bom
```

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `configSpec` | string | 配置规格（如 `latest`、`wip`） |
| `path` | string | 路径（用于获取子树） |
| `diverge` | boolean | 是否展开替换件 |

**返回**：嵌套 `ComponentDTO`（含 `assembly` 标识、`number`、`path` 等）。

### 6.2 过滤产品结构

```
GET /workspaces/{workspaceId}/products/{ciId}/filter
```

参数同上，返回 `List<PartRevisionDTO>`。

### 6.3 获取装配体实例（3D 渲染用）

```
GET /workspaces/{workspaceId}/products/{ciId}/instances
    ?configSpec=latest&path={partPath}&timestamp={ts}&diverge=false
```

服务端**递归装配树并累乘所有层级变换矩阵**，返回每个叶子零件的全局 4×4 世界坐标矩阵（16 个 double，行优先）：

```json
[{
  "id": "u1-1:u2-3",
  "partIterationId": "PART-001-A-1",
  "path": "u1-u2",
  "matrix": [1, 0, 0, 10.0, 0, 1, 0, 0.0, 0, 0, 1, 5.0, 0, 0, 0, 1.0],
  "qualities": 3,
  "xMin": -5.0, "yMin": -5.0, "zMin": -5.0,
  "xMax": 5.0, "yMax": 5.0, "zMax": 5.0,
  "files": [{ "fullName": "api/files/workspace/part/file.obj" }],
  "attributes": []
}]
```

> 前端 `InstancesManager.js` 接收后直接 `mesh.applyMatrix4(matrix)`，无需手动计算层级关系。

也支持 POST 多路径查询：

```
POST /workspaces/{workspaceId}/products/{ciId}/instances
Content-Type: application/json

{ "configSpec": "latest", "paths": ["path1", "path2"] }
```

### 6.4 零件引用查询

| 操作 | 方法 | URL |
|------|------|-----|
| 零件参与的所有基线 | GET | `/workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/baselines` |
| 被哪些零件作为组件引用 | GET | `/workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/used-by-as-component` |
| 被哪些零件作为替换件引用 | GET | `/workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/used-by-as-substitute` |

### 6.5 基线

| 操作 | 方法 | URL |
|------|------|-----|
| 基线创建路径选择 | GET | `/workspaces/{workspaceId}/products/{ciId}/path-choices?configSpec=LATEST` |

---

## 7. 装配体上传流程

### 7.1 数据模型

```
PartMaster（零件/装配体，同一实体）
  └── PartRevision（版本 A/B/C）
        └── PartIteration（迭代 1/2/3）
              └── PartUsageLink（BOM 行，一行 = 引用一个子件）
                    ├── component → PartMaster（被引用子件）
                    └── CADInstance × N（该子件的 N 个位置实例）
```

"零件"和"装配体"是同一个 `PartMaster` 实体，区别仅在于 `PartIteration.isAssembly()` 动态判断（`components` 是否非空）。数据库中不存在单独的"装配体"表。

### 7.2 时序约束（通用）

```
POST /api/auth/login           ← 登录
POST /parts                    ← 创建零件（自动 checkout, iter=1）
PUT .../nativecad              ← 上传 .stp（触发 Kafka 异步转换）
GET .../conversion             ← 轮询等待 pending=false
PUT .../checkin                ← 签入
```

上传 `.stp` 后异步转为 `.obj`，回调时再次检查零件是否 checkout：

```
上传 .stp →（Kafka 异步）→ 转换服务处理 → 回调 ConverterBean
                                               ↓
                                      再次检查 isCheckedOut()
                                      若已 checkin → 转换结果丢弃
```

源码逻辑（`ConverterBean.java:172`）：
```java
if(!partRevision.isCheckedOut()) {
    LOGGER.severe("Cannot proceed as the part is not checked out");
    productService.endConversion(partIterationKey, false);
    return;  // geometry 不保存
}
```

**规则**：同一零件的"上传 → 轮询 → checkin"三步必须**严格串行**。不同零件之间可以**并行**。

**错误场景**：
- 直接上传未 checkout → 抛 `NotAllowedException`，上传失败
- Checkout 后上传但 check-in 太快（转换尚未回调）→ 回调时 `isCheckedOut() == false`，geometry 被丢弃，`conversion.succeed = false`

### 7.3 转换状态查询

```
GET /workspaces/{workspaceId}/parts/{partNumber}-{version}/iterations/{iteration}/conversion
```

**返回**：
```json
{
  "pending": false,
  "succeed": true,
  "startDate": "2026-05-21T19:24:33.722Z",
  "endDate": "2026-05-21T19:24:34.310Z"
}
```

| pending | succeed | 含义 |
|---------|---------|------|
| true | — | 转换进行中，**不要 checkin** |
| false | true | 转换成功，可以 checkin |
| false | false | 转换失败，可 retry（发送重试请求，零件须仍处于 checkout） |

`succeed: false` 可能是转换失败或被丢弃（checkin 太快导致回调时零件已非 checkout 状态）。

前端"无转换"标签含义：数据库 `partiteration_geometry` 表无关联 `.obj` 文件记录。

### 7.4 重试转换

```
PUT /workspaces/{workspaceId}/parts/{partNumber}-{version}/iterations/{iteration}/conversion
```

重走完整 convertCADFileToOBJ 流程（重新发 Kafka 消息），零件必须仍处于 checkout 状态。

### 7.5 方式 A：每个零件独立 STP + 外部 BOM

**适用场景**：有外部数据（JSON/CSV/程序生成）描述装配层级和各子件位置。

**操作顺序（深度优先，叶子→根）**：
```
1. POST /api/auth/login                     登录，获取 JWT

对每个零件（从叶子到根）：
2. POST /parts                              创建零件（自动 checkout, iter=1）
3. [仅装配体] PUT .../iterations/1          写入 BOM + cadInstances（位置）
4. PUT /files/.../nativecad                 上传 .stp（触发异步转换）
5. 轮询 GET .../conversion                  等待 pending=false
   └─ 若 succeed=false → PUT .../conversion 重试，再轮询
6. PUT .../checkin                          签入
```

步骤 3 和 4 顺序无关，但步骤 4→5→6 必须严格串行。

**并行示例**：
```
线程1: leaf_A → 上传 → 等转换 → checkin
线程2: leaf_B → 上传 → 等转换 → checkin
线程3: leaf_C → 上传 → 等转换 → checkin
                    ↓（等所有叶子完成）
主线程: assy → 写BOM → 上传 → 等转换 → checkin
```

### 7.6 方式 B：syncAssembly 自动解析 BOM

**适用场景**：有完整装配体 STP 文件，内部包含子件层级和位置信息。

**操作顺序**：
```
1. POST /api/auth/login                     登录

2. 先上传所有叶子零件（不同零件之间可并行）：
   POST /parts → PUT nativecad → 轮询 → checkin
   ⚠️ 上传时的文件名必须与装配体 STP 内部引用的子件文件名完全一致（含大小写）

3. 创建装配体零件：POST /parts

4. PUT /files/.../nativecad  上传整个装配体 .stp
   → 转换服务解析子件层级和位置，回调 syncAssembly
   → syncAssembly 按文件名查 binaryresource 表匹配已存在的 PartMaster
   → 自动写入 BOM + CADInstance（覆盖旧结构）

5. 轮询 .../conversion
   succeed=true  → 所有子件均匹配成功
   succeed=false → 至少有一个子件文件名未匹配（检查大小写，查后端日志 WARNING）

6. PUT .../checkin
```

**syncAssembly 匹配逻辑**（`BinaryResourceDAO.java:157`）：
```sql
WHERE fullName LIKE '{workspaceId}/parts/%/nativecad/{cadFileName}'
```

严格按文件名匹配，大小写敏感，无通配符容错。匹配失败时静默跳过，仅打印 WARNING 日志，不中断流程也不报错。

### 7.7 两种方式对比

| 考量点 | 方式 A（独立 STP + 外部 BOM） | 方式 B（装配体 STP） |
|--------|-----------------------------|---------------------|
| BOM 控制 | 完全可控 | 依赖 STP 内部解析 |
| 位置数据 | 需外部提供 | 自动从 STP 提取 |
| 文件名约束 | 无 | 严格与 STP 内引用一致 |
| 多层嵌套 | 每层手动写 BOM | 转换服务递归处理（取决于实现） |
| 适用场景 | 有程序化 BOM 数据源 | 有完整装配体 STP 且文件名可控 |

---

## 8. 通用注意事项

### 8.1 URL 编码

路径模板：`@Path("{partNumber: [^/].*}-{partVersion:[A-Z]+}")`

`partNumber` 能匹配含空格的字符串，但 HTTP 路径中空格**必须** encode 为 `%20`（不是 `+`，`+` 只用于 query string）：

```python
import urllib.parse
encoded = urllib.parse.quote(part_number, safe='')
url = f"/api/workspaces/{workspace_id}/parts/{encoded}-{version}"
```

### 8.2 已知限制

- 无批量创建零件接口（需逐个 POST）
- 无批量查询转换状态接口（需逐个轮询）
- 创建零件时不支持直接指定 `components`，必须先创建再 PUT iterations
- `PartCreationDTO` 必填字段仅 `number`，其余均可省略
- 不支持 `.CATPart` / `.CATProduct` 直接上传转换（需导出为 STP/IGES 等通用格式）
- 创建零件后系统**自动 checkout**，`iteration = 1`，无需再单独调用 checkout

### 8.3 服务端已修复的 NPE 问题

> 以下为服务端 bug，但客户端了解有助于排查异常。

**isCheckoutByUser / isCheckoutByAnotherUser NPE**（`ProductManagerBean.java:3504–3510`）：

修复前：`partRevision.getCheckOutUser().equals(user)` — `checkOutUser` 为 null 时 NPE
修复后：`user.equals(partRevision.getCheckOutUser())` — `user` 来自登录上下文保证非 null

**客户端防御建议**（仍建议保留）：
```python
check_out_login = (data.get("checkOutUser") or {}).get("login")
```
