[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackageA,

    [Parameter(Mandatory = $true)]
    [string]$PackageBadB,

    [Parameter(Mandatory = $true)]
    [string]$PackageB,

    [Parameter(Mandatory = $true)]
    [string]$BrowserArchive,

    [Parameter(Mandatory = $true)]
    [string]$Certificate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$packageName = "ROI-H.Prototype"
$packageAPath = (Resolve-Path $PackageA).Path
$packageBadBPath = (Resolve-Path $PackageBadB).Path
$packageBPath = (Resolve-Path $PackageB).Path
$archivePath = (Resolve-Path $BrowserArchive).Path
$certificatePath = (Resolve-Path $Certificate).Path
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("roi-h-msix-proof-" + [guid]::NewGuid())
$sentinelHome = Join-Path $work "customer-home"
$functionalHome = Join-Path $work "functional-home"
$isolatedCwd = Join-Path $work "unrelated-cwd"
$browserRoot = Join-Path $env:LOCALAPPDATA "ROI-H\Browsers"
$trustedCertificate = $null
New-Item -ItemType Directory -Force -Path $sentinelHome, $isolatedCwd | Out-Null
Set-Content -Encoding utf8 (Join-Path $sentinelHome "sentinel.txt") "customer-data-must-not-change"

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-TreeDigest([string]$Root) {
    $rows = foreach ($file in Get-ChildItem -Recurse -File $Root | Sort-Object FullName) {
        $relative = $file.FullName.Substring($Root.Length).TrimStart('\')
        "$relative`0$($file.Length)`0$((Get-FileHash -Algorithm SHA256 $file.FullName).Hash)"
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($rows -join "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([Convert]::ToHexString($sha.ComputeHash($bytes))).ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Assert-CustomerHome([string]$Expected) {
    Assert ((Get-TreeDigest $sentinelHome) -eq $Expected) "MSIX servicing changed ROI_H_HOME."
}

function Get-InstalledPackage {
    return Get-AppxPackage -Name $packageName -ErrorAction SilentlyContinue
}

function Invoke-ROI([string[]]$Arguments, [int]$ExpectedExit = 0) {
    $alias = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\roi-h.exe"
    $output = & $alias @Arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne $ExpectedExit) {
        throw "roi-h $($Arguments -join ' ') exited $LASTEXITCODE instead of $ExpectedExit.`n$output"
    }
    return $output.Trim()
}

function Call-Agent([string]$Operation, [hashtable]$Request) {
    $requestFile = Join-Path $isolatedCwd ($Operation.Replace('.', '-') + "-" + [guid]::NewGuid() + ".json")
    $Request | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 $requestFile
    return (Invoke-ROI @("agent", "call", $Operation, "--input", $requestFile) | ConvertFrom-Json)
}

function Install-Browser([string]$InstallLocation) {
    $target = Get-Content -Raw (Join-Path $InstallLocation "browser-target.json") | ConvertFrom-Json
    $archive = Get-Item $archivePath
    Assert ($archive.Length -eq $target.bytes) "The browser archive length does not match signed package metadata."
    $hash = (Get-FileHash -Algorithm SHA256 $archivePath).Hash.ToLowerInvariant()
    Assert ($hash -eq $target.sha256) "The browser archive digest does not match signed package metadata."

    New-Item -ItemType Directory -Force -Path $browserRoot | Out-Null
    Get-ChildItem $browserRoot -Directory -Filter ".staging-*" | Remove-Item -Recurse -Force
    $staging = Join-Path $browserRoot (".staging-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $staging | Out-Null
    try {
        Expand-Archive -Path $archivePath -DestinationPath $staging
        Assert (Test-Path (Join-Path $staging $target.browser_revision)) "The verified archive has no required Chromium target."
        foreach ($item in Get-ChildItem $staging) {
            $destination = Join-Path $browserRoot $item.Name
            if (Test-Path $destination) { Remove-Item -Recurse -Force $item.FullName }
            else { Move-Item $item.FullName $destination }
        }
    }
    finally {
        Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
    }
}

function Install-Package([string]$Path, [switch]$Downgrade) {
    $parameters = @{
        Path = $Path
        ForceApplicationShutdown = $true
    }
    if ($Downgrade) { $parameters.ForceUpdateFromAnyVersion = $true }
    Add-AppxPackage @parameters
}

function Assert-Doctor {
    $doctor = Invoke-ROI @("doctor", "--output", "json") | ConvertFrom-Json
    Assert ($doctor.ok -eq $true) "The installed browser doctor did not pass."
    $browser = $doctor.checks | Where-Object code -eq "browser.launch"
    Assert ($browser.status -eq "pass") "The browser launch check did not pass."
    Assert ($browser.details.playwright_version -eq "1.61.0") "Doctor used an unexpected Playwright version."
    Assert ($browser.details.browser_root -eq $browserRoot) "Doctor used the wrong managed browser root."
}

function Assert-SubprocessSkill {
    $created = Call-Agent "project.create" @{
        idempotency_key = "msix-project"
        arguments = @{ home = $functionalHome; name = "msix-proof" }
    }
    Assert ($created.ok -eq $true) "The installed CLI could not create a project."
    $source = @'
from pydantic import BaseModel
TOOL_ID = "echo"
TOOL_EFFECT = "read"
IDEMPOTENCY = "key"
ALLOW_IN_PROD = True
REQUIRES_APPROVAL = False
class Input(BaseModel):
    value: str
class Output(BaseModel):
    ok: bool = True
    result: str

def run(args: Input) -> Output:
    return Output(result=args.value)
'@
    $base = @{
        context = @{ project = "msix-proof"; environment = "dev" }
        arguments = @{ home = $functionalHome }
    }
    $defined = Call-Agent "skill.define" @{
        context = $base.context
        idempotency_key = "msix-skill"
        arguments = @{ home = $functionalHome; skill = "sample"; tool = "echo"; source = $source }
    }
    Assert ($defined.ok -eq $true) "The installed CLI could not define a subprocess skill."
    $started = Call-Agent "run.start" @{
        context = $base.context
        idempotency_key = "msix-run"
        arguments = @{ home = $functionalHome; run_id = "msix-run"; goal = "Prove packaged subprocess execution" }
    }
    Assert ($started.ok -eq $true) "The installed CLI could not start a run."
    $context = @{ project = "msix-proof"; environment = "dev"; run_id = "msix-run" }
    $phase = Call-Agent "phase.begin" @{
        context = $context
        idempotency_key = "msix-phase"
        arguments = @{ home = $functionalHome; name = "prove" }
    }
    Assert ($phase.ok -eq $true) "The installed CLI could not start a phase."
    $invoked = Call-Agent "tool.invoke" @{
        context = $context
        idempotency_key = "msix-invoke"
        arguments = @{ home = $functionalHome; name = "sample.echo"; arguments = @{ value = "subprocess-ok" }; force = $true }
    }
    Assert ($invoked.result.status -eq "ok") "The packaged subprocess skill did not run."
    Assert ($invoked.result.output.result -eq "subprocess-ok") "The packaged subprocess returned the wrong result."
}

if (-not $IsWindows) { throw "The MSIX journey requires Windows." }
$baseline = Get-TreeDigest $sentinelHome
$env:ROI_H_HOME = $sentinelHome

try {
    Assert ($null -eq (Get-InstalledPackage)) "Remove the existing ROI-H prototype package before this clean-machine proof."
    Assert (-not (Test-Path $browserRoot)) "Move or remove the existing managed browser root before this proof."

    $trustedCertificate = Import-Certificate -FilePath $certificatePath -CertStoreLocation "Cert:\CurrentUser\TrustedPeople"
    foreach ($package in @($packageAPath, $packageBadBPath, $packageBPath)) {
        Assert ((Get-AuthenticodeSignature $package).Status -eq "Valid") "A prototype MSIX signature is not trusted."
    }

    Install-Package $packageAPath
    $installedA = Get-InstalledPackage
    Assert ($null -ne $installedA) "Package A was not installed."
    New-Item -ItemType Directory -Force -Path (Join-Path $browserRoot ".staging-interrupted") | Out-Null
    Set-Content (Join-Path $browserRoot ".staging-interrupted\partial") "partial"
    Install-Browser $installedA.InstallLocation
    Assert (-not (Test-Path (Join-Path $browserRoot ".staging-interrupted"))) "Interrupted browser acquisition was not reconciled."
    Push-Location $isolatedCwd
    try {
        $applicationVersionA = "$($installedA.Version.Major).$($installedA.Version.Minor).$($installedA.Version.Build)"
        Assert ((Invoke-ROI @("--version")) -match [regex]::Escape($applicationVersionA)) "The execution alias did not expose package A."
        Assert-Doctor
        Assert-SubprocessSkill
    }
    finally { Pop-Location }
    Assert-CustomerHome $baseline

    Install-Package $packageBadBPath
    $installedBadB = Get-InstalledPackage
    Assert ($installedBadB.Version -gt $installedA.Version) "The unhealthy package did not replace package A."
    Install-Browser $installedBadB.InstallLocation
    Invoke-ROI @("doctor", "--output", "json") 1 | Out-Null
    Install-Package $packageAPath -Downgrade
    Assert-Doctor
    Assert-CustomerHome $baseline

    Install-Package $packageBPath
    $installedB = Get-InstalledPackage
    Assert ($installedB.Version -gt $installedBadB.Version) "Healthy package B did not replace the failed update."
    Install-Browser $installedB.InstallLocation
    Assert-Doctor
    Move-Item $archivePath "$archivePath.offline"
    try { Install-Package $packageAPath -Downgrade }
    finally { Move-Item "$archivePath.offline" $archivePath }
    Assert-Doctor
    Assert-CustomerHome $baseline

    $installed = Get-InstalledPackage
    Remove-AppxPackage -Package $installed.PackageFullName
    Remove-Item -Recurse -Force $browserRoot
    Remove-Item (Split-Path -Parent $browserRoot) -ErrorAction SilentlyContinue
    Assert ($null -eq (Get-InstalledPackage)) "MSIX uninstall left the application registered."
    Assert (-not (Test-Path $browserRoot)) "ROI-H uninstall left managed browsers behind."
    Assert-CustomerHome $baseline

    @{
        ok = $true
        platform = (Get-CimInstance Win32_OperatingSystem).Caption
        package_a = $installedA.Version.ToString()
        failed_package_b = $installedBadB.Version.ToString()
        package_b = $installedB.Version.ToString()
        cli_alias = "roi-h.exe"
        browser_revision = "chromium-1228"
        browser_launch = "pass"
        subprocess_skill = "pass"
        interrupted_acquisition_recovery = "pass"
        failed_doctor_restore = "pass"
        offline_downgrade = "pass"
        package_uninstall = "pass"
        managed_browser_cleanup = "scripted"
        customer_home_sha256 = $baseline
    } | ConvertTo-Json -Compress
}
finally {
    $installed = Get-InstalledPackage
    if ($null -ne $installed) { Remove-AppxPackage -Package $installed.PackageFullName -ErrorAction SilentlyContinue }
    Remove-Item -Recurse -Force $browserRoot -ErrorAction SilentlyContinue
    Remove-Item (Split-Path -Parent $browserRoot) -ErrorAction SilentlyContinue
    if ($null -ne $trustedCertificate) {
        Remove-Item "Cert:\CurrentUser\TrustedPeople\$($trustedCertificate.Thumbprint)" -ErrorAction SilentlyContinue
    }
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
