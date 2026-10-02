param(
    [string]$OutputDir = (Join-Path ([IO.Path]::GetTempPath()) 'wheres-my-meme-windows')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Build the Windows executable on Windows with Python 3.12 x64.' }
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
$TaskPython = (Get-Command python -ErrorAction Stop).Source

function Invoke-TaskPython([string[]]$TaskArguments) {
    & $TaskPython @TaskArguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
}

# resolve() also detects existing junctions leading back into the checkout.
Invoke-TaskPython @('-c', 'import pathlib,sys; r=pathlib.Path(sys.argv[1]).resolve(); o=pathlib.Path(sys.argv[2]).resolve(); assert not o.is_relative_to(r), "OutputDir must be outside the source tree"; assert sys.version_info[:2]==(3,12) and sys.maxsize>2**32, "Python 3.12 x64 required"', $RepoRoot, $OutputDir)
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PIP_CACHE_DIR = Join-Path $OutputDir 'pip-cache'
$env:PYTHONPATH = Join-Path $RepoRoot 'desktop'
$Verification = Join-Path $OutputDir 'verification'
$Release = Join-Path $OutputDir 'release'
$Resources = Join-Path $OutputDir 'resources'
$Work = Join-Path $OutputDir 'work'
$Venv = Join-Path $OutputDir 'venv'
foreach ($Directory in @($Verification, $Release, $Resources, $Work)) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
}
$env:COVERAGE_FILE = Join-Path $Verification '.coverage'
Invoke-TaskPython @('-m', 'venv', $Venv)
$TaskPython = Join-Path $Venv 'Scripts/python.exe'
Invoke-TaskPython @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $RepoRoot 'desktop/requirements.txt'))
$AppVersion = (Invoke-TaskPython @('-c', 'from memeocr import __version__; print(__version__)')).Trim()

$OldLocation = Get-Location
try {
    Set-Location $OutputDir
    Invoke-TaskPython @('-m', 'pytest', (Join-Path $RepoRoot 'desktop/tests'), '-p', 'no:cacheprovider',
        '--cov=memeocr.core', '--cov=memeocr.storage', '--cov=memeocr.images',
        "--cov-report=json:$(Join-Path $Verification 'coverage.json')", '--cov-report=term-missing',
        "--junitxml=$(Join-Path $Verification 'tests.xml')")
    Invoke-TaskPython @((Join-Path $RepoRoot 'desktop/build_support.py'), $Resources)
    $ExeName = "WhereIsMyMeme-$AppVersion-windows-x64"
    $PackArguments = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', $ExeName, '--distpath', $Release, '--workpath', $Work, '--specpath', $Work,
        '--paths', (Join-Path $RepoRoot 'desktop'), '--collect-data', 'rapidocr_onnxruntime',
        '--collect-all', 'onnxruntime', '--collect-data', 'cv2', '--hidden-import', 're2',
        '--add-data', "$(Join-Path $RepoRoot 'desktop/memeocr/assets');memeocr/assets",
        '--add-data', "$(Join-Path $Resources 'licenses');licenses",
        '--add-data', "$(Join-Path $Resources 'THIRD_PARTY_NOTICES.txt');.",
        '--add-data', "$(Join-Path $Resources 'build-info.json');.",
        '--exclude-module', 'pytest', '--exclude-module', 'IPython', '--exclude-module', 'matplotlib',
        '--exclude-module', 'torch', '--exclude-module', 'tensorflow')
    # Put the redistributable VC runtime next to the executable's native modules.
    $SitePackages = Join-Path $Venv 'Lib/site-packages'
    $QtDirectory = Join-Path $SitePackages 'PySide6'
    foreach ($Pattern in @('msvcp140*.dll', 'vcruntime140*.dll')) {
        foreach ($Library in Get-ChildItem $QtDirectory -Filter $Pattern) {
            $PackArguments += @('--add-binary', "$($Library.FullName);.")
        }
    }
    $Re2Libraries = Join-Path $SitePackages 'google_re2.libs'
    if (Test-Path $Re2Libraries) {
        $PackArguments += @('--add-binary', "$Re2Libraries/*.dll;google_re2.libs")
    }
    $PackArguments += (Join-Path $RepoRoot 'desktop/launcher.py')
    Invoke-TaskPython $PackArguments
    $Executable = Join-Path $Release "$ExeName.exe"
    $Report = Join-Path $Verification 'frozen-selftest.json'
    Remove-Item $Report -Force -ErrorAction SilentlyContinue
    # Test the Windows platform plugin and real Windows clipboard.
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    $Process = Start-Process -FilePath $Executable -ArgumentList @('--self-test', "`"$Report`"") -PassThru
    if (-not $Process.WaitForExit(180000)) {
        $Process.Kill()
        throw 'Frozen executable self-test exceeded three minutes.'
    }
    if ($Process.ExitCode -ne 0 -or -not (Test-Path $Report)) {
        throw "Frozen executable self-test failed with exit code $($Process.ExitCode)"
    }
    $Result = Get-Content $Report -Raw | ConvertFrom-Json
    if (-not $Result.success -or -not $Result.frozen -or $Result.platform -ne 'win32' -or $Result.qt_platform -ne 'windows' -or $Result.version -ne $AppVersion) {
        throw 'Frozen executable verification did not pass on the native Windows platform.'
    }
    $Digest = (Get-FileHash $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $Release 'SHA256SUMS'), "$Digest  $ExeName.exe`n", [Text.UTF8Encoding]::new($false))
    Write-Output "Verified executable: $Executable"
    Write-Output "SHA256: $Digest"
    if ($env:GITHUB_OUTPUT) {
        Add-Content -Path $env:GITHUB_OUTPUT -Value "version=$AppVersion" -Encoding utf8
    }
} finally {
    Set-Location $OldLocation
}
