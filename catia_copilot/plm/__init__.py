"""
catia_copilot.plm — PDM/PLM 集成包。
支持多后端（DocdokuPLM / myPDM / plm-unified）。

子模块：
  api_client         DocdokuPLM REST API 客户端（纯 urllib，无第三方依赖）
  my_pdm_api_client  myPDM REST API 客户端（JWT 认证，纯 urllib）
  my_pdm_schemas     myPDM API 数据模型（dataclass）
  unified_client     plm-unified FastAPI 客户端（drop-in 替换 api_client）
  sync               CATIA BOM → PLM 同步逻辑
  workspace_scanner  本地工作区扫描 + PLM 缓存管理
"""
