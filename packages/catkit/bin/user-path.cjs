const path = require('node:path');
const { execFileSync } = require('node:child_process');

function appendPath(current, directory, environment = process.env) {
  const normalize = value => path.win32.normalize(value.trim().replace(/^"|"$/g, '')
    .replace(/%([^%]+)%/g, (original, name) => {
      const key = Object.keys(environment).find(key => key.toLowerCase() === name.toLowerCase());
      return key === undefined ? original : environment[key];
    })).replace(/[\\/]+$/, '').toLowerCase();
  if (current.split(';').some(entry => entry.trim() && normalize(entry) === normalize(directory))) return current;
  return current + (current && !current.endsWith(';') ? ';' : '') + directory;
}

function powershell(script, value) {
  const code = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); " + script;
  return execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand',
    Buffer.from(code, 'utf16le').toString('base64')], {
    encoding: 'utf8', timeout: 30000,
    env: { ...process.env, CATKIT_USER_PATH_VALUE: value || '' },
  }).trim();
}

const windowsUserPath = {
  read() {
    // 原样读取变量表达式，避免展开后覆盖用户已有的 %变量% 路径。
    return JSON.parse(powershell(`
      $key=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment');
      try {
        $value=if($key){$key.GetValue('Path','',[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)}else{''};
        ConvertTo-Json -InputObject ([string]$value) -Compress
      } finally { if($key){$key.Dispose()} }
    `));
  },
  write(value) {
    powershell(`
      $key=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment');
      try { $key.SetValue('Path',$env:CATKIT_USER_PATH_VALUE,[Microsoft.Win32.RegistryValueKind]::ExpandString) }
      finally { $key.Dispose() }
      Add-Type -Namespace Catkit -Name EnvironmentNotice -MemberDefinition '
        [DllImport("user32.dll", CharSet=CharSet.Unicode)]
        public static extern IntPtr SendMessageTimeout(IntPtr hwnd, uint msg, UIntPtr wparam, string lparam, uint flags, uint timeout, out UIntPtr result);';
      [UIntPtr]$result=[UIntPtr]::Zero;
      [Catkit.EnvironmentNotice]::SendMessageTimeout([IntPtr]65535,26,[UIntPtr]::Zero,'Environment',2,100,[ref]$result) | Out-Null
    `, value);
  },
};

function ensureUserPath(directory, store = windowsUserPath) {
  if (directory.includes(';')) throw new Error('安装目录含分号，无法加入 PATH；可用 --no-path 安装。');
  const before = store.read();
  const after = appendPath(before, directory);
  if (after !== before) store.write(after);
  return { directory, changed: after !== before };
}

module.exports = { appendPath, ensureUserPath, windowsUserPath };
