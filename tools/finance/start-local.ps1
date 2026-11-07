[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable,

    [int]$Port = 8096
)

$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be an integer from 1 to 65535.'
}
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw 'PythonExecutable must name an existing Python interpreter. Provide its explicit path; no environment is installed by this launcher.'
}
$resolvedPython = (Resolve-Path -LiteralPath $PythonExecutable).Path

if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot 'frontend/dist/index.html') -PathType Leaf)) {
    throw 'Missing frontend/dist/index.html beside this launcher. Use the built delivery package or build with the existing frontend dependencies first.'
}

$portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $portProbe.Server.ExclusiveAddressUse = $true
    $portProbe.Start()
}
catch [System.Net.Sockets.SocketException] {
    throw "Loopback port $Port is occupied or unavailable. Choose another -Port; no existing process has been stopped."
}
finally {
    $portProbe.Stop()
}

Write-Host "Local desktop: http://127.0.0.1:$Port/#/lab/transaction-review"
Write-Host 'Running in the foreground. Press Ctrl+C to stop this local process.'
& $resolvedPython -m uvicorn tools.finance.local_app:create_app --factory --host 127.0.0.1 --port $Port --app-dir $repositoryRoot
exit $LASTEXITCODE
