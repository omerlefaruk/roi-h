[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$uvVersion = "0.11.16"
$uvInstallerSha256 = "a885d46d3105506fdabc1febd2673313968605c8434e17e5841750cb20b28989"
$pythonVersion = "3.12.13"
$defaultInstallerVersion = "0.1.0"
$defaultReleaseBundleUrl = "https://get.roi-h.dev/releases/stable/roi-h-release-0.1.0.tar.gz"
$defaultReleaseBundleSha256 = "ce2ea82cae5e43ee526ac5a437193f2c562877023699c3e860f6e56940c4cf40"

function Stop-Install {
    param([Parameter(Mandatory = $true)][string]$Message)

    throw "ROI-H install failed: $Message"
}

$installerVersion = if (
    [string]::IsNullOrWhiteSpace($env:ROI_H_INSTALLER_VERSION)
) {
    $defaultInstallerVersion
} else {
    $env:ROI_H_INSTALLER_VERSION
}
$releaseBundleUrl = if (
    [string]::IsNullOrWhiteSpace($env:ROI_H_RELEASE_BUNDLE_URL)
) {
    $defaultReleaseBundleUrl
} else {
    $env:ROI_H_RELEASE_BUNDLE_URL
}
$releaseBundleSha256 = if (
    [string]::IsNullOrWhiteSpace($env:ROI_H_RELEASE_BUNDLE_SHA256)
) {
    $defaultReleaseBundleSha256
} else {
    $env:ROI_H_RELEASE_BUNDLE_SHA256
}

if ($installerVersion -notmatch "^[0-9A-Za-z.+-]+$") {
    Stop-Install "ROI_H_INSTALLER_VERSION is not a valid exact version."
}
$parsedReleaseBundleUrl = $null
if (
    -not [Uri]::TryCreate(
        $releaseBundleUrl,
        [UriKind]::Absolute,
        [ref]$parsedReleaseBundleUrl
    ) -or $parsedReleaseBundleUrl.Scheme -ne "https"
) {
    Stop-Install "ROI_H_RELEASE_BUNDLE_URL must use HTTPS."
}
if (
    [string]::IsNullOrWhiteSpace($releaseBundleSha256) -or
    $releaseBundleSha256 -notmatch "^[0-9A-Fa-f]{64}$"
) {
    Stop-Install "ROI_H_RELEASE_BUNDLE_SHA256 must contain 64 hexadecimal characters."
}
$releaseBundleSha256 = $releaseBundleSha256.ToLowerInvariant()

$installRoot = if (-not [string]::IsNullOrWhiteSpace($env:ROI_H_INSTALL_ROOT)) {
    $env:ROI_H_INSTALL_ROOT
} else {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Stop-Install "LOCALAPPDATA is not set."
    }
    Join-Path $env:LOCALAPPDATA "ROI-H"
}
$dataHome = if (-not [string]::IsNullOrWhiteSpace($env:ROI_H_HOME)) {
    $env:ROI_H_HOME
} else {
    Join-Path $HOME ".roi-h"
}

$temporaryRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ("roi-h-install-" + [Guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $temporaryRoot

$managedEnvironmentNames = @(
    "UV_UNMANAGED_INSTALL",
    "UV_NO_MODIFY_PATH",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "UV_CACHE_DIR"
)
$savedEnvironment = @{}
foreach ($name in $managedEnvironmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    $uvInstallerPath = Join-Path $temporaryRoot "uv-installer.ps1"
    $releaseBundlePath = Join-Path $temporaryRoot "release-bundle.tar.gz"
    $releaseRoot = Join-Path $temporaryRoot "release"
    $releaseDescriptionPath = Join-Path $releaseRoot "release.json"
    $uvRoot = Join-Path $installRoot "bootstrap"
    $installerRoot = Join-Path (
        Join-Path $installRoot "installer\versions"
    ) $installerVersion
    $installerBinRoot = Join-Path $installerRoot "bin"

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "https://astral.sh/uv/$uvVersion/install.ps1" `
        -OutFile $uvInstallerPath
    $actualUvInstallerSha256 = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $uvInstallerPath
    ).Hash.ToLowerInvariant()
    if ($actualUvInstallerSha256 -ne $uvInstallerSha256) {
        Stop-Install "The uv installer checksum is invalid."
    }

    Invoke-WebRequest `
        -UseBasicParsing `
        -Uri $releaseBundleUrl `
        -OutFile $releaseBundlePath
    $actualReleaseBundleSha256 = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $releaseBundlePath
    ).Hash.ToLowerInvariant()
    if ($actualReleaseBundleSha256 -ne $releaseBundleSha256) {
        Stop-Install "The release bundle checksum is invalid."
    }

    $members = @(& tar -tzf $releaseBundlePath)
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "The release bundle cannot be read."
    }
    $memberDetails = @(& tar -tvzf $releaseBundlePath)
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "The release bundle cannot be inspected."
    }
    if ($members.Count -ne $memberDetails.Count) {
        Stop-Install "The release bundle member list is inconsistent."
    }
    foreach ($detail in $memberDetails) {
        if (-not $detail.StartsWith("-")) {
            Stop-Install "The release bundle contains a non-regular entry."
        }
    }
    if (($members | Group-Object | Where-Object Count -gt 1).Count -ne 0) {
        Stop-Install "The release bundle contains duplicate entries."
    }
    foreach ($member in $members) {
        if (
            $member -ne "release.json" -and
            $member -notmatch "^[A-Za-z0-9][A-Za-z0-9_.-]*\.whl$"
        ) {
            Stop-Install "The release bundle contains an unsafe entry."
        }
    }
    if (($members | Where-Object { $_ -eq "release.json" }).Count -ne 1) {
        Stop-Install "The release bundle must contain one release.json file."
    }
    if (($members | Where-Object { $_ -like "*.whl" }).Count -lt 1) {
        Stop-Install "The release bundle must contain at least one wheel."
    }
    $null = New-Item -ItemType Directory -Path $releaseRoot
    & tar -xzf $releaseBundlePath -C $releaseRoot
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "The release bundle cannot be extracted."
    }

    $null = New-Item -ItemType Directory -Path $uvRoot -Force
    $env:UV_UNMANAGED_INSTALL = $uvRoot
    $env:UV_NO_MODIFY_PATH = "1"
    & $uvInstallerPath
    $uvBinary = Join-Path $uvRoot "uv.exe"
    if (-not (Test-Path -LiteralPath $uvBinary -PathType Leaf)) {
        Stop-Install "The pinned uv installer did not create uv."
    }

    $null = New-Item -ItemType Directory -Path $installerBinRoot -Force
    $env:UV_TOOL_DIR = Join-Path $installerRoot "tool"
    $env:UV_TOOL_BIN_DIR = $installerBinRoot
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $installRoot "python\versions"
    $env:UV_CACHE_DIR = Join-Path $installRoot "cache\uv"
    & $uvBinary `
        --no-config `
        tool install `
        --python $pythonVersion `
        --python-preference only-managed `
        --no-index `
        --find-links $releaseRoot `
        --force `
        "roi-h-installer==$installerVersion"
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "The exact ROI-H installer could not be installed."
    }

    $installerBinary = Join-Path $installerBinRoot "roi-h-installer.exe"
    if (-not (Test-Path -LiteralPath $installerBinary -PathType Leaf)) {
        Stop-Install "The exact ROI-H installer was not installed."
    }
    $installerOperation = if (
        Test-Path -LiteralPath (Join-Path $installRoot "install-state.json") -PathType Leaf
    ) {
        "update"
    } else {
        "install"
    }
    & $installerBinary `
        $installerOperation `
        --release-description $releaseDescriptionPath `
        --install-root $installRoot `
        --data-home $dataHome `
        --output json
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "The ROI-H installer did not complete."
    }

    $updaterHelper = Join-Path $installRoot "installer\update.ps1"
    $updaterContent = @'
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$temporaryScript = Join-Path (
    [IO.Path]::GetTempPath()
) ("roi-h-update-" + [Guid]::NewGuid().ToString("N") + ".ps1")
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "https://get.roi-h.dev/windows" `
        -OutFile $temporaryScript
    & $temporaryScript
    if ($LASTEXITCODE -ne 0) {
        throw "The ROI-H update did not complete."
    }
} finally {
    if (Test-Path -LiteralPath $temporaryScript) {
        Remove-Item -Force -LiteralPath $temporaryScript
    }
}
'@
    Set-Content `
        -LiteralPath $updaterHelper `
        -Encoding UTF8 `
        -Value $updaterContent

    $activeCli = Join-Path $installRoot "current\Scripts\roi-h.exe"
    if (-not (Test-Path -LiteralPath $activeCli -PathType Leaf)) {
        Stop-Install "The active ROI-H command is not executable."
    }
    $binRoot = Join-Path $installRoot "bin"
    $null = New-Item -ItemType Directory -Path $binRoot -Force
    $launcher = Join-Path $binRoot "roi-h.cmd"
    Set-Content `
        -LiteralPath $launcher `
        -Encoding Ascii `
        -NoNewline `
        -Value "@`"$activeCli`" %*`r`n"

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathEntries = @(
        ($userPath -split ";") |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($pathEntries -notcontains $binRoot) {
        $newUserPath = if ([string]::IsNullOrWhiteSpace($userPath)) {
            $binRoot
        } else {
            "$userPath;$binRoot"
        }
        [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
        Write-Warning "Open a new terminal before you run roi-h."
    }
} finally {
    foreach ($name in $managedEnvironmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $savedEnvironment[$name],
            "Process"
        )
    }
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -Recurse -Force -LiteralPath $temporaryRoot
    }
}
