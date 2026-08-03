<#
  backup.ps1 - single git backup of user's projects into a remote repo.
  ASCII-only on purpose (PowerShell 5.1 reads no-BOM .ps1 as ANSI).

  Scheme: local backup repo C:\Users\lexas\projects-backup, one subfolder per
  project. Mirrors source working files into subfolders (robocopy /MIR, with
  exclusions), generates .env.example instead of secrets, scans staged diff for
  secrets, commits and pushes to branch projects-backup. Sources and their own
  .git are NEVER modified (read-only).

  Usage:
    powershell -ExecutionPolicy Bypass -File backup.ps1 -DryRun          # sync + scan + preview, NO commit/push
    powershell -ExecutionPolicy Bypass -File backup.ps1                  # real run: commit + push + Telegram
    powershell -ExecutionPolicy Bypass -File backup.ps1 -InstallSchedule # weekly task (Mon 03:30, i.e. Sun->Mon night)

  Exit codes: 0 = ok; 1 = secrets found (STOP); 2 = error (push/network/source).
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$NoPush,
  [switch]$InstallSchedule
)

$ErrorActionPreference = 'Stop'

# --------------------------- CONFIG ---------------------------
$RepoRoot  = 'C:\Users\lexas\projects-backup'
$Branch    = 'projects-backup'
$RemoteUrl = 'https://github.com/lexasanych1984-ux/Frol_Of.git'
$BotEnv    = 'C:\Users\lexas\bybit-tradfi-bot\.env'   # Telegram channel source (same as watchdog)
$TgPrefix  = '[BACKUP]'
$MaxFileMB = 50

# source dir -> backup subfolder
$Sources = @(
  @{ Name = 'tradingview-mcp';     Path = 'C:\Users\lexas\.claude\tools\tradingview-mcp' },
  @{ Name = 'tv-strategies';       Path = 'C:\Users\lexas\tv-strategies' },
  @{ Name = 'bybit-tradfi-bot';    Path = 'C:\Users\lexas\bybit-tradfi-bot' },
  @{ Name = 'ai-agent';            Path = 'D:\MY\Crypto\AI agent' },
  @{ Name = 'mql5-src';            Path = 'C:\Users\lexas\mql5-src' },
  @{ Name = 'sw-macros-futerovka'; Path = 'C:\SW_Macros\Futerovka' }
)

# single mq5 file from the MANUAL MT5 terminal data folder -> mql5-src\terminal-copy\
$TerminalMq5 = 'C:\Users\lexas\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts\Advisors\FrolTradeAssistant.mq5'

# memory folders of all claude projects -> claude-memory\<parent-folder-name>\
$MemoryGlob = 'C:\Users\lexas\.claude\projects\*\memory'

# copy exclusions
$ExcludeDirs  = @('.git','node_modules','venv','.venv','__pycache__','.pytest_cache','.wrangler','.idea','.vscode','.mypy_cache','cdp-profile','chrome-profile','browser-data')
# *.log excluded: bot.log contains the webhook token in a self-test URL. Valuable
# data in logs/ (trades.csv, executions.csv, reports\*.md/*.png) is still kept.
$ExcludeFiles = @('.env','.env.*','*.key','*.pem','credentials*','token*','id_rsa*','*.pyc','*.log')

# files NOT scanned for secrets (noisy hashes/binaries) - still backed up
$ScanIgnore = @('package-lock.json','*-lock.json','yarn.lock','pnpm-lock.yaml','*.map','*.min.js','*.min.css','*.ex5','*.swp','*.png','*.jpg','*.jpeg','*.gif','*.pdf','*.zip')

