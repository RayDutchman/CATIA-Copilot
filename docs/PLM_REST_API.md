## 1. 搜索零件

### 1.1 全文搜索零件 Revision
- **URL**: `GET /workspaces/{workspaceId}/parts/search`
- **HTTP 方法**: `GET`
- **查询参数**:
  | 参数名 | 类型 | 说明 |
  |--------|------|------|
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
  | `fetchHeadOnly` | boolean | 仅获取最新版本 |a

### 1.2 运行自定义查询（含复杂规则）
- **URL**: `POST /workspaces/{workspaceId}/parts/queries`
- **HTTP 方法**: `POST`
- **请求体**: `QueryDTO`（嵌套 `QueryRuleDTO`，支持字段：`pm.number`、`pm.name`、`pm.author` 等，运算符：`equal`、`begins_with`，逻辑条件：`AND`/`OR`）
- **查询参数**: `save`（boolean，是否保存）、`export`（string，导出格式如 `JSON`/`XLS`）

### 1.3 获取已保存的查询列表
- **URL**: `GET /workspaces/{workspaceId}/parts/queries`
- **HTTP 方法**: `GET`

### 1.4 删除已保存的查询
- **URL**: `DELETE /workspaces/{workspaceId}/parts/queries/{queryId}`
- **HTTP 方法**: `DELETE`

### 1.5 导出已保存的查询结果
- **URL**: `GET /workspaces/{workspaceId}/parts/queries/{queryId}/format/{export}`
- **HTTP 方法**: `GET`
- **路径参数**: `export`（如 `XLS`）

---

## 2. 下载附件（attachedfiles）

### 2.1 上传附件
- **URL**: `POST /files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/attachedfiles`
- **HTTP 方法**: `POST`
- **Content-Type**: `multipart/form-data`
- **表单字段**: `upload`（File）

### 2.2 下载附件（通过 subType）
- **URL**: `GET /files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/{subType}/{fileName}`
- **HTTP 方法**: `GET`
- **路径参数**: `subType = "attachedfiles"`，`fileName` 为文件名
- **可选查询参数**: `type`、`output`、`uuid`、`token`
- **可选 Header**: `Range`、`password`
- **源码调用示例**: `partBinaryApi.downloadPartFile(wsId, pn, ver, iter, "attachedfiles", fileName, null, null, null, null, null, null)`

### 2.3 下载附件（无子类型，直接按文件名）
- **URL**: `GET /files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/{fileName}`
- **HTTP 方法**: `GET`

---

## 3. 下载 CAD 文件（nativecad）

### 3.1 上传 Native CAD 文件
- **URL**: `POST /files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/nativecad`
- **HTTP 方法**: `POST`
- **Content-Type**: `multipart/form-data`
- **表单字段**: `upload`（File）

#### 另见 rest-api.md 中的另一种路径格式：
- **URL**: `PUT /api/workspaces/{workspaceId}/parts/{partNumber}/versions/{version}/iterations/{iteration}/nativecad`
- **HTTP 方法**: `PUT`
- **Content-Type**: `multipart/form-data`

### 3.2 下载 Native CAD 文件
- **URL**: `GET /files/{workspaceId}/parts/{partNumber}/{version}/{iteration}/{subType}/{fileName}`
- **HTTP 方法**: `GET`
- **路径参数**: `subType = "nativecad"`，`fileName` 为文件名
- **源码调用示例**: `partBinaryApi.downloadPartFile(wsId, pn, ver, iter, "nativecad", fileName, null, null, null, null, null, null)`

---

## 4. BOM / 子装配结构

### 4.1 获取产品 BOM 树（核心接口）
- **URL**: `GET /workspaces/{workspaceId}/products/{ciId}/bom`
- **HTTP 方法**: `GET`
- **查询参数**:
  | 参数名 | 类型 | 说明 |
  |--------|------|------|
  | `configSpec` | string | 配置规格（如 `latest`、`wip`） |
  | `path` | string | 路径（用于获取子树） |
  | `diverge` | boolean | 是否展开替换件 |
- **返回**: `ComponentDTO`（嵌套 `components` 数组，含 `assembly` 标识、`number`、`path` 等）

### 4.2 过滤产品结构（基于路径）
- **URL**: `GET /workspaces/{workspaceId}/products/{ciId}/filter`
- **HTTP 方法**: `GET`
- **查询参数**: `configSpec`、`path`、`diverge`
- **返回**: `List<PartRevisionDTO>`
- **测试调用示例**: `productsApi.filterProductStructure(wsId, productId, "wip", "-1", -1, null, false)`

### 4.3 获取装配体实例（3D 渲染用，含全局 4×4 矩阵）
- **URL**: `GET /workspaces/{workspaceId}/products/{ciId}/instances`
- **HTTP 方法**: `GET`
- **查询参数**: `configSpec`、`path`、`timestamp`、`diverge`
- 也支持 POST 多路径查询

### 4.4 获取零件参与的所有基线
- **URL**: `GET /workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/baselines`
- **HTTP 方法**: `GET`

### 4.5 获取哪里使用了该零件（作为组件）
- **URL**: `GET /workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/used-by-as-component`
- **HTTP 方法**: `GET`

### 4.6 获取哪里使用了该零件（作为替换件）
- **URL**: `GET /workspaces/{workspaceId}/parts/{partNumber}-{partVersion}/used-by-as-substitute`
- **HTTP 方法**: `GET`

### 4.7 基线创建路径选择
- **URL**: `GET /workspaces/{workspaceId}/products/{ciId}/path-choices`
- **HTTP 方法**: `GET`
- **查询参数**: `configSpec`（如 `LATEST`）
- **调用示例**: `productsApi.getBaselineCreationPathChoices(wsId, productId, "LATEST")`