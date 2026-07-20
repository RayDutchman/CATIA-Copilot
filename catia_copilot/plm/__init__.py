"""
catia_copilot.plm — PDM/PLM 集成包。
对接 myPDM 后端（JWT REST API）。

子模块：
  my_pdm_api_client  myPDM REST API 客户端（纯 urllib，无第三方依赖）
  my_pdm_schemas     myPDM API 数据模型（dataclass）
  sync               CATIA BOM → PDM 同步逻辑
"""
