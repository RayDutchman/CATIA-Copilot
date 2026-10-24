"""Windows 原生 Nuitka standalone 构建与 ZIP 打包。"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def app_version():
    tree = ast.parse((ROOT / 'catia_copilot/constants.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'APP_VERSION' for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError('APP_VERSION 未找到')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/catkit')
    parser.add_argument('--dry-run', action='store_true', help='只打印构建命令')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('请在 Windows 原生 Python 中构建')
    version = app_version()
    output = args.output.resolve()
    version_file = ROOT / 'catia_copilot/cli/_build_version.json'
    command = [sys.executable, '-m', 'nuitka', '--mode=standalone', '--windows-console-mode=force',
               '--output-filename=catkit.exe', '--output-dir='+str(output),
               '--include-package=pycatia', '--include-package=win32com', '--include-package=comtypes',
               '--nofollow-import-to=PySide6,PyQt5,nuitka,pytest,win32com.test,win32com.demos,comtypes.test',
               '--include-data-files='+str(version_file)+'=catia_copilot/cli/_build_version.json',
               '--assume-yes-for-downloads', str(ROOT / 'catkit.py')]
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False, indent=2))
        return 0
    output.mkdir(parents=True, exist_ok=True)
    # pywinauto 导入 UIA 时会动态加载 comtypes 命名空间和类型库包装。
    # 先在构建环境生成包装，再让 include-package 将它们一起编译。
    subprocess.run([sys.executable, '-c', 'import sys; sys.coinit_flags=2; import pywinauto'], cwd=ROOT, check=True)
    old_version = version_file.read_bytes() if version_file.exists() else None
    try:
        version_file.write_text(json.dumps({'version': version}), encoding='utf-8')
        subprocess.run(command, cwd=ROOT, check=True)
    finally:
        if old_version is None:
            version_file.unlink(missing_ok=True)
        else:
            version_file.write_bytes(old_version)
    distribution = output / 'catkit.dist'
    subprocess.run([str(distribution / 'catkit.exe'), '--help'], check=True)
    subprocess.run([str(distribution / 'catkit.exe'), '--version'], check=True)
    subprocess.run([str(distribution / 'catkit.exe'), 'schema', 'document', 'save'], check=True)
    subprocess.run([str(distribution / 'catkit.exe'), 'schema', 'status', '--diagnose'], check=True)
    invalid = subprocess.run([str(distribution / 'catkit.exe'), 'schema', 'not-a-command'], capture_output=True, text=True, encoding='utf-8')
    if invalid.returncode != 2:
        raise RuntimeError('未知 schema 路径应返回退出码 2')
    if not json.loads(invalid.stdout).get('error'):
        raise RuntimeError('未知 schema 路径应返回 JSON 错误')
    shutil.copytree(ROOT / 'skills/catkit', distribution / 'skills/catkit', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'LICENSE', distribution / 'LICENSE')
    archive = Path(shutil.make_archive(str(output / f'catkit-{version}-windows-x64'), 'zip', distribution))
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    # 构建验证通过后覆盖正式安装，避免散落多套可执行程序。
    subprocess.run(['node', str(ROOT / 'packages/catkit/bin/catkit.cjs'), 'install',
                    '--from-zip', str(archive), '--sha256', digest], cwd=ROOT, check=True)
    for old_archive in output.glob('catkit-*-windows-x64.zip'):
        if old_archive != archive:
            old_archive.unlink()
    for name in ('catkit.build', 'catkit.dist', 'catkit.onefile-build'):
        if (output / name).exists():
            shutil.rmtree(output / name)
    print(archive)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
