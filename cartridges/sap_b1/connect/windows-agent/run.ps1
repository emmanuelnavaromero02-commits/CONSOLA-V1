<#
.SYNOPSIS
    Ejecuta el agente OMEGA para SAP Business One con la configuracion de -DataRoot.

.DESCRIPTION
    La tarea programada llama a este script con 'extract-all'. Un administrador
    puede llamarlo a mano con cualquier comando del agente; los argumentos
    restantes se pasan tal cual a agent.py:

        .\run.ps1 test-connection
        .\run.ps1 status
        .\run.ps1 extract --entity OINV --mode full
        .\run.ps1 extract --entity OINV --from-date 2025-01-01 --to-date 2025-03-31
        .\run.ps1 extract-all --mode full          (carga inicial)

    El codigo de salida es el del agente: 0 correcto, 1 alguna corrida fallo
    (o quedan archivos sin subir en la cola local), 2 error de configuracion.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Command = 'extract-all',
    [string]$DataRoot = (Join-Path $env:ProgramData 'OmegaSapB1Agent'),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$AgentArgs = @()
)

$ErrorActionPreference = 'Stop'

# Instalado: <InstallRoot>\windows-agent\run.ps1 con el venv en <InstallRoot>\venv.
$installRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $installRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = Join-Path $PSScriptRoot 'venv\Scripts\python.exe' }
if (-not (Test-Path $python)) {
    Write-Error "No se encuentra el entorno virtual del agente (venv\Scripts\python.exe) junto a $PSScriptRoot; ejecute install.ps1."
    exit 2
}

$config = Join-Path $DataRoot 'agent.toml'
if (-not (Test-Path $config)) {
    Write-Error "No existe $config; copie agent.toml.template y complete los valores."
    exit 2
}

& $python (Join-Path $PSScriptRoot 'agent.py') --config $config $Command @AgentArgs
exit $LASTEXITCODE
