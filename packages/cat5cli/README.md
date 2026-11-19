# cat5cli

Windows-native CATIA V5 tools for humans and AI agents. This npm package is a thin installer and launcher; the business runtime is a separate Nuitka standalone ZIP and does not require system Python.

Current preview: **0.1.0-beta.2**. The CLI has its own version, independent of the CATIA Copilot GUI.

## Requirements

- Windows x64; Node.js 22+ and npm. Node.js 24 LTS is recommended.
- Windows PowerShell with Expand-Archive; PowerShell 7 is not required.
- CATIA V5 with a valid license for CAD operations, running under the same interactive Windows user. Installation, help and schema do not need CATIA.
- Online installation downloads from npm and GitHub Releases; local TGZ and ZIP installation supports offline distribution.

## Install

Install the preview:

```powershell
npx @raydutchman/cat5cli@beta install
```

Alternatively, install the launcher globally and explicitly install the runtime:

```powershell
npm install -g @raydutchman/cat5cli@beta
cat5cli install
```

There is no postinstall script. npm installation alone installs the launcher, not the standalone runtime. npx is another way to run the same npm package, not a separate release channel.

For offline installation, use local artifacts:

```powershell
npx --yes --package .\raydutchman-cat5cli-0.1.0-beta.2.tgz cat5cli install --from-zip .\cat5cli-0.1.0-beta.2-windows-x64.zip --sha256 <HASH> --skills-dir "$env:USERPROFILE/.config/opencode/skills"
```

From this repository:

```powershell
node packages/cat5cli/bin/cat5cli.cjs install --from-zip dist/cat5cli/cat5cli-0.1.0-beta.2-windows-x64.zip --skills-dir "$env:USERPROFILE/.config/opencode/skills"
```

The fixed runtime location is `%LOCALAPPDATA%/Cat5cli/runtime/cat5cli.exe`. `CAT5CLI_HOME` overrides the installation root; `CAT5CLI_TASKS_DIR` overrides task storage. Installation updates the user PATH without duplicating entries. Reopen existing terminals/agents afterward. `--no-path` leaves PATH unchanged for isolated tests.

Repeated installation replaces runtime in place and preserves task records. Finish all active operations before replacement. Use the npm `cat5cli.cmd` shim explicitly if an existing runtime EXE takes precedence in PATH and you need the install subcommand.

## Skills

`install` copies the bundled `skills/cat5cli` directory, including SKILL.md and references, to `~/.agents/skills/cat5cli` by default. For OpenCode, pass `--skills-dir ~/.config/opencode/skills`. Hosts must load the selected directory; a running session may require refresh or restart.

The matching Skill can be installed separately from the release tag:

```powershell
npx skills add https://github.com/RayDutchman/CATIA-Copilot/tree/cat5cli-v0.1.0-beta.2/skills/cat5cli --skill cat5cli -g -a opencode
```

The separate Skills CLI currently requires Node.js 22.20+. Company-specific BOM rules belong in a separate private Skill. The public Skill describes CLI usage and supports the 0.1.x preview contract; consult the installed command schema for exact capabilities.

## Release artifacts

GitHub tag: `cat5cli-v<VERSION>`. Assets: `cat5cli-<VERSION>-windows-x64.zip` and `SHA256SUMS`. Online installs require matching checksums; local installs accept `--sha256`.

Run `python scripts/build_cat5cli.py --no-install` to build release artifacts without touching the local installation. Omit `--no-install` to install after building. Build intermediates are cleaned after successful packaging.

## Uninstall and legacy migration

`npm uninstall -g @raydutchman/cat5cli` removes only the npm launcher. Complete removal also requires deleting the actual Cat5cli installation and its Skill directory and removing only its runtime entry from user PATH, after all workers finish. npx keeps its package in the normal npm cache.

The old Catkit installation is not automatically deleted. Before migration, finish old tasks, preserve required task evidence, install and verify cat5cli, then remove the old runtime, old Skill copies and its PATH entry. Do not run old and new clients concurrently against CATIA.

The worker retains the standard interactive scheduled-task fallback when Windows Job breakaway is denied; a brief console window may appear. No custom hidden launcher is added.
