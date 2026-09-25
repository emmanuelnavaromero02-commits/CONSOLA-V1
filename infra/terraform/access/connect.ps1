param(
  [Parameter(Mandatory=$true)] [string] $AppIp,
  [Parameter(Mandatory=$false)][string] $VpnIp = "",
  [Parameter(Mandatory=$false)][string] $TunnelName = ""
)

$ErrorActionPreference = "Stop"

function Write-Ok   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2{ param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "  [X]  $msg" -ForegroundColor Red }

$services = Get-Service -Name "WireGuardTunnel*" -ErrorAction SilentlyContinue
if (-not $services) {
  Write-Err "No hay tunnels WireGuard instalados. Corre primero vpn-setup.ps1."
  exit 1
}

if ($TunnelName) {
  $svc = $services | Where-Object { $_.Name -eq "WireGuardTunnel`$$TunnelName" } | Select-Object -First 1
  if (-not $svc) {
    Write-Err "Tunnel '$TunnelName' no encontrado. Disponibles:"
    $services | ForEach-Object { Write-Host "    - $($_.Name -replace '^WireGuardTunnel\$','')" }
    exit 1
  }
} else {
  $svc = $services | Select-Object -First 1
}

$displayName = $svc.Name -replace '^WireGuardTunnel\$',''
Write-Host "Tunnel: $displayName" -ForegroundColor Cyan

if ($svc.Status -ne 'Running') {
  Start-Service -Name $svc.Name
  Start-Sleep -Seconds 3
  Write-Ok "Tunnel activado"
} else {
  Write-Ok "Tunnel ya estaba activo"
}

$reachable = $false
try {
  $reachable = Test-Connection -ComputerName $AppIp -Count 2 -Quiet -ErrorAction Stop
} catch { $reachable = $false }

if (-not $reachable) {
  Write-Err "No hay conectividad a $AppIp"
  Write-Host "    Stop-Service '$($svc.Name)' ; Start-Service '$($svc.Name)'" -ForegroundColor Yellow
  exit 1
}

Write-Ok "Conectividad a $AppIp OK"
Write-Host ""
Write-Host "URLs de acceso:" -ForegroundColor Cyan
$rows = @(
  [pscustomobject]@{ Servicio = "Console";    URL = "http://${AppIp}:8000" }
  [pscustomobject]@{ Servicio = "Superset";   URL = "http://${AppIp}:8088" }
  [pscustomobject]@{ Servicio = "Airflow";    URL = "http://${AppIp}:8082" }
  [pscustomobject]@{ Servicio = "Refinement"; URL = "http://${AppIp}:8500" }
  [pscustomobject]@{ Servicio = "RAG";        URL = "http://${AppIp}:8600" }
  [pscustomobject]@{ Servicio = "MCP-Infra";  URL = "http://${AppIp}:8010" }
)
if ($VpnIp) {
  $rows += [pscustomobject]@{ Servicio = "wg-easy"; URL = "http://${VpnIp}:51821" }
}
$rows | Format-Table -AutoSize
