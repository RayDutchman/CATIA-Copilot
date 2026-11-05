"""Build and package the native Windows cat5cli standalone runtime."""
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
    tree = ast.parse((ROOT / 'catia_copilot/cli/version.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CLI_VERSION' for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError('CLI_VERSION was not found')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/cat5cli')
    parser.add_argument('--dry-run', action='store_true', help='Print the build command without executing it')
    parser.add_argument('--no-install', action='store_true', help='Build release artifacts without installing locally')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Build using native Windows Python')
    version = app_version()
    output = args.output.resolve()
    version_file = ROOT / 'catia_copilot/cli/_build_version.json'
    command = [sys.executable, '-m', 'nuitka', '--mode=standalone', '--windows-console-mode=force',
               '--output-filename=cat5cli.exe', '--output-dir='+str(output),
               '--include-module=comtypes.persist', '--include-module=comtypes.typeinfo',
               '--include-module=comtypes.automation', '--include-module=comtypes.stream',
               '--include-package=comtypes.gen',
               '--nofollow-import-to=PySide6,PyQt5,nuitka,pytest,win32com.test,win32com.demos,comtypes.test',
               '--include-data-files='+str(version_file)+'=catia_copilot/cli/_build_version.json',
               '--assume-yes-for-downloads', str(ROOT / 'cat5cli.py')]
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False, indent=2))
        return 0
    output.mkdir(parents=True, exist_ok=True)
    # pywinauto 导入 UIA 时会动态加载 comtypes 命名空间和类型库包装。
    # 只强制纳入动态命名空间及生成包装；其余模块按实际导入收集。
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
    distribution = output / 'cat5cli.dist'
    subprocess.run([str(distribution / 'cat5cli.exe'), '--help'], check=True)
    subprocess.run([str(distribution / 'cat5cli.exe'), '--version'], check=True)
    subprocess.run([str(distribution / 'cat5cli.exe'), 'schema', 'document', 'save'], check=True)
    subprocess.run([str(distribution / 'cat5cli.exe'), 'schema', 'status', '--diagnose'], check=True)
    invalid = subprocess.run([str(distribution / 'cat5cli.exe'), 'schema', 'not-a-command'], capture_output=True, text=True, encoding='utf-8')
    if invalid.returncode != 2:
        raise RuntimeError('An unknown schema path must return exit code 2')
    if not json.loads(invalid.stdout).get('error'):
        raise RuntimeError('An unknown schema path must return a JSON error')
    shutil.copytree(ROOT / 'skills/cat5cli', distribution / 'skills/cat5cli', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'LICENSE', distribution / 'LICENSE')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', distribution / 'THIRD_PARTY_NOTICES.md')
    archive = Path(shutil.make_archive(str(output / f'cat5cli-{version}-windows-x64'), 'zip', distribution))
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    # 构建验证通过后覆盖正式安装，避免散落多套可执行程序。
    if not args.no_install:
        subprocess.run(['node', str(ROOT / 'packages/cat5cli/bin/cat5cli.cjs'), 'install',
                        '--from-zip', str(archive), '--sha256', digest], cwd=ROOT, check=True)
    for old_archive in output.glob('cat5cli-*-windows-x64.zip'):
        if old_archive != archive:
            old_archive.unlink()
    for name in ('cat5cli.build', 'cat5cli.dist', 'cat5cli.onefile-build'):
        if (output / name).exists():
            shutil.rmtree(output / name)
    print(archive)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
