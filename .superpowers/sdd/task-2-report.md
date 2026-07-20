## Task 2 完成报告

**任务**：创建 myPDM API 客户端 `catia_copilot/plm/my_pdm_api_client.py`

**状态**：✅ 完成

**提交**：
- 新建 `catia_copilot/plm/my_pdm_api_client.py`

**验证结果**：
```
client created OK
has_permission: False
```
符合任务说明中的预期输出。

**实现要点**：
- 仅使用 urllib 标准库，无第三方 HTTP 依赖
- JWT access_token 内存保存，refresh_token 通过 QSettings 持久化
- 401 自动 refresh 重试机制
- 完整覆盖认证、零件 CRUD、签出/签入、BOM 匹配/同步、附件上传、权限检查

**关注事项**：无
