[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Output
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (-not $IsWindows) { throw "The browser archive must be built on Windows." }
$uv = (Get-Command uv.exe -ErrorAction Stop).Source
$repository = Split-Path -Parent $PSScriptRoot
$destination = [System.IO.Path]::GetFullPath($Output)
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("roi-h-browser-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $work, (Split-Path -Parent $destination) | Out-Null

try {
    $previousPath = $env:PLAYWRIGHT_BROWSERS_PATH
    $previousGc = $env:PLAYWRIGHT_SKIP_BROWSER_GC
    $env:PLAYWRIGHT_BROWSERS_PATH = $work
    $env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
    try {
        & $uv run --frozen --project $repository python -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Playwright could not acquire Chromium." }
    }
    finally {
        $env:PLAYWRIGHT_BROWSERS_PATH = $previousPath
        $env:PLAYWRIGHT_SKIP_BROWSER_GC = $previousGc
    }
    if (-not (Test-Path (Join-Path $work "chromium-1228"))) {
        throw "Playwright 1.61.0 did not produce Chromium revision 1228."
    }
    Remove-Item $destination -Force -ErrorAction SilentlyContinue
    Compress-Archive -CompressionLevel Optimal -Path (Join-Path $work "*") -DestinationPath $destination
    $file = Get-Item $destination
    @{
        ok = $true
        archive = $file.FullName
        bytes = $file.Length
        sha256 = (Get-FileHash -Algorithm SHA256 $file.FullName).Hash.ToLowerInvariant()
        playwright_version = "1.61.0"
        browser_revision = "chromium-1228"
        targets = @(Get-ChildItem $work -Directory | Where-Object Name -NotLike ".*" | ForEach-Object Name | Sort-Object)
    } | ConvertTo-Json -Compress
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
