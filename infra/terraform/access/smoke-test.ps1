param(
  [Parameter(Mandatory=$true)] [string] $AppIp,
  [Parameter(Mandatory=$true)] [string] $PemPath,
  [Parameter(Mandatory=$false)][string] $User = "ubuntu"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PemPath)) {
  Write-Host "[X] No se encuentra la llave: $PemPath" -ForegroundColor Red
  exit 1
}

$services = @(
  [pscustomobject]@{ Servicio = "console";    Puerto = 8000; Path = "/"        }
  [pscustomobject]@{ Servicio = "superset";   Puerto = 8088; Path = "/health"  }
  [pscustomobject]@{ Servicio = "airflow";    Puerto = 8082; Path = "/health"  }
  [pscustomobject]@{ Servicio = "rag";        Puerto = 8600; Path = "/health"  }
  [pscustomobject]@{ Servicio = "refinement"; Puerto = 8500; Path = "/health"  }
  [pscustomobject]@{ Servicio = "mcp-infra";  Puerto = 8010; Path = "/health"  }
)

$sshBase = @(
  "-i", $PemPath,
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "UserKnownHostsFile=$HOME\.ssh\known_hosts.modecissions",
  "-o", "ConnectTimeout=5",
  "$User@$AppIp"
)

$remoteScript = @()
foreach ($s in $services) {
  $url = "http://localhost:$($s.Puerto)$($s.Path)"
  $remoteScript += "echo -n '$($s.Servicio)|'; curl -s -o /dev/null -m 5 -w '%{http_code}' '$url' 2>/dev/null || echo -n 'ERR'; echo"
}
$remoteCmd = $remoteScript -join ' ; '

Write-Host "Ejecutando smoke tests vía SSH a $AppIp..." -ForegroundColor Cyan
$raw = & ssh @sshBase $remoteCmd
if ($LASTEXITCODE -ne 0) {
  Write-Host "[X] SSH falló (código $LASTEXITCODE). ¿Está la VPN activa?" -ForegroundColor Red
  exit 1
}

$results = @{}
foreach ($line in ($raw -split "`n")) {
  $line = $line.Trim()
  if (-not $line) { continue }
  $parts = $line -split '\|'
  if ($parts.Length -eq 2) {
    $results[$parts[0]] = $parts[1].Trim()
  }
}

$ok = 0
$rows = foreach ($s in $services) {
  $code = $results[$s.Servicio]
  $url  = "http://localhost:$($s.Puerto)$($s.Path)"
  $isOk = $code -match '^(2|3)\d\d$'
  if ($isOk) { $ok++ }
  [pscustomobject]@{
    Servicio = $s.Servicio
    Puerto   = $s.Puerto
    URL      = $url
    HTTP     = if ($code) { $code } else { "?" }
    Status   = if ($isOk) { "[OK]" } else { "[X]" }
  }
}

$rows | Format-Table -AutoSize

Write-Host ""
$total = $services.Count
if ($ok -eq $total) {
  Write-Host "$ok/$total servicios operativos" -ForegroundColor Green
  exit 0
} else {
  Write-Host "$ok/$total servicios operativos" -ForegroundColor Yellow
  exit 1
}
