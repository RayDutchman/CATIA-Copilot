const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { version } = require('../package.json');
const { ensureUserPath } = require('./user-path.cjs');

function installHome() {
  return process.env.CAT5CLI_HOME || path.join(process.env.LOCALAPPDATA || os.homedir(), 'Cat5cli');
}
function checksum(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}
function selectedExecutable(home = installHome()) {
  const current = JSON.parse(fs.readFileSync(path.join(home, 'current.json'), 'utf8'));
  const executable = path.join(home, current.directory, 'cat5cli.exe');
  if (!fs.existsSync(executable)) throw new Error('Cat5cli executable is missing. Run install again.');
  return executable;
}
function installDirectory(bundle, home, skillsDir, selectedVersion) {
  if (!fs.existsSync(path.join(bundle, 'cat5cli.exe')) || !fs.existsSync(path.join(bundle, 'skills', 'cat5cli', 'SKILL.md'))) {
    throw new Error('ZIP must contain cat5cli.exe and skills/cat5cli/SKILL.md');
  }
  // 固定运行目录；先检查文件占用，避免删除到一半才发现 EXE 正在运行。
  const directory = 'runtime';
  const target = path.join(home, directory);
  fs.mkdirSync(home, { recursive: true });
  if (fs.existsSync(target)) {
    for (const entry of fs.readdirSync(target, { recursive: true, withFileTypes: true })) {
      if (!entry.isFile()) continue;
      try {
        fs.closeSync(fs.openSync(path.join(entry.parentPath || entry.path, entry.name), 'r+'));
      } catch (error) {
        throw new Error(`Runtime file is locked or not writable. Close Cat5cli and try again: ${entry.name}`, { cause: error });
      }
    }
    fs.rmSync(target, { recursive: true });
  }
  fs.cpSync(bundle, target, { recursive: true });
  fs.mkdirSync(skillsDir, { recursive: true });
  fs.rmSync(path.join(skillsDir, 'cat5cli'), { recursive: true, force: true });
  fs.cpSync(path.join(target, 'skills', 'cat5cli'), path.join(skillsDir, 'cat5cli'), { recursive: true });
  const current = { version: selectedVersion, directory, skillsDir };
  const temporary = path.join(home, 'current.tmp.json');
  fs.writeFileSync(temporary, JSON.stringify(current, null, 2));
  fs.renameSync(temporary, path.join(home, 'current.json'));
  return current;
}
function parseOptions(args) {
  const options = {};
  for (let i = 0; i < args.length; i++) {
    const key = args[i];
    if (key === '--no-path') {
      options[key] = true;
      continue;
    }
    if (!['--from-zip', '--sha256', '--skills-dir', '--version'].includes(key) || !args[i + 1]) {
      throw new Error(`Unknown option or missing value: ${key}`);
    }
    options[key] = args[++i];
  }
  return options;
}
async function download(url, destination) {
  const response = await fetch(url, { signal: AbortSignal.timeout(120000) });
  if (!response.ok) throw new Error(`Download failed (${response.status}): ${url}`);
  fs.writeFileSync(destination, Buffer.from(await response.arrayBuffer()));
}
async function install(args) {
  if (process.platform !== 'win32') throw new Error('Cat5cli requires native Windows.');
  const options = parseOptions(args);
  const selectedVersion = options['--version'] || version;
  if (!/^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$/.test(selectedVersion)) throw new Error('Invalid version format');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cat5cli-install-'));
  try {
    let archive = options['--from-zip'] && path.resolve(options['--from-zip']);
    let expected = options['--sha256'];
    if (!archive) {
      const name = `cat5cli-${selectedVersion}-windows-x64.zip`;
      const base = `https://github.com/RayDutchman/CATIA-Copilot/releases/download/cat5cli-v${selectedVersion}`;
      archive = path.join(temp, name);
      await download(`${base}/${name}`, archive);
      const sums = path.join(temp, 'SHA256SUMS');
      await download(`${base}/SHA256SUMS`, sums);
      const line = fs.readFileSync(sums, 'utf8').split(/\r?\n/).find(line => line.trim().endsWith(`  ${name}`));
      if (!line) throw new Error('Release archive has no matching entry in SHA256SUMS');
      expected = line.split(/\s+/)[0];
    }
    if (expected && (!/^[a-fA-F0-9]{64}$/.test(expected) || checksum(archive) !== expected.toLowerCase())) {
      throw new Error('ZIP SHA-256 verification failed');
    }
    const extracted = path.join(temp, 'bundle');
    const quote = value => "'" + value.replaceAll("'", "''") + "'";
    // Node可能继承PowerShell 7模块路径；明确加载当前Windows PowerShell自带模块。
    execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      `$ErrorActionPreference='Stop'; Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Archive/Microsoft.PowerShell.Archive.psd1'); Expand-Archive -LiteralPath ${quote(archive)} -DestinationPath ${quote(extracted)}`], { stdio: 'inherit' });
    const skillsDir = options['--skills-dir'] ? path.resolve(options['--skills-dir']) : path.join(os.homedir(), '.agents', 'skills');
    const installed = installDirectory(extracted, installHome(), skillsDir, selectedVersion);
    const executable = selectedExecutable();
    const userPath = options['--no-path'] ? { skipped: true } : ensureUserPath(path.dirname(path.resolve(executable)));
    console.log(JSON.stringify({ installed: true, ...installed, executable, userPath,
      ...(options['--no-path'] ? {} : { notice: 'User PATH is configured. Reopen existing terminals or AI hosts to use cat5cli.' }) }));
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
}
module.exports = { install, installHome, installDirectory, selectedExecutable, checksum };
