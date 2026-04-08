# bootstrap.ps1 — 依 manifest.yaml 把所有來源 repo clone 到正確相對位置。
#
# 用法：
#   cd <Project>\_workspaces
#   .\bootstrap.ps1            # 缺的就 clone，已存在則跳過
#   .\bootstrap.ps1 -Fetch     # 已存在的 repo 額外做 fetch --all --prune
#
# 不會切換 branch、不會強制覆寫，避免動到本地未推送的工作。

[CmdletBinding()]
param(
    [switch]$Fetch
)

$ErrorActionPreference = 'Stop'

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$Manifest    = Join-Path $ScriptDir 'manifest.yaml'

if (-not (Test-Path $Manifest)) {
    Write-Error "manifest.yaml not found at $Manifest"
}

# 簡單解析 yaml 的 repos 區段（避免依賴 powershell-yaml 模組）
$entries = New-Object System.Collections.Generic.List[hashtable]
$current = $null
foreach ($raw in Get-Content $Manifest) {
    $line = $raw -replace '#.*$', '' -replace '\s+$', ''
    if ($line -match '^\s*-\s+name:\s*(.+)$') {
        if ($null -ne $current) { $entries.Add($current) }
        $current = @{ name = $matches[1].Trim() }
        continue
    }
    if ($null -eq $current) { continue }
    if ($line -match '^\s*url:\s*(.+)$')    { $current.url    = $matches[1].Trim() }
    elseif ($line -match '^\s*path:\s*(.+)$')   { $current.path   = $matches[1].Trim() }
    elseif ($line -match '^\s*branch:\s*(.+)$') { $current.branch = $matches[1].Trim() }
}
if ($null -ne $current) { $entries.Add($current) }

$ok      = @()
$fetched = @()
$missing = @()

foreach ($e in $entries) {
    $target = Join-Path $ProjectRoot $e.path

    if (Test-Path (Join-Path $target '.git')) {
        if ($Fetch) {
            Write-Host "→ fetch $($e.name) ($($e.path))"
            git -C $target fetch --all --prune
            if ($LASTEXITCODE -eq 0) { $fetched += $e.name }
        } else {
            Write-Host "✓ exists $($e.name) ($($e.path))"
            $ok += $e.name
        }
        continue
    }

    Write-Host "→ clone $($e.name) → $($e.path) (branch: $($e.branch))"
    $parent = Split-Path -Parent $target
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    git clone --branch $e.branch $e.url $target
    if ($LASTEXITCODE -eq 0) { $ok += $e.name } else { $missing += $e.name }
}

Write-Host ''
Write-Host '── summary ──'
Write-Host "OK     : $($ok.Count)"
if ($Fetch) { Write-Host "Fetched: $($fetched.Count)" }
Write-Host "Missing: $($missing.Count)"
if ($missing.Count -gt 0) {
    foreach ($m in $missing) { Write-Host "  - $m" }
    exit 1
}
