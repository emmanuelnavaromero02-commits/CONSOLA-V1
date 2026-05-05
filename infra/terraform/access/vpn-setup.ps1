<#
.SYNOPSIS
  Setup inicial de VPN WireGuard para MODecissions (admin / primer uso).

.PARAMETER VpnIp
  IP pÃºblica de la EC2 VPN. Obtener con: terraform output ec2_vpn_public_ip

.PARAMETER AppIp
  IP privada de la EC2 App. Obtener con: terraform output ec2_app_private_ip

.PARAMETER PemPath
  Ruta al archivo modecissions-key.pem (no se usa aquÃ­ pero se acepta para consistencia con otros scripts).

.EXAMPLE
  .\vpn-setup.ps1 -VpnIp 54.123.45.67 -AppIp 10.0.2.15 -PemPath .\modecissions-key.pem
#>

param(
  [Parameter(Mandatory=$true)] [string] $VpnIp,
  [Parameter(Mandatory=$true)] [string] $AppIp,
  [Parameter(Mandatory=$false)][string] $PemPath = ".\modecissions-key.pem"
)

$ErrorActionPreference = "Stop"

function Write-Step    { param($msg) Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok      { param($msg) Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn2   { param($msg) Write-Host "  [!]  $msg"   -ForegroundColor Yellow }
function Write-Err     { param($msg) Write-Host "  [X]  $msg"   -ForegroundColor Red }

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 1. Verificar WireGuard instalado
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "1/9  Verificando instalaciÃ³n de WireGuard"

$wgExe = "C:\Program Files\WireGuard\wireguard.exe"
if (-not (Test-Path $wgExe)) {
  Write-Err "WireGuard no estÃ¡ instalado en C:\Program Files\WireGuard\"
  Write-Host "    DescÃ¡rgalo de: https://www.wireguard.com/install/" -ForegroundColor Yellow
  exit 1
}
Write-Ok "WireGuard encontrado: $wgExe"

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 2. Abrir wg-easy en el navegador
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "2/9  Abriendo wg-easy en el navegador"
$wgEasyUrl = "http://${VpnIp}:51821"
Write-Host "  URL: $wgEasyUrl"
Start-Process $wgEasyUrl

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 3. Instrucciones manuales en wg-easy
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "3/9  Crea tu peer en wg-easy"
Write-Host "  1. Login con password: M4n4g3rWG27*"
Write-Host "  2. Click en '+ New Client'"
Write-Host "  3. Nombre: laptop-<tuNombre>  (ej: laptop-rodolfo)"
Write-Host "  4. Click 'Download' para bajar el .conf"
Write-Host ""
Read-Host "  >>> Presiona ENTER cuando tengas el archivo descargado"

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 4. Localizar el .conf mÃ¡s reciente en Downloads
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "4/9  Buscando el .conf mÃ¡s reciente en $HOME\Downloads"

$conf = Get-ChildItem -Path "$HOME\Downloads\*.conf" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

if (-not $conf) {
  Write-Err "No se encontrÃ³ ningÃºn .conf en $HOME\Downloads"
  Write-Host "    Verifica que el archivo se haya descargado." -ForegroundColor Yellow
  exit 1
}
Write-Ok "Encontrado: $($conf.Name)  ($([math]::Round($conf.Length/1KB,1)) KB)"

$tunnelName = [System.IO.Path]::GetFileNameWithoutExtension($conf.Name)
Write-Host "  Tunnel name: $tunnelName"

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 5. Importar el tunnel como servicio
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "5/9  Instalando tunnel como servicio Windows"

# Necesita admin para /installtunnelservice
$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent() `
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
  Write-Warn2 "Se requieren privilegios de administrador para instalar el tunnel."
  Write-Host  "    Reabre PowerShell como Administrador y vuelve a correr este script." -ForegroundColor Yellow
  Write-Host  "    Alternativa: importar manualmente desde la app WireGuard ($($conf.FullName))." -ForegroundColor Yellow
  exit 1
}

& $wgExe /installtunnelservice $conf.FullName
if ($LASTEXITCODE -ne 0) {
  Write-Err "FallÃ³ la instalaciÃ³n del tunnel (exit $LASTEXITCODE)"
  exit 1
}
Write-Ok "Tunnel instalado: WireGuardTunnel`$$tunnelName"

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 6. Activar el tunnel
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "6/9  Activando el tunnel"

$svc = "WireGuardTunnel`$$tunnelName"
try {
  Start-Service -Name $svc -ErrorAction Stop
  Write-Ok "Servicio iniciado: $svc"
} catch {
  # Fallback a 'net start' si Start-Service no encuentra el servicio
  & net start $svc | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Err "No se pudo iniciar el tunnel. Activa manualmente desde la app WireGuard."
    exit 1
  }
  Write-Ok "Tunnel activado via 'net start'"
}

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 7. Esperar a que el handshake se complete
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "7/9  Esperando handshake (5s)"
Start-Sleep -Seconds 5

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 8. Test de conectividad a la EC2 App
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "8/9  Probando conectividad a EC2 App ($AppIp)"

$reachable = $false
try {
  $ping = Test-Connection -ComputerName $AppIp -Count 2 -Quiet -ErrorAction Stop
  $reachable = $ping
} catch {
  $reachable = $false
}

# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# 9. Resultado y guÃ­a
# â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Write-Step "9/9  Resultado"

if ($reachable) {
  Write-Ok "VPN OPERATIVA - EC2 App responde"
  Write-Host ""
  Write-Host "URLs de acceso (sÃ³lo con VPN activa):" -ForegroundColor Cyan
  $rows = @(
    [pscustomobject]@{ Servicio = "Console";     URL = "http://${AppIp}:8000"  }
    [pscustomobject]@{ Servicio = "Superset";    URL = "http://${AppIp}:8088"  }
    [pscustomobject]@{ Servicio = "Airflow";     URL = "http://${AppIp}:8082"  }
    [pscustomobject]@{ Servicio = "Refinement";  URL = "http://${AppIp}:8500"  }
    [pscustomobject]@{ Servicio = "RAG";         URL = "http://${AppIp}:8600"  }
    [pscustomobject]@{ Servicio = "MCP-Infra";   URL = "http://${AppIp}:8010"  }
    [pscustomobject]@{ Servicio = "wg-easy";     URL = "http://${VpnIp}:51821" }
  )
  $rows | Format-Table -AutoSize
} else {
  Write-Err "VPN NO conecta a $AppIp"
  Write-Host ""
  Write-Host "Troubleshooting:" -ForegroundColor Yellow
  Write-Host "  1. Verifica que el tunnel estÃ© activo:"
  Write-Host "     Get-Service 'WireGuardTunnel*' | Where-Object Status -eq Running"
  Write-Host "  2. Revisa el handshake en la app WireGuard (debe ser menor 3 min)."
  Write-Host "  3. Confirma que el security group de EC2 App permite trÃ¡fico desde 10.8.0.0/24."
  Write-Host "  4. Confirma que la EC2 App estÃ¡ running:"
  Write-Host "     aws ec2 describe-instances --filters Name=tag:Name,Values=modecissions-app"
  Write-Host "  5. Reinicia el tunnel:"
  $svcName = "WireGuardTunnel`$$tunnelName"
  Write-Host "     Stop-Service '$svcName' ; Start-Service '$svcName'"
  exit 1
}

