const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { checksum, installDirectory, selectedExecutable } = require('../bin/install.cjs');
const { appendPath, ensureUserPath } = require('../bin/user-path.cjs');

test('Windows PATH writer keeps environment and notification bindings consistent', () => {
  const vm = require('node:vm');
  let captured;
  const sandbox = { module: { exports: {} }, Buffer, process,
    require: name => name === 'node:child_process' ? { execFileSync: (exe, args, options) => {
      captured = { script: Buffer.from(args.at(-1), 'base64').toString('utf16le'), env: options.env };
      return '';
    } } : require(name) };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../bin/user-path.cjs'), 'utf8'), sandbox);
  sandbox.module.exports.windowsUserPath.write('C:\\Existing;C:\\Cat5cli\\runtime');
  const variable = captured.script.match(/SetValue\('Path',\$env:(\w+)/)[1];
  assert.equal(captured.env[variable], 'C:\\Existing;C:\\Cat5cli\\runtime');
  const namespace = captured.script.match(/Add-Type -Namespace (\w+)/)[1];
  assert.ok(captured.script.includes(`[${namespace}.EnvironmentNotice]::SendMessageTimeout`));
});

test('PATH append preserves entries and is case-insensitively idempotent', () => {
  const before = '%USERPROFILE%\\bin;C:\\Other Tools';
  const target = 'C:\\Cat5cli\\runtime';
  const after = appendPath(before, target);
  assert.equal(after, before + ';' + target);
  assert.equal(appendPath(after, 'c:/cat5cli/runtime/'), after);
  assert.equal(appendPath('', target), target);
});

test('existing variable-based PATH entry is preserved without duplication', () => {
  const before = '%LOCALAPPDATA%\\Cat5cli\\runtime;C:\\Tools';
  assert.equal(appendPath(before, 'C:\\Users\\Test\\AppData\\Local\\Cat5cli\\runtime',
    { LOCALAPPDATA: 'C:\\Users\\Test\\AppData\\Local' }), before);
});

test('user PATH update writes only when required', () => {
  let value = 'C:\\Tools';
  let writes = 0;
  const store = { read: () => value, write: next => { value = next; writes++; } };
  assert.equal(ensureUserPath('C:\\Cat5cli\\runtime', store).changed, true);
  assert.equal(ensureUserPath('C:\\Cat5cli\\runtime', store).changed, false);
  assert.equal(writes, 1);
});

test('install preserves executable and skill layout with spaces', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cat5cli 测试 '));
  try {
    const bundle = path.join(temp, 'bundle');
    fs.mkdirSync(path.join(bundle, 'skills', 'cat5cli'), { recursive: true });
    fs.writeFileSync(path.join(bundle, 'cat5cli.exe'), 'fixture');
    fs.writeFileSync(path.join(bundle, 'skills', 'cat5cli', 'SKILL.md'), 'fixture skill');
    const home = path.join(temp, 'installed');
    const skills = path.join(temp, 'agent skills');
    const result = installDirectory(bundle, home, skills, '2.2.0');
    assert.equal(fs.readFileSync(selectedExecutable(home), 'utf8'), 'fixture');
    assert.equal(fs.readFileSync(path.join(skills, 'cat5cli', 'SKILL.md'), 'utf8'), 'fixture skill');
    assert.equal(result.version, '2.2.0');
    const executable = selectedExecutable(home);
    fs.writeFileSync(path.join(path.dirname(executable), 'obsolete.dll'), 'old');
    fs.writeFileSync(path.join(bundle, 'cat5cli.exe'), 'updated');
    installDirectory(bundle, home, skills, '2.2.1');
    assert.equal(selectedExecutable(home), path.join(home, 'runtime', 'cat5cli.exe'));
    assert.equal(selectedExecutable(home), executable);
    assert.equal(fs.readFileSync(executable, 'utf8'), 'updated');
    assert.equal(fs.existsSync(path.join(home, 'runtime', 'obsolete.dll')), false);
    assert.deepEqual(fs.readdirSync(home).sort(), ['current.json', 'runtime']);
    assert.equal(checksum(path.join(bundle, 'cat5cli.exe')).length, 64);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('invalid bundle does not replace existing selection', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cat5cli-invalid-'));
  try {
    assert.throws(() => installDirectory(temp, path.join(temp, 'home'), path.join(temp, 'skills'), '2.2.0'));
    assert.equal(fs.existsSync(path.join(temp, 'home', 'current.json')), false);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('invalid bundle error is English', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cat5cli-invalid-language-'));
  try {
    assert.throws(() => installDirectory(temp, path.join(temp, 'home'), path.join(temp, 'skills'), '2.2.0'),
      /ZIP must contain cat5cli\.exe and skills\/cat5cli\/SKILL\.md/);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('Windows ZIP install works without touching user installation', { skip: process.platform !== 'win32' }, () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cat5cli zip test '));
  try {
    const bundle = path.join(temp, 'bundle');
    fs.mkdirSync(path.join(bundle, 'skills', 'cat5cli'), { recursive: true });
    fs.writeFileSync(path.join(bundle, 'cat5cli.exe'), 'test executable, not launched');
    fs.writeFileSync(path.join(bundle, 'skills', 'cat5cli', 'SKILL.md'), 'test skill');
    const zip = path.join(temp, 'archive.zip');
    const quote = value => "'" + value.replaceAll("'", "''") + "'";
    const packed = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      `Compress-Archive -Path ${quote(path.join(bundle, '*'))} -DestinationPath ${quote(zip)}`], { encoding: 'utf8' });
    assert.equal(packed.status, 0, packed.stderr);
    const home = path.join(temp, 'home');
    const installed = spawnSync(process.execPath, [path.resolve(__dirname, '../bin/cat5cli.cjs'), 'install',
      '--from-zip', zip, '--sha256', checksum(zip), '--skills-dir', path.join(temp, 'skills'), '--no-path'],
      { encoding: 'utf8', env: { ...process.env, CAT5CLI_HOME: home } });
    assert.equal(installed.status, 0, installed.stderr);
    assert.equal(JSON.parse(installed.stdout).installed, true);
    assert.ok(fs.existsSync(selectedExecutable(home)));
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});
