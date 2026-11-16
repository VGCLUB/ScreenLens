param([switch]$NativeOverlayCheck, [switch]$VideoCheck)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    uv venv --python 3.12 .venv
    uv pip install --python $taskPython -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
}
uv pip install --python $taskPython -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
& $taskPython prepare_video_assets.py
if ($LASTEXITCODE -ne 0) { throw '准备视频组件失败' }
$appVersion = (& $taskPython -c 'from screenlens import __version__; print(__version__)').Trim()
$distRoot = Join-Path $PSScriptRoot ('dist\' + $appVersion)
$bundleDirectory = Join-Path $distRoot 'ScreenLens'
& $taskPython -m pytest -q --junitxml=qa\test-results.xml
if ($LASTEXITCODE -ne 0) { throw '测试失败，停止打包' }
& $taskPython -m PyInstaller --noconfirm --distpath $distRoot ScreenLens.spec
if ($LASTEXITCODE -ne 0) { throw '打包失败' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '使用说明.txt') -Destination (Join-Path $bundleDirectory '使用说明.txt') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $bundleDirectory 'README.md') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $bundleDirectory 'THIRD_PARTY_NOTICES.md') -Force
$licenseDestination = Join-Path $bundleDirectory 'licenses'
New-Item -ItemType Directory -Force -Path $licenseDestination | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot 'licenses\*') -Destination $licenseDestination -Recurse -Force
$selfCheckPath = Join-Path $PSScriptRoot 'qa\packaged-self-test.json'
$selfCheckExe = Join-Path $bundleDirectory 'ScreenLens.exe'
$selfCheckProcess = Start-Process -FilePath $selfCheckExe -ArgumentList @('--self-test', ('"' + $selfCheckPath + '"')) -WindowStyle Hidden -PassThru -Wait
if ($selfCheckProcess.ExitCode -ne 0) { throw '发行包自检失败，请查看 qa\packaged-self-test.json' }
if ($NativeOverlayCheck) {
    $nativeCheckPath = Join-Path $PSScriptRoot 'qa\packaged-native-overlay-self-test.json'
    $nativeCheckProcess = Start-Process -FilePath $selfCheckExe -ArgumentList @('--overlay-self-test', ('"' + $nativeCheckPath + '"')) -WindowStyle Hidden -PassThru -Wait
    if ($nativeCheckProcess.ExitCode -ne 0) { throw '发行包原位覆盖自检失败' }
}
if ($VideoCheck) {
    $videoCheckPath = Join-Path $PSScriptRoot 'qa\packaged-video-self-test.json'
    $videoCheckProcess = Start-Process -FilePath $selfCheckExe -ArgumentList @('--video-self-test', ('"' + $videoCheckPath + '"')) -WindowStyle Hidden -PassThru -Wait
    if ($videoCheckProcess.ExitCode -ne 0) { throw '发行包视频译制自检失败' }
}
Write-Host ('打包完成：' + (Join-Path $bundleDirectory 'ScreenLens.exe'))
