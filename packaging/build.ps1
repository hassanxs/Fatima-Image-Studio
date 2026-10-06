# Builds the Windows installer: dist\FatimaImageStudio-Setup-<version>.exe (+ .sha256).
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Needs: Python 3.13 with pip and Pillow on PATH (to fetch packages and draw the icon), and Inno Setup 6
# (ISCC.exe). The installer ships this repo's source files as they are, plus the official embeddable Python
# and the exact packages in packaging\requirements-lock.txt. Engines, models and upscalers are not bundled;
# the app downloads them on its Setup and Models pages.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$PyVersion = "3.13.16"
$PySha256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"

$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Root "build"
$Stage = Join-Path $Build "app"
$Dist = Join-Path $Root "dist"
$Cache = Join-Path $Build "cache"

$Version = (Select-String -Path "$Root\studio\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Fatima Image Studio $Version"

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force $Stage, $Dist, $Cache | Out-Null

# 1. The app's own files, exactly as in the repo.
robocopy "$Root\studio" "$Stage\studio" /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copying studio failed" }
foreach ($f in "studio_mcp.py", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md") { Copy-Item "$Root\$f" $Stage }
Set-Content "$Stage\installed" "Marks an installed copy: data lives in %LOCALAPPDATA%\Fatima Image Studio." -Encoding ascii

# 2. Official embeddable Python, checked against its SHA-256.
$zip = Join-Path $Cache "python-$PyVersion-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile $zip
}
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $PySha256) { throw "Python download hash mismatch: $hash" }
Expand-Archive $zip "$Stage\python" -Force
# Let it see site-packages and the app folder (the ._pth file replaces the usual sys.path rules).
$pth = Get-ChildItem "$Stage\python\python*._pth" | Select-Object -First 1
$tag = $pth.BaseName
Set-Content $pth.FullName "$tag.zip`r`n.`r`nLib\site-packages`r`n..`r`nimport site" -Encoding ascii

# 3. Pinned packages, as wheels for this Python and platform only.
$site = "$Stage\python\Lib\site-packages"
python -m pip install --disable-pip-version-check --no-warn-script-location --quiet `
    --target $site --no-deps --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp `
    -r "$Root\packaging\requirements-lock.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 4. Trim what never runs: caches, tests, pywin32's IDE and demos, console-script launchers.
Get-ChildItem $site -Recurse -Directory -Include __pycache__, tests, test, Demos, demos |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
foreach ($d in "pythonwin", "bin", "win32\Demos", "win32\test", "win32com\demos", "win32comext\axdebug", "isapi") {
    if (Test-Path "$site\$d") { Remove-Item "$site\$d" -Recurse -Force }
}
Get-ChildItem $site -Recurse -Include *.chm, *.pyi | Remove-Item -Force

# 5. App icon for the installer and shortcuts.
python -c "import sys; sys.path.insert(0, r'$Root'); from studio.autostart import icon_image; icon_image().save(r'$Stage\app.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE -ne 0) { throw "icon failed" }

# 6. Smoke test: the bundled Python can import the whole app.
& "$Stage\python\python.exe" -c "import studio.app, studio.tray, studio.mcp_server, win32api; print('imports ok')"
if ($LASTEXITCODE -ne 0) { throw "the bundled runtime can't import the app" }

$size = (Get-ChildItem $Stage -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("Staged {0:N1} MB in {1}" -f ($size / 1MB), $Stage)

# 7. Installer.
$iscc = @("$env:ProgramFiles (x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found" }
& $iscc /Q "/DAppVersion=$Version" "/DSourceDir=$Stage" "/DOutputDir=$Dist" "$Root\packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$exe = Join-Path $Dist "FatimaImageStudio-Setup-$Version.exe"
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
Set-Content "$exe.sha256" "$sha  $(Split-Path -Leaf $exe)" -Encoding ascii
Write-Host ("Built {0} ({1:N1} MB)`nSHA-256 {2}" -f $exe, ((Get-Item $exe).Length / 1MB), $sha)
