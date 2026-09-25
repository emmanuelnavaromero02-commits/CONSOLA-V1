param(
  [Parameter(Mandatory=$true)] [string] $AppIp,
  [Parameter(Mandatory=$true)] [string] $PemPath,
  [Parameter(Mandatory=$false)][string] $Cmd  = "",
  [Parameter(Mandatory=$false)][string] $User = "ubuntu"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PemPath)) {
  Write-Host "[X] No se encuentra la llave: $PemPath" -ForegroundColor Red
  exit 1
}

$acl = Get-Acl $PemPath
$openTo = $acl.Access | Where-Object { $_.IdentityReference -match 'Everyone|Users|Authenticated Users' }
if ($openTo) {
  Write-Host "[!] $PemPath tiene permisos demasiado abiertos. ssh los rechazará." -ForegroundColor Yellow
  Write-Host "    Corre:" -ForegroundColor Yellow
  Write-Host "    icacls `"$PemPath`" /inheritance:r /grant:r `"`$($env:USERNAME):R`""
}

$sshArgs = @(
  "-i", $PemPath,
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "UserKnownHostsFile=$HOME\.ssh\known_hosts.modecissions",
  "$User@$AppIp"
)

if ([string]::IsNullOrWhiteSpace($Cmd)) {
  & ssh @sshArgs
} else {
  & ssh @sshArgs $Cmd
}

exit $LASTEXITCODE
