#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'OmegaSapB1Agent'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'OmegaSapB1Agent'),
    [string]$TaskName = 'OMEGA SAP B1 Agent',
    [string]$ServiceName = 'OmegaSapB1Agent',
    [switch]$PurgeData
)

$ErrorActionPreference = 'Stop'

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "==> Eliminando la tarea programada '$TaskName'"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$serviceDir = Join-Path $InstallRoot 'service'
$wrapper = Join-Path $serviceDir "$ServiceName.exe"
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "==> Deteniendo y eliminando el servicio $ServiceName"
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    if (Test-Path $wrapper) { & $wrapper uninstall | Out-Null } else { & sc.exe delete $ServiceName | Out-Null }
}
foreach ($leftover in @($wrapper, (Join-Path $serviceDir "$ServiceName.xml"))) {
    if (Test-Path $leftover) { Remove-Item -Force -Path $leftover }
}

$otherServices = @()
if (Test-Path $serviceDir) { $otherServices = @(Get-ChildItem -Path $serviceDir -Filter '*.xml' -File) }
if ($otherServices.Count -gt 0) {
    Write-Host "==> Se conserva $InstallRoot: lo usan otros servicios ($($otherServices.BaseName -join ', '))."
} elseif (Test-Path $InstallRoot) {
    Write-Host "==> Eliminando el codigo en $InstallRoot"
    Remove-Item -Recurse -Force -Path $InstallRoot
}

if (Test-Path $DataRoot) {
    $spool = Join-Path $DataRoot 'spool'
    $pending = 0
    if (Test-Path $spool) {
        $pending = @(Get-ChildItem -Path $spool -Recurse -File -Filter '*.parquet' -ErrorAction SilentlyContinue).Count
    }
    if ($PurgeData) {
        if ($pending -gt 0) {
            Write-Warning "La cola local tiene $pending archivo(s) que no llegaron al lakehouse; se borran por -PurgeData."
        }
        Write-Host "==> Eliminando el directorio de datos $DataRoot"
        Remove-Item -Recurse -Force -Path $DataRoot
    } else {
        Write-Host "==> Se conserva $DataRoot (configuracion, estado, cola con $pending archivo(s) pendiente(s), registros)."
        Write-Host '    Borrelo a mano cuando ya no haga falta, o vuelva a ejecutar con -PurgeData.'
    }
}

Write-Host 'Desinstalacion terminada.'
