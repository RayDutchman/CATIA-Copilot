"""
catia_copilot.plm — PLM 集成包。

子模块：
  api_client        DocdokuPLM REST API 客户端（纯 urllib，无第三方依赖）
  unified_client    plm-unified FastAPI 客户端（drop-in 替换 api_client）
  sync              CATIA BOM → PLM 同步逻辑
  workspace_scanner 本地工作区扫描 + PLM 缓存管理
"""
