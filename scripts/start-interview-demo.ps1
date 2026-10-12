param(
    [Parameter(Mandatory = $true)]
    [string]$LanIp
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repoRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Create the project .env file before starting the interview demo.'
}

$parsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($LanIp, [ref]$parsedAddress) -or
    $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'LanIp must be the recruiter computer Wi-Fi IPv4 address.'
}

function Set-DotEnvValue([string]$name, [string]$value) {
    $lines = @(Get-Content -LiteralPath $envPath)
    $pattern = '^' + [regex]::Escape($name) + '='
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match $pattern) {
            if (-not $found) { "$name=$value" }
            $found = $true
        } else { $line }
    }
    if (-not $found) { $updated += "$name=$value" }
    [System.IO.File]::WriteAllLines($envPath, [string[]]$updated,
        [System.Text.UTF8Encoding]::new($false))
}

Push-Location $repoRoot
try {
    Set-DotEnvValue 'LAN_HOST' $LanIp
    docker compose --profile interview-tunnel up --build -d db api web livekit interview-tunnel
    if ($LASTEXITCODE -ne 0) { throw 'Docker could not start the interview services.' }

    $publicUrl = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $logs = docker compose --profile interview-tunnel logs --no-color --tail 80 interview-tunnel 2>&1
        $matches = [regex]::Matches(($logs -join "`n"),
            'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($matches.Count -gt 0) {
            $publicUrl = $matches[$matches.Count - 1].Value
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $publicUrl) {
        throw 'The temporary HTTPS address did not appear. Read: docker compose --profile interview-tunnel logs interview-tunnel'
    }

    $rtcUrl = ($publicUrl -replace '^https:', 'wss:') + '/livekit'
    Set-DotEnvValue 'WEB_BASE_URL' $publicUrl
    Set-DotEnvValue 'LIVEKIT_PUBLIC_URL' $rtcUrl
    docker compose up -d --force-recreate api
    if ($LASTEXITCODE -ne 0) { throw 'The API could not restart with the interview URL.' }

    Write-Host ''
    Write-Host "Recruiter address: $publicUrl/interviews"
    Write-Host 'Open that address before scheduling or resending an invitation.'
    Write-Host "LiveKit signaling: $rtcUrl"
    Write-Host 'Keep Docker running. If the tunnel restarts, run this script again and resend invitations.'
} finally {
    Pop-Location
}
