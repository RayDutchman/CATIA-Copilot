const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { checksum, installDirectory, selectedExecutable } = require('../bin/install.cjs');
const { appendPath, ensureUserPath } = require('../bin/user-path.cjs');

test('PATH append preserves entries and is case-insensitively idempotent', () => {
  const before = '%USERPROFILE%\\bin;C:\\Other Tools';
  const target = 'C:\\Catkit\\runtime';
  const after = appendPath(before, target);
  assert.equal(after, before + ';' + target);
  assert.equal(appendPath(after, 'c:/catkit/runtime/'), after);
  assert.equal(appendPath('', target), target);
});

test('existing variable-based PATH entry is preserved without duplication', () => {
  const before = '%LOCALAPPDATA%\\Catkit\\runtime;C:\\Tools';
  assert.equal(appendPath(before, 'C:\\Users\\Test\\AppData\\Local\\Catkit\\runtime',
    { LOCALAPPDATA: 'C:\\Users\\Test\\AppData\\Local' }), before);
});

test('user PATH update writes only when required', () => {
  let value = 'C:\\Tools';
  let writes = 0;
  const store = { read: () => value, write: next => { value = next; writes++; } };
  assert.equal(ensureUserPath('C:\\Catkit\\runtime', store).changed, true);
  assert.equal(ensureUserPath('C:\\Catkit\\runtime', store).changed, false);
  assert.equal(writes, 1);
});

test('install preserves executable and skill layout with spaces', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'catkit test '));
  try {
    const bundle = path.join(temp, 'bundle');
    fs.mkdirSync(path.join(bundle, 'skills', 'catkit'), { recursive: true });
    fs.writeFileSync(path.join(bundle, 'catkit.exe'), 'fixture');
    fs.writeFileSync(path.join(bundle, 'skills', 'catkit', 'SKILL.md'), 'fixture skill');
    const home = path.join(temp, 'installed');
    const skills = path.join(temp, 'agent skills');
    const result = installDirectory(bundle, home, skills, '2.2.0');
    assert.equal(fs.readFileSync(selectedExecutable(home), 'utf8'), 'fixture');
    assert.equal(fs.readFileSync(path.join(skills, 'catkit', 'SKILL.md'), 'utf8'), 'fixture skill');
    assert.equal(result.version, '2.2.0');
    const executable = selectedExecutable(home);
    fs.writeFileSync(path.join(path.dirname(executable), 'obsolete.dll'), 'old');
    fs.writeFileSync(path.join(bundle, 'catkit.exe'), 'updated');
    installDirectory(bundle, home, skills, '2.2.1');
    assert.equal(selectedExecutable(home), path.join(home, 'runtime', 'catkit.exe'));
    assert.equal(selectedExecutable(home), executable);
    assert.equal(fs.readFileSync(executable, 'utf8'), 'updated');
    assert.equal(fs.existsSync(path.join(home, 'runtime', 'obsolete.dll')), false);
    assert.deepEqual(fs.readdirSync(home).sort(), ['current.json', 'runtime']);
    assert.equal(checksum(path.join(bundle, 'catkit.exe')).length, 64);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('invalid bundle does not replace existing selection', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'catkit-invalid-'));
  try {
    assert.throws(() => installDirectory(temp, path.join(temp, 'home'), path.join(temp, 'skills'), '2.2.0'));
    assert.equal(fs.existsSync(path.join(temp, 'home', 'current.json')), false);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('invalid bundle error is English', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'catkit-invalid-language-'));
  try {
    assert.throws(() => installDirectory(temp, path.join(temp, 'home'), path.join(temp, 'skills'), '2.2.0'),
      /ZIP must contain catkit\.exe and skills\/catkit\/SKILL\.md/);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('Windows ZIP install works without touching user installation', { skip: process.platform !== 'win32' }, () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'catkit zip test '));
  try {
    const bundle = path.join(temp, 'bundle');
    fs.mkdirSync(path.join(bundle, 'skills', 'catkit'), { recursive: true });
    fs.writeFileSync(path.join(bundle, 'catkit.exe'), 'test executable, not launched');
    fs.writeFileSync(path.join(bundle, 'skills', 'catkit', 'SKILL.md'), 'test skill');
    const zip = path.join(temp, 'archive.zip');
    const quote = value => "'" + value.replaceAll("'", "''") + "'";
    const packed = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      `Compress-Archive -Path ${quote(path.join(bundle, '*'))} -DestinationPath ${quote(zip)}`], { encoding: 'utf8' });
    assert.equal(packed.status, 0, packed.stderr);
    const home = path.join(temp, 'home');
    const installed = spawnSync(process.execPath, [path.resolve(__dirname, '../bin/catkit.cjs'), 'install',
      '--from-zip', zip, '--sha256', checksum(zip), '--skills-dir', path.join(temp, 'skills'), '--no-path'],
      { encoding: 'utf8', env: { ...process.env, CATKIT_HOME: home } });
    assert.equal(installed.status, 0, installed.stderr);
    assert.equal(JSON.parse(installed.stdout).installed, true);
    assert.ok(fs.existsSync(selectedExecutable(home)));
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});
