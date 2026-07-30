[CmdletBinding(DefaultParameterSetName = "ExistingCertificate")]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+(\.\d+)?$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [string]$BrowserArchive,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$Publisher = "CN=ROI-H Prototype",
    [string]$PackageUri,
    [string]$AppInstallerUri,
    [string]$StateBrowserRevision = "chromium-1228",

    [Parameter(Mandatory = $true, ParameterSetName = "ExistingCertificate")]
    [string]$CertificateThumbprint,

    [Parameter(Mandatory = $true, ParameterSetName = "TestCertificate")]
    [switch]$CreateTestCertificate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "$Name is required. Run this script from a Visual Studio Developer PowerShell with the Windows SDK."
    }
    return $command.Source
}

function Run([scriptblock]$Command, [string]$Failure) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $Failure
    }
}

if (-not $IsWindows -or $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "The MSIX prototype build requires a Windows x64 developer shell."
}
$versionParts = $Version -split '\.'
if ($versionParts.Count -notin @(3, 4) -or ($versionParts | Where-Object { $_.Length -gt 5 -or [int]$_ -gt 65535 })) {
    throw "Each MSIX version component must be between 0 and 65535."
}
if ($PSCmdlet.ParameterSetName -eq "ExistingCertificate") {
    $signingCertificate = Get-Item "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction Stop
    if ($signingCertificate.Subject -ne $Publisher) {
        throw "The MSIX publisher must exactly match the signing certificate subject."
    }
}

$uv = Require-Command "uv.exe"
$cl = Require-Command "cl.exe"
$makeAppx = Require-Command "MakeAppx.exe"
$signTool = Require-Command "SignTool.exe"
$repository = Split-Path -Parent $PSScriptRoot
$archive = (Resolve-Path $BrowserArchive).Path
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("roi-h-msix-" + [guid]::NewGuid())
$payload = Join-Path $work "payload"
$runtime = Join-Path $payload "runtime"
$wheelDirectory = Join-Path $work "wheel"
$sourceRoot = Join-Path $work "source"
New-Item -ItemType Directory -Force -Path $runtime, $wheelDirectory, $sourceRoot | Out-Null

