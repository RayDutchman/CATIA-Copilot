# 贡献指南

本文档说明如何向 CATIA Copilot 提交代码、报告问题及参与开发。

> 本项目仅供内部使用，贡献者限于项目团队成员。

---

## 开发流程

### 1. 领取任务

在开始开发前，请先在 Issues 中确认任务已分配给你，或与维护者沟通。

### 2. 创建功能分支

从 `main` 分支创建功能分支，命名规范：

```
feature/<简短描述>     # 新功能
fix/<简短描述>         # Bug 修复
refactor/<简短描述>    # 重构
docs/<简短描述>        # 文档更新
```

示例：
```bash
git checkout main
git pull
git checkout -b feature/add-description-column
```

### 3. 开发与提交

- 每次提交聚焦单一变更
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
feat: 新增 Description 列支持
fix: 修复中文路径下"打开路径"异常
refactor: 将 PLM 存在性判断改为 POST 探测
docs: 更新 ARCHITECTURE.md 模块说明
chore: 升级版本号至 1.8.0
```

### 4. 测试

提交前确保：
```bash
# 运行全部测试
pytest

# 确认无新增 Python 警告
python -W error main.py
```

### 5. 提交 Pull Request

- PR 标题与提交信息格式一致
- PR 描述中说明变更动机、影响范围及测试方法
- 至少获得一名维护者的 Code Review 后方可合并

---

## 代码规范

### Python

- 遵循 [PEP 8](https://peps.python.org/pep-0008/)
- 函数/变量/类名使用英文
- 代码注释使用中文
- 函数签名添加类型注解
- 公开函数/类添加 docstring（中文）

示例：
```python
def collect_bom(product, depth: int = 0) -> list[dict]:
    """
    递归采集 CATIA 产品树的 BOM 数据。

    Args:
        product: CATIA Product COM 对象
        depth: 当前递归深度（用于层级缩进）

    Returns:
        BOM 行列表，每行为包含属性键值对的字典
    """
    ...
```

### VBA 宏

- 函数/变量命名使用英文，注释使用中文
- 新增宏时同时更新 `.txt` 源码文件（可读副本）
- `.catvba` 文件是 OLE2 二进制，不要用文本编辑器直接编辑，需在 CATIA VBA IDE 中操作后重新导出
- `SelectElement3` 的 filter 参数必须使用 `Variant` 类型数组（非强类型 `String` 数组）

---

## 版本号更新

发布新版本时，按顺序更新以下文件：

1. `catia_copilot/constants.py` — `APP_VERSION`
2. `pyproject.toml` — `version`
3. `README.md` — 顶部版本号文字
4. `CHANGELOG.md` — 新增版本条目，遵循现有格式

---

## 不应提交的内容

- `.catvba` 文件的二进制变更（确认无功能变更时可提交；有变更时同步更新对应 `.txt` 文件）
- 包含真实 PLM 服务器地址、账号密码的配置文件
- `dist/`、`build/`、`__pycache__/`、`.venv/` 等构建产物（已在 `.gitignore` 中排除）
- 个人本地配置（`*.local.py`、`local_config.json` 等）

---

## 报告问题

提交 Issue 时请包含：
1. 问题的简短描述
2. 复现步骤
3. 期望行为与实际行为
4. 相关日志（菜单「帮助 → 日志窗口」中复制）
5. 环境信息（Python 版本、CATIA 版本、操作系统版本）
