param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutPath
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

function Await-WinRT($asyncOp, [Type]$resultType) {
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq "AsTask" -and
            $_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1
        } |
        Where-Object {
            $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
        } |
        Select-Object -First 1
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($asyncOp))
    $task.Wait(-1) | Out-Null
    return $task.Result
}

function Write-Utf8Json([string]$Path, $Object) {
    $json = $Object | ConvertTo-Json -Compress -Depth 6
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $json, $utf8)
}

[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    Write-Utf8Json $OutPath ([ordered]@{ ok = $false; error = "无法创建 OCR 引擎，请确认已安装中文语言包" })
    exit 1
}

$full = (Resolve-Path -LiteralPath $ImagePath).Path
$file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
$stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$lines = @()
foreach ($line in $result.Lines) {
    $minX = [double]::PositiveInfinity
    $minY = [double]::PositiveInfinity
    $maxX = 0.0
    $maxY = 0.0
    $got = $false
    foreach ($word in $line.Words) {
        $r = $word.BoundingRect
        if ($null -eq $r) { continue }
        $got = $true
        if ($r.X -lt $minX) { $minX = $r.X }
        if ($r.Y -lt $minY) { $minY = $r.Y }
        if (($r.X + $r.Width) -gt $maxX) { $maxX = $r.X + $r.Width }
        if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
    }
    if (-not $got) {
        $r = $line.BoundingRect
        if ($null -ne $r) {
            $minX = $r.X; $minY = $r.Y; $maxX = $r.X + $r.Width; $maxY = $r.Y + $r.Height
        } else {
            $minX = 0; $minY = 0; $maxX = 0; $maxY = 0
        }
    }
    $lines += [ordered]@{
        text = $line.Text
        x    = [math]::Round($minX, 1)
        y    = [math]::Round($minY, 1)
        w    = [math]::Round(($maxX - $minX), 1)
        h    = [math]::Round(($maxY - $minY), 1)
    }
}

$payload = [ordered]@{
    ok     = $true
    width  = $decoder.PixelWidth
    height = $decoder.PixelHeight
    text   = $result.Text
    lines  = $lines
}

Write-Utf8Json $OutPath $payload
