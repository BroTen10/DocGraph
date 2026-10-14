<#
.SYNOPSIS
    DocGraph 版本对比：本机开发环境 vs 服务器正式环境。

.DESCRIPTION
    三路取数，逐项列出后给出一行结论：
      - 本机工作区：调用 backend/app/version.py（与后端 /api/version 同一份实现）
      - 本机运行中后端：默认 http://localhost:8800（未启动则跳过）
      - 服务器正式环境：-Remote 指定的地址，经前端 nginx 反代取 /api/version

    结论按"提交号"判定（版本号相同但提交不同 = 代码不同，必须看提交）。

.EXAMPLE
    .\scripts\version.ps1
    .\scripts\version.ps1 -Remote https://ontologyready.com
    .\scripts\version.ps1 -Remote http://127.0.0.1:8080 -LocalApi '' -NoLocalApi
#>
[CmdletBinding()]
param(
    # 服务器正式环境地址（前端入口，不要带 /api 后缀）
    [string]$Remote = '',
    # 本机运行中的后端地址；置空或配合 -NoLocalApi 可跳过
    [string]$LocalApi = 'http://localhost:8800',
    # 跳过"本机运行中后端"这一路
    [switch]$NoLocalApi
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'

function Get-WorkspaceVersionInfo {
    param([string]$RepoRoot, [string]$Python)
    if (-not (Test-Path $Python)) {
        throw "未找到虚拟环境：$Python（本机工作区版本无法读取）"
    }
    $code = "import sys, json; sys.path.insert(0, r'$RepoRoot\backend'); from app.version import collect_build_info; print(json.dumps(collect_build_info(), ensure_ascii=False))"
    $raw = & $Python -c $code
    if ($LASTEXITCODE -ne 0) {
        throw "读取本机工作区版本失败：$raw"
    }
    return $raw | ConvertFrom-Json
}

function Get-ApiVersionInfo {
    param([string]$BaseUrl)
    $url = $BaseUrl.TrimEnd('/') + '/api/version'
    return Invoke-RestMethod -Uri $url -TimeoutSec 8
}

function New-Row {
    param([string]$Where, $Info)
    [pscustomobject]@{
        对比项   = $Where
        版本     = $Info.version
        '标签'   = $Info.label
        提交     = $Info.commit
        描述     = $Info.describe
        构建环境 = $Info.build_env
        构建时间 = if ($Info.built_at) { $Info.built_at } else { $Info.commit_date }
        取值来源 = $Info.source
    }
}

$rows = @()
$notes = @()

Write-Host '=== DocGraph 版本对比 ===' -ForegroundColor Cyan

$workspace = $null
try {
    $workspace = Get-WorkspaceVersionInfo -RepoRoot $repoRoot -Python $python
    $rows += New-Row '本机工作区（代码）' $workspace
}
catch {
    $notes += "本机工作区：$($_.Exception.Message)"
}

$localApiInfo = $null
if (-not $NoLocalApi -and $LocalApi) {
    try {
        $localApiInfo = Get-ApiVersionInfo -BaseUrl $LocalApi
        $rows += New-Row "本机运行中后端（$LocalApi）" $localApiInfo
    }
    catch {
        $notes += "本机运行中后端（$LocalApi）未响应，已跳过"
    }
}

$remoteInfo = $null
if ($Remote) {
    try {
        $remoteInfo = Get-ApiVersionInfo -BaseUrl $Remote
        $rows += New-Row "服务器正式环境（$Remote）" $remoteInfo
    }
    catch {
        $notes += "服务器正式环境（$Remote）未响应：$($_.Exception.Message)"
    }
}

Write-Host ''
if ($rows.Count -gt 0) {
    $rows | Format-Table -AutoSize -Wrap
}

Write-Host '--- 结论 ---'

$changed = $false

if ($localApiInfo -and $workspace) {
    if ($localApiInfo.commit -and $workspace.commit -and $localApiInfo.commit -ne $workspace.commit) {
        Write-Host "本机运行中后端与本机代码不一致（$($localApiInfo.commit) vs $($workspace.commit)）：后端进程未加载最新代码，重启 backend 或确认 --reload 生效。" -ForegroundColor Yellow
        $changed = $true
    }
}

if ($remoteInfo -and $workspace) {
    if ($remoteInfo.commit -and $workspace.commit) {
        if ($remoteInfo.commit -eq $workspace.commit) {
            Write-Host "服务器与本机代码提交一致（$($workspace.commit)）。" -ForegroundColor Green
        }
        else {
            Write-Host "服务器与本机代码提交不一致：服务器 $($remoteInfo.commit) / 本机 $($workspace.commit)。" -ForegroundColor Yellow
            Write-Host "  服务器部署哪个提交：到服务器执行 bash deploy.sh check，或 git -C /opt/docgraph/app log -1 --oneline" -ForegroundColor Gray
            $changed = $true
        }
    }
    else {
        Write-Host '服务器或本机缺少提交定位（镜像未经 deploy.sh 构建，或本地不是 git 工作区），无法比较提交。' -ForegroundColor Yellow
        $changed = $true
    }
}

if (-not $changed) {
    Write-Host '未发现不一致（未指定 -Remote 时仅比较本机两路）。' -ForegroundColor Green
}

if ($notes.Count -gt 0) {
    Write-Host ''
    Write-Host '--- 说明 ---' -ForegroundColor Gray
    foreach ($note in $notes) {
        Write-Host "  $note" -ForegroundColor DarkGray
    }
}
