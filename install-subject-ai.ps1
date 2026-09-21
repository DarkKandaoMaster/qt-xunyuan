$ErrorActionPreference = 'Stop'

$bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$python = if (Test-Path -LiteralPath $bundled) {
    $bundled
} else {
    (Get-Command python -ErrorAction Stop).Source
}

& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')

$modelDir = Join-Path $PSScriptRoot 'tools\models'
New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
$prototxt = Join-Path $modelDir 'mobilenet_ssd_deploy.prototxt'
$weights = Join-Path $modelDir 'mobilenet_ssd.caffemodel'
if (-not (Test-Path -LiteralPath $prototxt)) {
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt' -OutFile $prototxt
}
if (-not (Test-Path -LiteralPath $weights)) {
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/mobilenet_iter_73000.caffemodel' -OutFile $weights
}

$expected = @{
    $prototxt = '2D180F723B3109E21F8287F6B3C691390D07B60EED998327CD3259FFA0E50608'
    $weights = '52EED8BE80522C152A17FB56740DE705B79881BDE1A167E0E747310523685FC7'
}
foreach ($item in $expected.GetEnumerator()) {
    $actual = (Get-FileHash -LiteralPath $item.Key -Algorithm SHA256).Hash
    if ($actual -ne $item.Value) {
        throw "主体检测模型校验失败：$($item.Key)"
    }
}

Write-Host '主体检测组件安装完成。'