# --- git audit: repos whose OWN history must live off this machine ---------
# Remote is EXPLICIT per repo on purpose. tradingview-mcp pushes to 'mine', while
# its 'origin' is the upstream project we have no write access to - assuming
# 'origin' everywhere is exactly what hid 8 unpushed commits until 2026-08-02.
# Empty Remote = repo has none: its commits exist ONLY here, because robocopy
# mirrors working files but '.git' is in $ExcludeDirs.
$GitRepos = @(
  @{ Name = 'tv-strategies';    Path = 'C:\Users\lexas\tv-strategies';                 Remote = 'origin'; Branch = 'main' },
  @{ Name = 'tradingview-mcp';  Path = 'C:\Users\lexas\.claude\tools\tradingview-mcp'; Remote = 'mine';   Branch = 'main' },
  @{ Name = 'bybit-tradfi-bot'; Path = 'C:\Users\lexas\bybit-tradfi-bot';              Remote = 'origin'; Branch = 'master' },
  @{ Name = 'projects-backup';  Path = $RepoRoot;                                      Remote = 'origin'; Branch = $Branch },
  @{ Name = 'ai-agent';         Path = 'D:\MY\Crypto\AI agent';                        Remote = '';       Branch = 'main' },
  @{ Name = 'mql5-src';         Path = 'C:\Users\lexas\mql5-src';                      Remote = '';       Branch = 'master' }
)
# NOT .log on purpose: repo .gitignore excludes *.log, the audit would never commit.
$AuditLog = Join-Path $RepoRoot 'git-audit.md'

# --------------------------- HELPERS ---------------------------
function Say([string]$m) { Write-Host $m }

function Read-EnvFile([string]$file) {
  $h = @{}
  if (Test-Path -LiteralPath $file) {
    foreach ($line in [System.IO.File]::ReadAllLines($file)) {
      if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') { $h[$Matches[1]] = $Matches[2].Trim() }
    }
  }
  return $h
}

function Resolve-Telegram {
  $tok = ''; $chat = ''
  $b = Read-EnvFile $BotEnv
  if ($b.ContainsKey('TELEGRAM_BOT_TOKEN')) { $tok = $b['TELEGRAM_BOT_TOKEN'] }
  if ($b.ContainsKey('TELEGRAM_CHAT_ID'))   { $chat = $b['TELEGRAM_CHAT_ID'] }
  if ((-not $tok -or -not $chat) -and $b.ContainsKey('TELEGRAM_ENV_FILE') -and $b['TELEGRAM_ENV_FILE']) {
    $e = Read-EnvFile $b['TELEGRAM_ENV_FILE']
    if (-not $tok  -and $e.ContainsKey('TELEGRAM_BOT_TOKEN')) { $tok  = $e['TELEGRAM_BOT_TOKEN'] }
    if (-not $chat -and $e.ContainsKey('TELEGRAM_CHAT_ID'))   { $chat = $e['TELEGRAM_CHAT_ID'] }
  }
  return @{ Token = $tok; Chat = $chat }
}

function Send-Telegram([string]$text) {
  try {
    $tg = Resolve-Telegram
    if (-not $tg.Token -or -not $tg.Chat) { Say "Telegram: channel not configured - skipped."; return }
    $body = @{ chat_id = $tg.Chat; text = "$TgPrefix $text"; disable_web_page_preview = $true }
    Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$($tg.Token)/sendMessage" -Body $body -TimeoutSec 20 | Out-Null
  } catch { Say "Telegram: not sent ($($_.Exception.Message))" }
}

function Fail([string]$msg, [int]$code = 2) {
  Say "ERROR: $msg"
  Send-Telegram "ERROR: $msg"
  exit $code
}

