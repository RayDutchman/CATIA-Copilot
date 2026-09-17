"""包级冒烟测试：验证核心常量与资源路径解析的真实行为。"""
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catia_copilot.constants import APP_NAME, APP_VERSION  # noqa: E402
from catia_copilot.utils import resource_path  # noqa: E402


class PackageSmokeTest(unittest.TestCase):
    def test_app_name_is_catia_copilot(self) -> None:
        # 应用名称同时用于 QSettings 分组，改动会导致既有配置丢失
        self.assertEqual(APP_NAME, "CATIA Copilot")

    def test_app_version_is_two_two(self) -> None:
        self.assertEqual(APP_VERSION, "2.2.0")

    def test_resource_path_resolves_project_file(self) -> None:
        # 资源定位必须命中源码树内的真实文件（当前非打包模式为项目根）
        self.assertTrue(Path(resource_path("main.py")).is_file())

    def test_resource_path_root_contains_package(self) -> None:
        # 资源根应包含 catia_copilot 包（i18n 翻译文件放置目录）
        self.assertTrue(Path(resource_path("catia_copilot/i18n.py")).is_file())


if __name__ == "__main__":
    unittest.main()