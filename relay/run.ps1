$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $ProjectDir ".env"

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Сначала скопируй .env.example в .env и заполни токены девяти ботов и TELEGRAM_CHAT_ID."
}

Get-Content -Encoding UTF8 -LiteralPath $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $parts = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

$RelayPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $RelayPython)) {
    $RelayPython = Join-Path $ProjectDir ".venv312\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $RelayPython)) { throw "Create .venv or .venv312 first." }
& $RelayPython -u (Join-Path $ProjectDir "service.py")