try {
    Run { & $uv python install 3.12 } "uv could not install the locked Python 3.12 build runtime."
    $python = (& $uv python find --managed-python 3.12).Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $python)) {
        throw "uv could not locate managed Python 3.12."
    }
    $pythonRoot = Split-Path -Parent $python
    Copy-Item -Recurse -Force (Join-Path $pythonRoot "*") $runtime

    Copy-Item (Join-Path $repository "LICENSE"), (Join-Path $repository "README.md"), (Join-Path $repository "pyproject.toml") $sourceRoot
    Copy-Item -Recurse (Join-Path $repository "src"), (Join-Path $repository "skills") $sourceRoot
    $projectFile = Join-Path $sourceRoot "pyproject.toml"
    $projectText = Get-Content -Raw $projectFile
    $projectText = $projectText -replace '(?m)^version = "[^"]+"$', "version = `"$Version`""
    Set-Content -Encoding utf8 $projectFile $projectText
    Run {
        & $uv build --wheel --out-dir $wheelDirectory $sourceRoot
    } "The ROI-H wheel build failed."
    $wheel = Get-ChildItem $wheelDirectory -Filter "roi_h-*.whl" | Select-Object -First 1
    if ($null -eq $wheel) {
        throw "The ROI-H wheel build produced no wheel."
    }
    Run {
        & $uv pip install --python (Join-Path $runtime "python.exe") --target (Join-Path $runtime "Lib\site-packages") $wheel.FullName
    } "The locked ROI-H runtime installation failed."

    $launcher = Join-Path $payload "roi-h.exe"
    $launcherObject = Join-Path $work "launcher.obj"
    Run {
        & $cl /nologo /O1 /MT /W4 /DUNICODE /D_UNICODE "/Fo:$launcherObject" (Join-Path $repository "packaging\windows\launcher.c") "/Fe:$launcher" /link /SUBSYSTEM:CONSOLE
    } "The native ROI-H launcher build failed."

    $msixVersion = if (($Version -split '\.').Count -eq 3) { "$Version.0" } else { $Version }
    @{
        schema_version = 1
        active_version = $Version
        browser_revision = $StateBrowserRevision
    } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $payload "install-state.json")
    Set-Content -Encoding utf8 (Join-Path $payload "current") "$Version`n"

    $archiveFile = Get-Item $archive
    $archiveHash = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
    @{
        schema_version = 1
        playwright_version = "1.61.0"
        browser_revision = "chromium-1228"
        platform = "win64"
        archive = $archiveFile.Name
        bytes = $archiveFile.Length
        sha256 = $archiveHash
    } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $payload "browser-target.json")

    $manifest = Get-Content -Raw (Join-Path $repository "packaging\windows\AppxManifest.xml.in")
    $escapedPublisher = [Security.SecurityElement]::Escape($Publisher)
    $manifest = $manifest.Replace("@@PUBLISHER@@", $escapedPublisher).Replace("@@VERSION@@", $msixVersion)
    Set-Content -Encoding utf8 (Join-Path $payload "AppxManifest.xml") $manifest

    Add-Type -AssemblyName System.Drawing
    $assets = Join-Path $payload "Assets"
    New-Item -ItemType Directory -Force -Path $assets | Out-Null
    foreach ($logo in @(
        @{ Name = "StoreLogo.png"; Size = 50 },
        @{ Name = "Square44x44Logo.png"; Size = 44 },
        @{ Name = "Square150x150Logo.png"; Size = 150 }
    )) {
        $bitmap = [System.Drawing.Bitmap]::new($logo.Size, $logo.Size)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try { $graphics.Clear([System.Drawing.Color]::FromArgb(24, 76, 112)) }
            finally { $graphics.Dispose() }
            $bitmap.Save((Join-Path $assets $logo.Name), [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally { $bitmap.Dispose() }
    }

    $package = Join-Path $output "roi-h_${Version}_x64.msix"
    Run { & $makeAppx pack /d $payload /p $package /o } "MakeAppx could not create the MSIX."

    $certificateFile = $null
    if ($CreateTestCertificate) {
        $certificate = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Publisher -CertStoreLocation "Cert:\CurrentUser\My" -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 -KeyExportPolicy Exportable -NotAfter (Get-Date).AddDays(30)
        $CertificateThumbprint = $certificate.Thumbprint
        $certificateFile = Join-Path $output "roi-h-prototype.cer"
        Export-Certificate -Cert $certificate -FilePath $certificateFile | Out-Null
    }
    Run { & $signTool sign /fd SHA256 /sha1 $CertificateThumbprint $package } "SignTool could not sign the MSIX."

    if ([string]::IsNullOrWhiteSpace($PackageUri)) {
        $PackageUri = "https://example.invalid/roi-h/$([System.IO.Path]::GetFileName($package))"
    }
    if ([string]::IsNullOrWhiteSpace($AppInstallerUri)) {
        $AppInstallerUri = ([uri]::new([uri]$PackageUri, "roi-h.appinstaller")).AbsoluteUri
    }
    $escapedPackageUri = [Security.SecurityElement]::Escape($PackageUri)
    $escapedAppInstallerUri = [Security.SecurityElement]::Escape($AppInstallerUri)
    $appInstaller = Join-Path $output "roi-h_${Version}.appinstaller"
    @"
<?xml version="1.0" encoding="utf-8"?>
<AppInstaller Uri="$escapedAppInstallerUri" Version="$msixVersion" xmlns="http://schemas.microsoft.com/appx/appinstaller/2021">
  <MainPackage Name="ROI-H.Prototype" Publisher="$escapedPublisher" Version="$msixVersion" ProcessorArchitecture="x64" Uri="$escapedPackageUri" />
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="0" ShowPrompt="true" UpdateBlocksActivation="false" />
    <ForceUpdateFromAnyVersion>true</ForceUpdateFromAnyVersion>
  </UpdateSettings>
</AppInstaller>
"@.Trim() | Set-Content -Encoding utf8 $appInstaller
    Run { & $signTool sign /fd SHA256 /sha1 $CertificateThumbprint $appInstaller } "SignTool could not sign the App Installer descriptor."

    $packageHash = (Get-FileHash -Algorithm SHA256 $package).Hash.ToLowerInvariant()
    Set-Content -Encoding ascii "$packageHash  $([System.IO.Path]::GetFileName($package))" "$package.sha256"
    @{
        ok = $true
        package = $package
        appinstaller = $appInstaller
        certificate = $certificateFile
        certificate_thumbprint = $CertificateThumbprint
        sha256 = $packageHash
        browser_sha256 = $archiveHash
        browser_bytes = $archiveFile.Length
    } | ConvertTo-Json -Compress
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
