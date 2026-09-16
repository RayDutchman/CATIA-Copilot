# 开发环境搭建

本文档说明如何搭建 CATIA Copilot 的本地开发环境、运行测试及调试技巧。

---

## 前置要求

- **操作系统：** Windows 10 / 11（COM 接口和 CATIA 均依赖 Windows）
- **Python：** 3.10 或更高版本
- **CATIA V5**（可选，功能测试需要）
- **Git**

---

## 环境搭建

```bash
# 1. 克隆仓库
git clone https://github.com/RayDutchman/CATIA-Copilot.git
cd CATIA-Copilot

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装运行依赖
pip install -r requirements.txt

# 4. 安装开发依赖（测试框架等）
pip install pytest pytest-qt
```

---

## 运行应用

```bash
python main.py
```

首次运行时程序会自动清理 `%LOCALAPPDATA%\Temp\gen_py\` 早绑定缓存。

---

## 运行测试

```bash
# 运行全部测试
pytest

# 运行指定模块
pytest tests/test_plm_api_integration.py -v

# 查看覆盖率（需安装 pytest-cov）
pytest --cov=catia_copilot --cov-report=term-missing
```

测试目录结构：
```
tests/
├── test_plm_api_integration.py # PLM REST API 集成测试
└── ...
```

> **注意：** PLM 集成测试需要 PLM 服务器可访问。如无服务器，相关测试会自动跳过或失败。

---

## 打包为 Windows 可执行文件

正式发行使用 Nuitka + Inno Setup：

```powershell
.\build_nuitka_installer.ps1
```

输出目录：`..\CATIA-Copilot-dist-nuitka\`。

构建脚本会自动：
- 从 `constants.py` 读取 `APP_VERSION`
- 使用 Nuitka 生成 standalone 产物
- 使用 Inno Setup 生成版本化安装包
- 包含 `resources/`、`macros/`、`drawing_templates/`
- 不包含 `crack/` 和未完成的 `part_templates/`

---

## 版本号管理

版本号在以下位置维护，修改时需保持一致：

| 文件 | 位置 |
|------|------|
| `catia_copilot/constants.py` | `APP_VERSION = "x.y.z"` |
| `pyproject.toml` | `version = "x.y.z"` |
| `README.md` | 顶部版本徽章（文字） |
| `CHANGELOG.md` | 新增版本条目 |
| `setup.iss` | 由构建脚本通过预处理变量注入，无需手动同步 |

`build.spec` 自动从 `constants.py` 读取版本号，无需手动同步。

## GitHub Actions 发布

正式发布通过版本标签触发 `.github/workflows/release.yml`：

```powershell
git tag v2.3.0
git push origin v2.3.0
```

Workflow 会在 Windows runner 上执行 Nuitka 和 Inno Setup，生成安装包并发布到 GitHub Release。CATIA COM 和 PLM 连接仍需在本机安装包上验收。

---

## 代码组织规范

### 新增功能

1. 业务逻辑放在 `catia/` 或 `plm/` 下，不要混入 UI 层
2. UI 对话框继承 `QDialog`，通过信号/槽与业务层解耦
3. 新增属性列时，在 `constants.py` 中同步更新列定义（显示名、宽度、可隐藏标志）
4. 新增用户自定义属性时，更新 `PRESET_USER_REF_PROPERTIES`，并手动同步 VBA 宏中的属性名数组

### 主题支持

所有新对话框需要：
1. 在 `__init__` 中订阅 `theme_manager.theme_changed` 信号
2. 实现 `_apply_theme()` 方法处理无法由 QSS 覆盖的动态样式（如表格行着色）
3. 不要在代码中硬编码颜色值，使用 `theme_manager` 提供的调色板常量

### 日志

```python
import logging
logger = logging.getLogger(__name__)

logger.debug("详细调试信息")
logger.info("正常操作记录")
logger.warning("非致命警告")
logger.error("错误信息", exc_info=True)
```

---

## 调试技巧

### COM 连接问题

如果 CATIA 指示器显示橙色（连接异常）：
1. 菜单「帮助 → CATIA 连接诊断」查看详细报告
2. 手动删除 `%LOCALAPPDATA%\Temp\gen_py\` 目录后重启程序
3. 重启 CATIA

### VBA 宏调试

在 CATIA VBA IDE（Alt+F8 打开）中：
- 使用 `Debug.Print` 输出到「立即」窗口
- `DEBUG_MODE = True` 的宏支持 `MsgBox` 断点调试
- **注意：** CATIA R33 中任何 `MsgBox` 弹出后会清空当前 Selection，调试宏时需注意此副作用

### PySide6 界面调试

```bash
# 启用 Qt 调试输出
set QT_LOGGING_RULES=*.debug=true
python main.py
```

---

## 常见问题

参见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
