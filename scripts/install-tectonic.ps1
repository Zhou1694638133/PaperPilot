$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $projectRoot 'tools\tectonic'
$version = '0.17.0'
$url = 'https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip'
$sha256 = 'f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$archive = Join-Path $destination 'release.zip'
Invoke-WebRequest -Uri $url -OutFile $archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha256) {
    throw 'Tectonic checksum mismatch. Installation stopped.'
}
Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
@{version=$version;url=$url;sha256=$sha256} | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath (Join-Path $destination 'release.json')
& (Join-Path $destination 'tectonic.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'Tectonic installation failed.' }
Remove-Item -LiteralPath $archive
Write-Host 'Project-local Tectonic is ready. First compilation downloads the required TeX packages.'
