#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Instala el agente OMEGA para SAP Business One (conector Windows).

.DESCRIPTION
    1. Localiza un Python 3.11+ instalado para todos los usuarios (bajo
       Archivos de programa o C:\Python3xx) o lo instala desde el instalador
       oficial de python.org (o desde uno ya descargado, -PythonInstaller).
       Un Python instalado por usuario se ignora.
    2. Copia el agente y los modulos del cartucho a -InstallRoot y crea un
       entorno virtual con requirements.txt.
    3. Crea -DataRoot (agent.toml, estado SQLite, cola local y registros) y
       restringe sus permisos a la cuenta de servicio y a los administradores.
    4. Registra una tarea programada que ejecuta run.ps1 cada -IntervalHours
       horas con la cuenta de servicio.

    Este script nunca pide ni guarda la contrasena de HANA ni las claves de
    S3: esas se escriben en agent.toml (protegido por ACL) despues de instalar.
    La unica credencial que pide es la de la cuenta de servicio, de forma
    interactiva, solo para registrar la tarea programada; no se escribe en
    disco.

.PARAMETER ServiceAccount
    Cuenta que ejecuta la tarea: '.\svc-omega-b1', 'DOMINIO\svc-omega-b1' o
    una cuenta de servicio administrada (gMSA) 'DOMINIO\gmsa-omega$' con -Gmsa.

.PARAMETER PythonInstaller
    Ruta a un instalador oficial python-<version>-amd64.exe ya descargado
    (servidores sin salida a internet). Si se omite, se descarga de python.org.

.PARAMETER PythonSha256
    SHA-256 esperado del instalador (publicado en python.org). Recomendado.

.PARAMETER WheelDir
    Carpeta con las ruedas (wheels) de requirements.txt para instalar sin
    acceso a PyPI (pip download -r requirements.txt -d <carpeta> en otra maquina).

.EXAMPLE
    .\install.ps1 -ServiceAccount 'DOMINIO\svc-omega-b1' -PythonSha256 <hash>
.EXAMPLE
    .\install.ps1 -ServiceAccount 'DOMINIO\gmsa-omega$' -Gmsa -PythonInstaller C:\temp\python-3.12.10-amd64.exe -WheelDir C:\temp\wheels
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServiceAccount,
    [switch]$Gmsa,
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'OmegaSapB1Agent'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'OmegaSapB1Agent'),
    [string]$PythonVersion = '3.12.10',
    [string]$PythonInstaller = '',
    [string]$PythonSha256 = '',
    [string]$WheelDir = '',
    [string]$TaskName = 'OMEGA SAP B1 Agent',
    [int]$IntervalHours = 2,
    [switch]$NoTask
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step([string]$Text) { Write-Host "==> $Text" }

