#!/usr/bin/env node
const { spawnSync } = require('node:child_process');
const { install, selectedExecutable } = require('./install.cjs');

async function main() {
  const args = process.argv.slice(2);
  if (args[0] === 'install') {
    if (args.includes('--help')) {
      console.log('cat5cli install [--from-zip FILE] [--sha256 HASH] [--skills-dir DIR] [--version VERSION]');
      return;
    }
    await install(args.slice(1));
    return;
  }
  if (process.platform !== 'win32') throw new Error('Run Cat5cli on Windows.');
  const result = spawnSync(selectedExecutable(), args, { stdio: 'inherit', windowsHide: false });
  if (result.error) throw result.error;
  process.exitCode = result.status === null ? 1 : result.status;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