# --------------------------- REPO BOOTSTRAP ---------------------------
function Ensure-Repo {
  if (-not (Test-Path -LiteralPath $RepoRoot)) { New-Item -ItemType Directory -Path $RepoRoot -Force | Out-Null }
  if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
    Push-Location $RepoRoot
    git init | Out-Null
    git symbolic-ref HEAD "refs/heads/$Branch"    # branch projects-backup from scratch
    git config core.autocrlf false
    Pop-Location
  }
  Push-Location $RepoRoot
  $hasOrigin = (git remote) -contains 'origin'
  if (-not $hasOrigin) { git remote add origin $RemoteUrl | Out-Null }
  # Commit identity: fresh repo has none and global is unset. Inherit from bot repo
  # (fallback to known values) so git commit does not fail with 'author unknown'.
  if (-not (git config user.email)) {
    $bn = git -C 'C:\Users\lexas\bybit-tradfi-bot' config user.name
    $be = git -C 'C:\Users\lexas\bybit-tradfi-bot' config user.email
    if (-not $bn) { $bn = 'lexas' }
    if (-not $be) { $be = 'lexasanych1984@gmail.com' }
    git config user.name $bn | Out-Null
    git config user.email $be | Out-Null
  }
  Pop-Location

  $readme = Join-Path $RepoRoot 'README.md'
  if (-not (Test-Path -LiteralPath $readme)) {
    $r = @()
    $r += '# projects-backup'
    $r += ''
    $r += 'Single git backup of project working files. Filled by backup.ps1'
    $r += '(robocopy mirror of sources into subfolders, secret scan, commit, push to branch projects-backup).'
    $r += 'Sources and their own .git are NOT touched.'
    $r += ''
    $r += 'Subfolders = projects. Secrets (.env, *.key, credentials*, token*, *.log) are NOT included:'
    $r += 'each .env is replaced by a *.example with the same keys and empty values.'
    $r += ''
    $r += 'Restore a project: copy its subfolder back, put your own .env in place.'
    [System.IO.File]::WriteAllLines($readme, $r, [System.Text.UTF8Encoding]::new($false))
  }
  $gi = Join-Path $RepoRoot '.gitignore'
  if (-not (Test-Path -LiteralPath $gi)) {
    $g = @('_verify-clone/','*.env','*.key','*.pem','credentials*','token*','*.log')
    [System.IO.File]::WriteAllLines($gi, $g, [System.Text.UTF8Encoding]::new($false))
  }

  $self = $PSCommandPath
  $dest = Join-Path $RepoRoot 'backup.ps1'
  if ($self -and ($self -ne $dest)) { Copy-Item -LiteralPath $self -Destination $dest -Force }
}