# icacls quiere COMPUTADORA\usuario para cuentas locales escritas como .\usuario
$aclAccount = $ServiceAccount
if ($aclAccount.StartsWith('.\')) { $aclAccount = "$env:COMPUTERNAME\" + $aclAccount.Substring(2) }
$sidSystem = '*S-1-5-18'
$sidAdministrators = '*S-1-5-32-544'

# ---------------------------------------------------------------------------
# 1. Que se instala
# ---------------------------------------------------------------------------
$sourceDir = $PSScriptRoot
$cartridgeRoot = $null
foreach ($candidate in @((Join-Path $sourceDir '..\..'), (Join-Path $sourceDir 'cartridge'))) {
    if (Test-Path (Join-Path $candidate 'app\services\b1_reader.py')) {
        $cartridgeRoot = (Resolve-Path $candidate).Path
        break
    }
}
if (-not $cartridgeRoot) {
    throw "No se encuentra la carpeta app\ del cartucho junto a este script (se esperaba en ..\.. o en .\cartridge)."
}

# Modulos del cartucho que el agente reutiliza: catalogo, planes y SQL,
# lector por empresa, formato de los archivos, mapa intercompania. Nada mas
# del cartucho se copia. Esta lista es la misma que CARTRIDGE_FILES en
# agent.py (una prueba las compara y comprueba que cubre todos los imports).
$cartridgeFiles = @(
    'app\__init__.py',
    'app\core\__init__.py',
    'app\core\b1_source.py',
    'app\services\__init__.py',
    'app\services\b1_queries.py',
    'app\services\b1_reader.py',
    'app\services\bronze_parquet.py',
    'app\services\intercompany_mapping.py',
    'app\config\entities.yaml'
)
$agentFiles = @(
    'agent.py', 'run.ps1', 'uninstall.ps1', 'requirements.txt',
    'agent.toml.template', 'iam-policy.template.json', 'README.md'
)
foreach ($relative in $cartridgeFiles) {
    if (-not (Test-Path (Join-Path $cartridgeRoot $relative))) { throw "Falta $relative en $cartridgeRoot" }
}
foreach ($relative in $agentFiles) {
    if (-not (Test-Path (Join-Path $sourceDir $relative))) { throw "Falta $relative en $sourceDir" }
}

# ---------------------------------------------------------------------------
# 2. Python
# ---------------------------------------------------------------------------
function Test-MachineWidePython([string]$Exe) {
    # Solo vale un Python instalado para todos los usuarios (Archivos de
    # programa o C:\Python3xx). Uno instalado por usuario vive bajo un perfil
    # que la cuenta de servicio no puede leer y desaparece con ese perfil.
    $resolved = (Resolve-Path -LiteralPath $Exe).Path
    # El Python de la Microsoft Store vive bajo Archivos de programa\WindowsApps
    # pero se registra por usuario: tampoco sirve.
    if ($resolved -match '\\WindowsApps\\') { return $false }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:SystemDrive 'Python')) | Where-Object { $_ }
    foreach ($root in $roots) {
        if ($resolved.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Get-PythonExe {
    $probe = 'import sys; print(sys.executable) if sys.version_info >= (3, 11) else sys.exit(1)'
    $launchers = @(
        @{ Exe = 'py'; Args = @('-3.12') },
        @{ Exe = 'py'; Args = @('-3.13') },
        @{ Exe = 'py'; Args = @('-3.11') },
        @{ Exe = 'python'; Args = @() }
    )
    foreach ($launcher in $launchers) {
        if (-not (Get-Command $launcher.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $out = & $launcher.Exe @($launcher.Args + @('-c', $probe)) 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $candidate = "$out".Trim()
                if (Test-MachineWidePython $candidate) { return $candidate }
                Write-Host "    Se omite $candidate (instalado por usuario, no sirve para la cuenta de servicio)."
            }
        } catch { }
    }
    return $null
}

function Install-Python {
    $installer = $PythonInstaller
    if (-not $installer) {
        $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
        $installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
        Write-Step "Descargando el instalador oficial de Python: $url"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    }
    if (-not (Test-Path $installer)) { throw "No existe el instalador de Python: $installer" }
    if ($PythonSha256) {
        $actual = (Get-FileHash -Algorithm SHA256 -Path $installer).Hash
        if ($actual.ToUpperInvariant() -ne $PythonSha256.ToUpperInvariant()) {
            throw "El instalador de Python no coincide con el SHA-256 esperado; no se instala."
        }
    } else {
        Write-Warning 'No se indico -PythonSha256: el instalador no se verifica contra el hash publicado en python.org.'
    }
    $shortVersion = ($PythonVersion -split '\.')[0..1] -join ''
    $target = Join-Path $env:ProgramFiles "Python$shortVersion"
    Write-Step "Instalando Python $PythonVersion en $target (para todos los usuarios, sin tocar PATH)"
    $arguments = @('/quiet', 'InstallAllUsers=1', 'PrependPath=0', 'Include_test=0', 'Include_doc=0',
                   'Include_launcher=1', 'Include_tcltk=0', "TargetDir=$target")
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "El instalador de Python termino con codigo $($process.ExitCode)" }
    $exe = Join-Path $target 'python.exe'
    if (-not (Test-Path $exe)) { throw "Python no quedo instalado en $exe" }
    return $exe
}

$python = Get-PythonExe
if ($python) {
    Write-Step "Usando el Python ya instalado para todos los usuarios: $python"
} else {
    $python = Install-Python
}

# ---------------------------------------------------------------------------
# 3. Archivos del agente y entorno virtual
# ---------------------------------------------------------------------------
Write-Step "Copiando el agente a $InstallRoot"
$agentDir = Join-Path $InstallRoot 'windows-agent'
New-Item -ItemType Directory -Force -Path $agentDir | Out-Null
foreach ($relative in $agentFiles) {
    Copy-Item -Force -Path (Join-Path $sourceDir $relative) -Destination (Join-Path $agentDir $relative)
}
foreach ($relative in $cartridgeFiles) {
    $destination = Join-Path $InstallRoot $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -Force -Path (Join-Path $cartridgeRoot $relative) -Destination $destination
}

$venv = Join-Path $InstallRoot 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Step "Creando el entorno virtual en $venv"
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
}
Write-Step 'Instalando las dependencias (hdbcli, pyarrow, boto3, pyyaml)'
$pipArgs = @('-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check', '-r', (Join-Path $agentDir 'requirements.txt'))
if ($WheelDir) { $pipArgs += @('--no-index', '--find-links', $WheelDir) }
& $venvPython @pipArgs
if ($LASTEXITCODE -ne 0) { throw 'pip no pudo instalar las dependencias.' }

# La cuenta de servicio solo necesita leer y ejecutar el codigo.
& icacls $InstallRoot /grant:r "${aclAccount}:(OI)(CI)RX" | Out-Null

# ---------------------------------------------------------------------------
# 4. Directorio de datos y permisos
# ---------------------------------------------------------------------------
Write-Step "Preparando el directorio de datos $DataRoot"
foreach ($sub in @('', 'spool', 'logs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot $sub) | Out-Null
}
$configFile = Join-Path $DataRoot 'agent.toml'
if (-not (Test-Path $configFile)) {
    Copy-Item -Path (Join-Path $sourceDir 'agent.toml.template') -Destination $configFile
    Write-Host "    agent.toml creado a partir de la plantilla; complete los valores entre < > antes de la primera ejecucion."
} else {
    Write-Host '    agent.toml ya existe; no se toca.'
}
# SYSTEM y Administradores: control total. Cuenta de servicio: modificar el
# directorio (estado, cola, registros) pero solo leer la configuracion.
& icacls $DataRoot /inheritance:r /grant:r "${sidSystem}:(OI)(CI)F" "${sidAdministrators}:(OI)(CI)F" "${aclAccount}:(OI)(CI)M" | Out-Null
& icacls $configFile /inheritance:r /grant:r "${sidSystem}:F" "${sidAdministrators}:F" "${aclAccount}:R" | Out-Null

# ---------------------------------------------------------------------------
# 5. Tarea programada
# ---------------------------------------------------------------------------
if (-not $NoTask) {
    Write-Step "Registrando la tarea programada '$TaskName' (cada $IntervalHours h) como $ServiceAccount"
    $runScript = Join-Path $agentDir 'run.ps1'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runScript`" extract-all -DataRoot `"$DataRoot`"" `
        -WorkingDirectory $DataRoot
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) -RepetitionDuration ([TimeSpan]::MaxValue)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12)
    if ($Gmsa) {
        $principal = New-ScheduledTaskPrincipal -UserId $ServiceAccount -LogonType Password -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Force | Out-Null
    } else {
        $credential = Get-Credential -UserName $ServiceAccount `
            -Message "Contrasena de $ServiceAccount, solo para registrar la tarea programada (no se guarda en disco)"
        if (-not $credential) { throw 'Se necesita la credencial de la cuenta de servicio para registrar la tarea.' }
        $secret = $credential.GetNetworkCredential().Password
        try {
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
                -User $credential.UserName -Password $secret -RunLevel Limited -Force | Out-Null
        } finally {
            $secret = $null
            $credential = $null
        }
    }
}

Write-Host ''
Write-Host 'Instalacion terminada. Siguientes pasos:'
Write-Host "  1. Edite $configFile (tenant, workspace, HANA, empresas, bucket y claves)."
Write-Host "  2. Pruebe:  & `"$(Join-Path $agentDir 'run.ps1')`" test-connection"
Write-Host "  3. Carga inicial (una vez):  & `"$(Join-Path $agentDir 'run.ps1')`" extract-all --mode full"
Write-Host "  4. Estado en cualquier momento:  & `"$(Join-Path $agentDir 'run.ps1')`" status"
Write-Host "  Registros: $(Join-Path $DataRoot 'logs\agent.log')"
