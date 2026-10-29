<#
.SYNOPSIS
    Builds the full mapping file of Phantom Blade Zero: every native type and every Blueprint class, struct and enum.
.DESCRIPTION
    1. Builds the package reader src\usmap_offline (C#, CUE4Parse; needs the .NET 10 SDK).
    2. Reads every class, struct and enum export of the game's packages into dist\work\packages.jsonl.
    3. src\usmap_offline.py turns them into dist\work\packages.usmap.
    4. src\usmap_merge.py merges the native dump, the live dumps and the package types into
       dist\<usmapName>.usmap (report: dist\<usmapName>.merge.txt).
    5. dist\<folder>_<version>.zip with the mapping file, and dist\SHA256SUMS.txt.
    The native dump is a mapping file of the game's native types (src\usmap_live.py --native-only in the running
    game, or UE4SS's Ctrl+Num6 with DumpUSMAP(false)); live dumps are optional (src\usmap_live.py with the game at
    some place: they add the runtime flags and the transient types). The defaults come from the shared _tools folder
    of the modding base folder ($env:UE5_TOOLS overrides it) and its gamedata\<game>\game.json.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build.ps1 -PaksDir "D:\Games\PBZ\ProjectPBZ\Content\Paks" -AesKey 0x... -Native .\native.usmap -Oodle .\oo2core_9_win64.dll
#>
param(
    # The game's Paks folder. Default: gameDir + paksPath of game.json.
    [string]$PaksDir,
    # The game's AES key (0x...). Default: aesKey of game.json.
    [string]$AesKey,
    # A mapping file with the game's native types. Default: gamedata\<game>\usmap_live\live_native_check.usmap.
    [string]$Native,
    # Live dumps with Blueprint types, merged after the native dump. Default: the other live_*.usmap there.
    [string[]]$Live,
    # Oodle (oo2core_9_win64.dll), for the compressed packages. Default: the one next to retoc in _tools.
    [string]$Oodle,
    # The .NET 10 SDK's dotnet.exe. Default: _tools\dotnet10\dotnet.exe, else dotnet on the PATH.
    [string]$Dotnet
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$mod = Get-Content (Join-Path $root "mod.json") -Raw | ConvertFrom-Json
$src = Join-Path $root "src"
$dist = Join-Path $root "dist"
$work = Join-Path $dist "work"

# The shared tools: $env:UE5_TOOLS, else the first _tools folder above the project (none is fine when every
# input is given as a parameter).
function Find-Tools {
    if ($env:UE5_TOOLS) {
        return $env:UE5_TOOLS
    }
    $dir = Split-Path $root -Parent
    while ($dir) {
        $candidate = Join-Path $dir "_tools"
        if (Test-Path $candidate) {
            return $candidate
        }
        $dir = Split-Path $dir -Parent
    }
    return $null
}

$tools = Find-Tools
$game = $null
if ($tools) {
    $gameJson = Join-Path $tools "gamedata\$($mod.game)\game.json"
    if (Test-Path $gameJson) {
        $game = Get-Content $gameJson -Raw | ConvertFrom-Json
    }
}
$liveDir = if ($tools) { Join-Path $tools "gamedata\$($mod.game)\usmap_live" } else { $null }
if (-not $PaksDir -and $game -and $game.gameDir -and $game.paksPath) {
    $PaksDir = Join-Path $game.gameDir $game.paksPath
}
if (-not $AesKey -and $game) {
    $AesKey = $game.aesKey
}
if (-not $Native -and $liveDir) {
    $Native = Join-Path $liveDir "live_native_check.usmap"
}
if (-not $PSBoundParameters.ContainsKey("Live") -and $liveDir -and (Test-Path $liveDir)) {
    $Live = @(Get-ChildItem $liveDir -Filter "live_*.usmap" | Where-Object { $_.Name -ne "live_native_check.usmap" } |
        Sort-Object Name | ForEach-Object { $_.FullName })
}
if (-not $Oodle -and $tools) {
    $Oodle = Join-Path $tools "retoc\oo2core_9_win64.dll"
}
if (-not $Dotnet) {
    $Dotnet = if ($tools -and (Test-Path (Join-Path $tools "dotnet10\dotnet.exe"))) { Join-Path $tools "dotnet10\dotnet.exe" } else { "dotnet" }
}
foreach ($pair in @(@("PaksDir", $PaksDir), @("Native", $Native), @("Oodle", $Oodle))) {
    if (-not $pair[1] -or -not (Test-Path $pair[1])) {
        throw "-$($pair[0]) is missing or not found ('$($pair[1])'). See Get-Help .\build.ps1."
    }
}
if (-not $AesKey) {
    throw "-AesKey is missing. See Get-Help .\build.ps1."
}

# Python: the venv of the shared tools, else python on the PATH.
$python = if ($tools -and (Test-Path (Join-Path $tools ".venv\Scripts\python.exe"))) { Join-Path $tools ".venv\Scripts\python.exe" } else { "python" }

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Exe failed with exit code $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Force $work | Out-Null

# 1. The package reader. Its caches stay in the shared tools folder when there is one.
if ($tools) {
    $env:DOTNET_CLI_HOME = Join-Path $tools "dotnet_home"
    $env:NUGET_PACKAGES = Join-Path $tools "nuget"
}
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"
$reader = Join-Path $work "reader"
Invoke-Native $Dotnet @("build", (Join-Path $src "usmap_offline\usmap_offline.csproj"), "-c", "Release", "-o", $reader, "-v", "quiet", "-nologo")
if ($Dotnet -ne "dotnet") {
    $env:DOTNET_ROOT = Split-Path $Dotnet -Parent
}

# 2. Every class, struct and enum export of the packages.
$jsonl = Join-Path $work "packages.jsonl"
Invoke-Native (Join-Path $reader "usmap_offline.exe") @($PaksDir, $AesKey, $Native, $Oodle, $jsonl)

# 3. and 4. The package types as a mapping file, then the merge.
$packages = Join-Path $work "packages.usmap"
Invoke-Native $python @("-I", "-B", (Join-Path $src "usmap_offline.py"), $jsonl, $packages)
$out = Join-Path $dist "$($mod.usmapName).usmap"
$liveFiles = @($Live | Where-Object { $_ })
Invoke-Native $python (@("-I", "-B", (Join-Path $src "usmap_merge.py"), $out, $Native) + $liveFiles + @($packages))

# 5. The zip and the checksums.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = Join-Path $dist "$($mod.folder)_$($mod.version).zip"
if (Test-Path $zip) {
    Remove-Item -LiteralPath $zip
}
$archive = [System.IO.Compression.ZipFile]::Open($zip, "Create")
try {
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $out, (Split-Path $out -Leaf))
} finally {
    $archive.Dispose()
}
$sums = foreach ($file in @($out, $zip)) {
    "{0}  {1}" -f (Get-FileHash $file -Algorithm SHA256).Hash.ToLower(), (Split-Path $file -Leaf)
}
[System.IO.File]::WriteAllText((Join-Path $dist "SHA256SUMS.txt"), (($sums -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding $false))
Write-Host "Built $out" -ForegroundColor Green
$sums | ForEach-Object { Write-Host "  $_" }