# --------------------------- SYNC ---------------------------
function Sync-Dir([string]$src, [string]$dstSub) {
  if (-not (Test-Path -LiteralPath $src)) { Fail "source not found: $src" }
  $dst = Join-Path $RepoRoot $dstSub
  if (-not (Test-Path -LiteralPath $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }
  $rcArgs = @($src, $dst, '/MIR', ('/MAX:' + ($MaxFileMB * 1MB)), '/R:1', '/W:1', '/NFL','/NDL','/NP','/NJH','/NJS')
  $rcArgs += '/XD'; $rcArgs += $ExcludeDirs
  $rcArgs += '/XF'; $rcArgs += $ExcludeFiles
  robocopy @rcArgs | Out-Null
  if ($LASTEXITCODE -ge 8) { Fail "robocopy failed ($LASTEXITCODE) for $src" }
  $global:LASTEXITCODE = 0
  New-EnvExamples $src $dst
}

function New-EnvExamples([string]$src, [string]$dst) {
  $files = Get-ChildItem -LiteralPath $src -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object {
      ($_.Name -eq '.env' -or $_.Name -like '.env.*') -and
      ($_.FullName -notmatch '\\(node_modules|\.git|\.venv|venv|__pycache__)\\')
    }
  foreach ($f in $files) {
    $rel = $f.FullName.Substring($src.TrimEnd('\').Length + 1)
    if ($f.Name -like '*.example') { $exampleName = $f.Name } else { $exampleName = $f.Name + '.example' }
    $relDir = Split-Path $rel -Parent
    if ($relDir) { $outDir = Join-Path $dst $relDir } else { $outDir = $dst }
    if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    $lines = [System.IO.File]::ReadAllLines($f.FullName)
    $out = foreach ($ln in $lines) {
      if ($ln -match '^\s*([^#=\s][^=]*)=') { "$($Matches[1])=" } else { $ln }
    }
    [System.IO.File]::WriteAllLines((Join-Path $outDir $exampleName), $out, [System.Text.UTF8Encoding]::new($false))
  }
}

function Sync-Memory {
  $dstBase = Join-Path $RepoRoot 'claude-memory'
  if (-not (Test-Path -LiteralPath $dstBase)) { New-Item -ItemType Directory -Path $dstBase -Force | Out-Null }
  foreach ($mem in (Get-ChildItem -Path $MemoryGlob -Directory -ErrorAction SilentlyContinue)) {
    $parent = Split-Path (Split-Path $mem.FullName -Parent) -Leaf
    $dst = Join-Path $dstBase $parent
    if (-not (Test-Path -LiteralPath $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }
    $mArgs = @($mem.FullName, $dst, '/MIR', '/R:1', '/W:1', '/NFL','/NDL','/NP','/NJH','/NJS')
    robocopy @mArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "robocopy failed ($LASTEXITCODE) for memory $($mem.FullName)" }
    $global:LASTEXITCODE = 0
  }
}

function Sync-TerminalMq5 {
  if (-not (Test-Path -LiteralPath $TerminalMq5)) { Fail "terminal mq5 not found: $TerminalMq5" }
  $dst = Join-Path $RepoRoot 'mql5-src\terminal-copy'
  if (-not (Test-Path -LiteralPath $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }
  # Copy-Item = binary copy, mq5 UTF-8 encoding preserved. Do NOT use Get/Set-Content!
  Copy-Item -LiteralPath $TerminalMq5 -Destination (Join-Path $dst (Split-Path $TerminalMq5 -Leaf)) -Force
}

# --------------------------- SECRET SCAN ---------------------------
function Test-ScanIgnored([string]$path) {
  $leaf = Split-Path $path -Leaf
  foreach ($p in $ScanIgnore) { if ($leaf -like $p) { return $true } }
  return $false
}

function Invoke-SecretScan {
  $patterns = @(
    @{ N='private-key';    R='-----BEGIN [A-Z ]*PRIVATE KEY-----' },
    @{ N='telegram-token'; R='\b\d{8,10}:[A-Za-z0-9_-]{35}\b' },
    @{ N='notion-token';   R='\b(ntn_|secret_)[A-Za-z0-9]{20,}\b' },
    @{ N='slack-token';    R='xox[baprs]-[A-Za-z0-9-]{10,}' },
    @{ N='aws-key';        R='AKIA[0-9A-Z]{16}' },
    # value must be a LITERAL secret: quoted string (>=16) OR bare high-entropy token
    # (>=24, no dots/parens) - so it does NOT match references like `token = os.getenv(x)`.
    @{ N='secret-assign';  R='(?i)(pass(word|wd)?|token|secret|api[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret)\s*[:=]\s*(["''][A-Za-z0-9+/_\-\.]{16,}["'']|[A-Za-z0-9+/_\-]{24,})' },
    @{ N='url-token';      R='(?i)/(hook|head|pull|webhook|token|key)/[A-Za-z0-9_\-]{20,}' }
  )
  Push-Location $RepoRoot
  $diff = git diff --cached --unified=0
  Pop-Location
  $findings = @()
  $curFile = ''
  $curLine = 0
  foreach ($ln in $diff) {
    if ($ln -match '^\+\+\+ b/(.+)$')  { $curFile = $Matches[1]; continue }
    if ($ln -match '^@@ .*\+(\d+)')    { $curLine = [int]$Matches[1]; continue }
    if ($ln -like '+++*' -or $ln -like '---*') { continue }
    if ($ln.StartsWith('+')) {
      $content = $ln.Substring(1)
      if (-not (Test-ScanIgnored $curFile)) {
        foreach ($p in $patterns) {
          if ($content -match $p.R) {
            $m = $Matches[0]
            if ($m.Length -gt 8) { $mask = $m.Substring(0,4) + '***' + $m.Substring($m.Length-2) } else { $mask = '***' }
            $findings += [pscustomobject]@{ File=$curFile; Line=$curLine; Rule=$p.N; Snippet=$mask }
          }
        }
      }
      $curLine++
    }
  }
  return $findings
}

# --------------------------- GIT AUDIT ---------------------------
# Read-only health check of the OTHER repos: work that never left this machine.
# Never commits, never pushes, never modifies their .git - only queries.

function Get-SafeRemoteUrl([string]$path, [string]$remote) {
  if (-not $remote) { return '' }
  $u = (git -C $path remote get-url $remote 2>$null)
  $global:LASTEXITCODE = 0
  if (-not $u) { return '' }
  # drop user:token@ if anyone ever embedded credentials in the URL
  return ($u -replace '://[^/@]*@', '://')
}

function Invoke-GitAudit {
  # Unattended task: git must FAIL on a missing credential, not hang on a prompt.
  $prevPrompt = $env:GIT_TERMINAL_PROMPT
  $env:GIT_TERMINAL_PROMPT = '0'
  # PS 5.1 turns a native command's stderr into ErrorRecords, and the script-wide
  # 'Stop' preference then makes a harmless line abort everything: git writes
  # "Everything up-to-date" to STDERR, which killed the first -DryRun of this
  # patch. The audit is a report, never a gate. Use 2>$null, never 2>&1.
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $rows = @()
  try {
    foreach ($r in $GitRepos) {
      $row = [pscustomobject]@{
        Name = $r.Name; Branch = ''; Ahead = $null; Dirty = 0
        Remote = $r.Remote; Url = ''; Level = 'OK'; Note = ''
      }
      if (-not (Test-Path -LiteralPath (Join-Path $r.Path '.git'))) {
        $row.Level = 'WARN'; $row.Note = 'not a git repo or path missing'
        $rows += $row; continue
      }
      $row.Branch = (git -C $r.Path rev-parse --abbrev-ref HEAD 2>$null)
      # The backup repo itself is ALWAYS dirty here: the audit runs after robocopy
      # and before the commit. Counting it would raise a WARN every single week,
      # which is how you learn to ignore warnings. Ahead/push checks still apply.
      if ($r.Path -ne $RepoRoot) {
        $row.Dirty = @(git -C $r.Path status --porcelain --untracked-files=no 2>$null).Count
      }
      $global:LASTEXITCODE = 0
      if ($row.Branch -ne $r.Branch) {
        $row.Level = 'WARN'; $row.Note = "on branch '$($row.Branch)', expected '$($r.Branch)'"
        $rows += $row; continue
      }
      if (-not $r.Remote) {
        $row.Level = 'WARN'
        $row.Note = 'no remote: history exists only on this machine'
        $rows += $row; continue
      }
      if (-not ((git -C $r.Path remote 2>$null) -contains $r.Remote)) {
        $global:LASTEXITCODE = 0
        $row.Level = 'ERROR'; $row.Note = "remote '$($r.Remote)' is not configured"
        $rows += $row; continue
      }
      $global:LASTEXITCODE = 0
      $row.Url = Get-SafeRemoteUrl $r.Path $r.Remote

      # Authoritative remote tip via ls-remote, NOT the local tracking ref:
      # a stale origin/main would under-report 'ahead'.
      $ls = (git -C $r.Path ls-remote --heads $r.Remote ('refs/heads/' + $r.Branch) 2>$null)
      if ($LASTEXITCODE -ne 0) {
        $global:LASTEXITCODE = 0
        $row.Level = 'ERROR'; $row.Note = 'cannot read remote (auth or network)'
        $rows += $row; continue
      }
      $global:LASTEXITCODE = 0
      $sha = ''
      foreach ($l in @($ls)) { if ("$l" -match '^([0-9a-f]{40})\s') { $sha = $Matches[1]; break } }
      if (-not $sha) {
        $row.Level = 'WARN'; $row.Note = "branch '$($r.Branch)' does not exist on remote yet"
        $rows += $row; continue
      }
      $cnt = (git -C $r.Path rev-list --count ($sha + '..HEAD') 2>$null)
      if ($LASTEXITCODE -ne 0 -or -not $cnt) {
        $global:LASTEXITCODE = 0
        $row.Level = 'WARN'; $row.Note = 'remote tip unknown locally - run git fetch'
        $rows += $row; continue
      }
      $global:LASTEXITCODE = 0
      $row.Ahead = [int]$cnt

      # Write permission WITHOUT sending anything: --dry-run still asks the server
      # for git-receive-pack, so a read-only remote returns 403 here exactly as a
      # real push would. This is the check that would have caught the 403 upfront.
      git -C $r.Path push --dry-run $r.Remote ('HEAD:refs/heads/' + $r.Branch) 2>$null | Out-Null
      $pushOk = ($LASTEXITCODE -eq 0)
      $global:LASTEXITCODE = 0

      if (-not $pushOk) {
        $row.Level = 'ERROR'; $row.Note = 'push refused (no write access?)'
      } elseif ($row.Ahead -gt 0) {
        $row.Level = 'WARN'; $row.Note = "$($row.Ahead) commit(s) never pushed"
      } elseif ($row.Dirty -gt 0) {
        $row.Level = 'WARN'; $row.Note = "$($row.Dirty) uncommitted change(s)"
      }
      $rows += $row
    }
  } finally {
    $env:GIT_TERMINAL_PROMPT = $prevPrompt
    $ErrorActionPreference = $prevEap
    $global:LASTEXITCODE = 0
  }
  return $rows
}

function Write-AuditLog($rows, [string]$stamp) {
  $out = @()
  if (Test-Path -LiteralPath $AuditLog) {
    $out += [System.IO.File]::ReadAllLines($AuditLog)
  } else {
    $out += '# git audit'
    $out += ''
    $out += 'Appended by backup.ps1 on every run. Read-only check of the other repos:'
    $out += 'unpushed commits, push permission, uncommitted changes. Newest section last.'
    $out += ''
  }
  $out += ('## ' + $stamp)
  $out += ''
  $out += '| repo | branch | ahead | dirty | remote | level | note |'
  $out += '|---|---|---|---|---|---|---|'
  foreach ($r in $rows) {
    if ($null -eq $r.Ahead) { $a = '-' } else { $a = [string]$r.Ahead }
    $rem = $r.Remote; if (-not $rem) { $rem = '(none)' }
    $out += ('| {0} | {1} | {2} | {3} | {4} | {5} | {6} |' -f $r.Name, $r.Branch, $a, $r.Dirty, $rem, $r.Level, $r.Note)
  }
  $out += ''
  [System.IO.File]::WriteAllLines($AuditLog, $out, [System.Text.UTF8Encoding]::new($false))
}

# --------------------------- PREVIEW ---------------------------
function Show-Preview {
  # Walk the filesystem directly (robust to Cyrillic / spaces in names, unlike
  # parsing git's escaped --name-only output).
  Say ""
  Say "=== BACKUP CONTENT (by subfolder) ==="
  $total = 0.0; $totalCount = 0
  $subs = Get-ChildItem -LiteralPath $RepoRoot -Directory -Force | Where-Object { $_.Name -ne '.git' } | Sort-Object Name
  foreach ($d in $subs) {
    $files = @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
    $bytes = 0.0
    foreach ($f in $files) { $bytes += $f.Length }
    $total += $bytes; $totalCount += $files.Count
    Say ("  {0,-26} {1,6} files  {2,9} MB" -f $d.Name, $files.Count, [math]::Round($bytes/1MB,2))
  }
  $rootFiles = @(Get-ChildItem -LiteralPath $RepoRoot -File -Force)
  if ($rootFiles.Count -gt 0) {
    $rb = 0.0; foreach ($f in $rootFiles) { $rb += $f.Length }
    $total += $rb; $totalCount += $rootFiles.Count
    Say ("  {0,-26} {1,6} files  {2,9} MB" -f '(repo root)', $rootFiles.Count, [math]::Round($rb/1MB,2))
  }
  Say ("  {0,-26} {1,6} files  {2,9} MB" -f 'TOTAL', $totalCount, [math]::Round($total/1MB,2))
}

# --------------------------- SCHEDULE ---------------------------
function Install-Schedule {
  $ps = (Get-Command powershell).Source
  $script = Join-Path $RepoRoot 'backup.ps1'
  $action = New-ScheduledTaskAction -Execute $ps -Argument "-ExecutionPolicy Bypass -NoProfile -File `"$script`""
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 3:30am
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
  Register-ScheduledTask -TaskName 'ProjectsBackup' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  Say "Scheduler: task 'ProjectsBackup' - weekly, Sun->Mon night 03:30."
}

# --------------------------- MAIN ---------------------------
try {
  Ensure-Repo

  if ($InstallSchedule) { Install-Schedule; exit 0 }

  Say "Syncing sources..."
  foreach ($s in $Sources) { Say "  -> $($s.Name)"; Sync-Dir $s.Path $s.Name }
  Say "  -> claude-memory"; Sync-Memory
  Say "  -> mql5-src\terminal-copy"; Sync-TerminalMq5

  # Audit runs BEFORE 'git add -A' so its log lands in this same commit.
  # Findings never abort the backup: saving files matters more than reporting.
  Say "Git audit (other repos)..."
  $audit = Invoke-GitAudit
  Write-AuditLog $audit (Get-Date -Format 'yyyy-MM-dd HH:mm')
  $auditBad = @($audit | Where-Object { $_.Level -ne 'OK' })
  $auditMsg = ''
  if ($auditBad.Count -eq 0) {
    Say "Git audit: all repos pushed and clean."
  } else {
    foreach ($a in $auditBad) { Say ("  {0}: {1} - {2}" -f $a.Level, $a.Name, $a.Note) }
    $auditMsg = ' | AUDIT: ' + ((($auditBad | ForEach-Object { "$($_.Name) [$($_.Level)] $($_.Note)" })) -join '; ')
  }

  # Drop nested (source) .gitignore files so their rules do NOT hide backed-up
  # working files (config.yaml, logs\trades.csv, state.db, reports). Only the
  # repo-root .gitignore governs; secrets are excluded by robocopy + secret scan.
  Get-ChildItem -LiteralPath $RepoRoot -Recurse -Force -Filter '.gitignore' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -ne (Join-Path $RepoRoot '.gitignore') } |
    Remove-Item -Force -ErrorAction SilentlyContinue

  Push-Location $RepoRoot
  git add -A | Out-Null
  Pop-Location

  Say "Secret scan..."
  $found = Invoke-SecretScan
  if ($found.Count -gt 0) {
    Say ""
    Say "!!! POSSIBLE SECRETS FOUND - COMMIT ABORTED !!!"
    $found | ForEach-Object { Say ("  {0}:{1}  [{2}]  {3}" -f $_.File, $_.Line, $_.Rule, $_.Snippet) }
    Send-Telegram "ERROR: secret scan found $($found.Count) matches - backup stopped, nothing pushed."
    exit 1
  }
  Say "Secret scan: clean."

  Push-Location $RepoRoot
  $changed = @(git diff --cached --name-only).Count   # count only (no FS paths) - safe on any names
  Pop-Location

  Show-Preview

  if ($DryRun) {
    Say ""
    Say ("[DryRun] Staged changes: {0} file(s). NOT committed, NOT pushed." -f $changed)
    Say "[DryRun] Review the list above. For a real run, run without -DryRun."
    exit 0
  }

  # git-audit.md changes on every run, so 'nothing to commit' can no longer
  # happen. Report source changes separately from the audit-only case.
  $srcChanged = @(git -C $RepoRoot diff --cached --name-only) | Where-Object { $_ -ne 'git-audit.md' }
  $srcCount = @($srcChanged).Count
  if ($changed -eq 0) { Say "No changes - nothing to commit."; Send-Telegram "OK: no changes.$auditMsg"; exit 0 }
  if ($srcCount -eq 0) { Say "No source changes - committing audit log only." }

  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
  Push-Location $RepoRoot
  git commit -m "backup $stamp" | Out-Null
  if (-not $NoPush) {
    git push -u origin $Branch
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "git push returned $LASTEXITCODE" }
    $status = git status -sb
    if ($status -match 'ahead') { Pop-Location; Fail "still 'ahead' after push - push did not land" }
  }
  Pop-Location

  Say "Done: $changed file(s) changed, pushed to $Branch."
  if ($auditBad.Count -gt 0) { $lead = 'OK (audit warnings)' } else { $lead = 'OK' }
  Send-Telegram "${lead}: $changed file(s) changed, pushed to branch $Branch (commit backup $stamp).$auditMsg"
  exit 0
}
catch {
  Fail $_.Exception.Message
}
