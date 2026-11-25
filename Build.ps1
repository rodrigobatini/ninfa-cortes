param(
    [Parameter(Mandatory)][string]$PortableDir
)

$ErrorActionPreference = 'Stop'
$portable = (Resolve-Path -LiteralPath $PortableDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $portable 'runtime/python/pythonw.exe'))) {
    throw 'Pass the path to an extracted Ninfa Cortes release.'
}
$lock = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
if ($lock | Where-Object Port -eq 8771) { throw 'Close Ninfa Cortes before rebuilding the desktop app.' }

$version = '1.0.4370.26'
$cache = Join-Path $PSScriptRoot ".build/webview2-$version"
$core = Join-Path $cache 'lib/net462/Microsoft.Web.WebView2.Core.dll'
$forms = Join-Path $cache 'lib/net462/Microsoft.Web.WebView2.WinForms.dll'
if (-not (Test-Path -LiteralPath $forms)) {
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    $archive = Join-Path $cache 'webview2.zip'
    $uri = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/microsoft.web.webview2.$version.nupkg"
    Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $cache -Force
}

$source = Join-Path $PSScriptRoot 'windows'
$exe = Join-Path $cache 'Ninfa Cortes.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /main:DesktopWindow `
    "/win32icon:$source\Ninfa.ico" "/out:$exe" /reference:System.Windows.Forms.dll `
    /reference:System.Drawing.dll "/reference:$core" "/reference:$forms" `
    "$source\DesktopWindow.cs" "$source\PortableLauncher.cs"
if ($LASTEXITCODE -ne 0) { throw 'The desktop build failed.' }

Copy-Item -LiteralPath $exe,$core,$forms,(Join-Path $cache 'runtimes/win-x64/native/WebView2Loader.dll') -Destination $portable -Force
Copy-Item -LiteralPath (Join-Path $cache 'LICENSE.txt') -Destination (Join-Path $portable 'WebView2-LICENSE.txt') -Force
Copy-Item -LiteralPath (Join-Path $cache 'NOTICE.txt') -Destination (Join-Path $portable 'WebView2-NOTICE.txt') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app') -Destination $portable -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'engine') -Destination (Join-Path $portable 'runtime') -Recurse -Force
