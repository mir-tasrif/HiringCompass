param(
    [string]$LanIp
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repoRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Create the project .env file before starting the interview demo.'
}

function Get-DotEnvValue([string]$name) {
    $pattern = '^' + [regex]::Escape($name) + '=(.*)$'
    $value = ''
    foreach ($line in (Get-Content -LiteralPath $envPath)) {
        if ($line -match $pattern) { $value = $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $value
}

$livekitUrl = Get-DotEnvValue 'LIVEKIT_URL'
$cloudLiveKit = $livekitUrl -match '^wss://[^/]+\.livekit\.cloud/?$'
if (-not $cloudLiveKit) {
    $parsedAddress = $null
    if (-not [System.Net.IPAddress]::TryParse($LanIp, [ref]$parsedAddress) -or
        $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'For local LiveKit, supply the recruiter computer Wi-Fi IPv4 address with -LanIp.'
    }
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
    if (-not $cloudLiveKit) { Set-DotEnvValue 'LAN_HOST' $LanIp }
    if ($cloudLiveKit) {
        docker compose build api
        if ($LASTEXITCODE -ne 0) { throw 'Docker could not build the API for LiveKit verification.' }
        docker compose run --rm --no-deps --entrypoint python api -m app.services.livekit_preflight
        if ($LASTEXITCODE -ne 0) { throw 'LiveKit Cloud verification failed. No invitation address was changed.' }
        docker compose --profile interview-tunnel up --build -d db web interview-tunnel
    } else {
        docker compose --profile interview-tunnel up --build -d db web livekit interview-tunnel
    }
    if ($LASTEXITCODE -ne 0) { throw 'Docker could not start the interview services.' }
    if ((Get-DotEnvValue 'STT_PROVIDER') -eq 'assemblyai') {
        if (-not $cloudLiveKit) {
            docker compose build api
            if ($LASTEXITCODE -ne 0) { throw 'Docker could not build the API for AssemblyAI verification.' }
        }
        docker compose run --rm --no-deps --entrypoint python api -m app.services.assemblyai_preflight
        if ($LASTEXITCODE -ne 0) { throw 'AssemblyAI verification failed. No invitation address was changed.' }
    }

    # A new Quick Tunnel gets a new hostname. Recreate only cloudflared so its log cannot
    # accidentally supply a hostname from an earlier run.
    docker compose --profile interview-tunnel up -d --force-recreate interview-tunnel
    if ($LASTEXITCODE -ne 0) { throw 'Docker could not create a fresh Cloudflare tunnel.' }

    $publicUrl = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $logs = docker compose --profile interview-tunnel logs --no-color --since 2m interview-tunnel 2>&1
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

    $tunnelReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $publicUrl -TimeoutSec 8 -UseBasicParsing
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                $tunnelReady = $true
                break
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $tunnelReady) { throw 'Cloudflare assigned a hostname, but the public app did not become reachable.' }

    $rtcUrl = if ($cloudLiveKit) { $livekitUrl } else { ($publicUrl -replace '^https:', 'wss:') + '/livekit' }
    Set-DotEnvValue 'WEB_BASE_URL' $publicUrl
    Set-DotEnvValue 'LIVEKIT_PUBLIC_URL' $rtcUrl
    docker compose --profile interview-tunnel up --build -d --force-recreate api worker
    if ($LASTEXITCODE -ne 0) { throw 'The API could not restart with the interview URL.' }

    $apiReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 5
            if ($health.status -eq 'ok') { $apiReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $apiReady) { throw 'The API did not become healthy after starting with the new tunnel address.' }

    Write-Host ''
    Write-Host "Recruiter address: $publicUrl/interviews"
    Write-Host 'The API is using the new address. Pending emailed invitations are refreshed automatically.'
    Write-Host "LiveKit signaling: $rtcUrl"
    Write-Host 'Keep Docker running. Run this command whenever you start the interview demo:'
    if ($cloudLiveKit) { Write-Host '.\scripts\start-interview-demo.ps1' }
    else { Write-Host ".\scripts\start-interview-demo.ps1 -LanIp $LanIp" }
} finally {
    Pop-Location
}
