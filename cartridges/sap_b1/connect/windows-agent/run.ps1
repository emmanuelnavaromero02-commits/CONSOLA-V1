[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Command = 'extract-all',
    [string]$DataRoot = (Join-Path $env:ProgramData 'OmegaSapB1Agent'),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$AgentArgs = @()
)

$ErrorActionPreference = 'Stop'

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
