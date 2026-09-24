#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Desinstala el agente OMEGA para SAP Business One.

.DESCRIPTION
    Elimina la tarea programada y el codigo (-InstallRoot). El directorio de
    datos (-DataRoot: agent.toml, estado SQLite, cola local y registros) se
    conserva salvo que se indique -PurgeData, porque la cola puede contener
    archivos extraidos que todavia no llegaron al lakehouse.

    No desinstala Python ni elimina la cuenta de servicio.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'OmegaSapB1Agent'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'OmegaSapB1Agent'),
    [string]$TaskName = 'OMEGA SAP B1 Agent',
    [switch]$PurgeData
)

$ErrorActionPreference = 'Stop'

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "==> Eliminando la tarea programada '$TaskName'"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if (Test-Path $InstallRoot) {
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
