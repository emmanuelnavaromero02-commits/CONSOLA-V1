#Requires -RunAsAdministrator
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
    [ValidateSet('Service', 'Task')][string]$Mode = 'Service',
    [string]$ServiceName = 'OmegaSapB1Agent',
    [string]$WinswExe = '',
    [string]$WinswSha256 = '',
    [int]$IntervalMinutes = 120,
    [pscredential]$Credential = $null,
    [string]$TaskName = 'OMEGA SAP B1 Agent',
    [int]$IntervalHours = 2,
    [switch]$NoTask
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step([string]$Text) { Write-Host "==> $Text" }

if (-not $NoTask -and $Mode -eq 'Service') {
    if (-not $WinswExe -or -not (Test-Path $WinswExe)) { throw 'Indique -WinswExe con la ruta de WinSW-x64.exe (version 2.12).' }
    if (-not $WinswSha256) { throw 'Indique -WinswSha256; WinSW no se instala sin verificar su hash.' }
    $winswHash = (Get-FileHash -Algorithm SHA256 -Path $WinswExe).Hash
    if ($winswHash.ToUpperInvariant() -ne $WinswSha256.ToUpperInvariant()) { throw 'WinSW no coincide con el SHA-256 esperado; no se instala.' }
    if ($IntervalMinutes -lt 15 -or $IntervalMinutes -gt 1440) { throw '-IntervalMinutes debe estar entre 15 y 1440.' }
}

$aclAccount = $ServiceAccount
if ($aclAccount.StartsWith('.\')) { $aclAccount = "$env:COMPUTERNAME\" + $aclAccount.Substring(2) }
$sidSystem = '*S-1-5-18'
$sidAdministrators = '*S-1-5-32-544'

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
    'agent.toml.template', 'iam-policy.template.json'
)
foreach ($relative in $cartridgeFiles) {
    if (-not (Test-Path (Join-Path $cartridgeRoot $relative))) { throw "Falta $relative en $cartridgeRoot" }
}
foreach ($relative in $agentFiles) {
    if (-not (Test-Path (Join-Path $sourceDir $relative))) { throw "Falta $relative en $sourceDir" }
}

function Test-MachineWidePython([string]$Exe) {
    $resolved = (Resolve-Path -LiteralPath $Exe).Path
    if ($resolved -match '\\WindowsApps\\') { return $false }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:SystemDrive 'Python')) | Where-Object { $_ }
    foreach ($root in $roots) {
        if ($resolved.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Get-PythonExe {
    $probe = 'import sys; print(sys.executable) if sys.version_info >= (3, 11) else sys.exit(1)'
    $machineWide = Get-ChildItem -Path $env:ProgramFiles -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
        Sort-Object -Property Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' } | Where-Object { Test-Path $_ }
    foreach ($candidate in $machineWide) {
        $out = & $candidate -c $probe 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return "$out".Trim() }
    }
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

& icacls $InstallRoot /grant:r "${aclAccount}:(OI)(CI)RX" | Out-Null

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
& icacls $DataRoot /inheritance:r /grant:r "${sidSystem}:(OI)(CI)F" "${sidAdministrators}:(OI)(CI)F" "${aclAccount}:(OI)(CI)M" | Out-Null
& icacls $configFile /inheritance:r /grant:r "${sidSystem}:F" "${sidAdministrators}:F" "${aclAccount}:R" | Out-Null

function Get-ServiceCredential {
    if ($Gmsa) { return $null }
    $value = $Credential
    if (-not $value) {
        $value = Get-Credential -UserName $ServiceAccount `
            -Message "Contrasena de $ServiceAccount, solo para registrar el agente (no se guarda en disco)"
    }
    if (-not $value) { throw 'Se necesita la credencial de la cuenta de servicio.' }
    return $value
}

function Grant-ServiceLogonRight([string]$Account) {
    $sid = (New-Object System.Security.Principal.NTAccount($Account)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    $work = Join-Path $env:TEMP ('omega-secedit-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        $exported = Join-Path $work 'export.inf'
        & secedit /export /cfg $exported /areas USER_RIGHTS | Out-Null
        $line = Get-Content -Path $exported | Where-Object { $_ -like 'SeServiceLogonRight*' } | Select-Object -First 1
        $current = if ($line) { ($line -split '=', 2)[1].Trim() } else { '' }
        if (($current -split ',') -contains "*$sid") { return }
        $value = if ($current) { "$current,*$sid" } else { "*$sid" }
        $template = Join-Path $work 'grant.inf'
        @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]',
          "SeServiceLogonRight = $value") | Set-Content -Path $template -Encoding Unicode
        & secedit /configure /db (Join-Path $work 'grant.sdb') /cfg $template /areas USER_RIGHTS | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "No se pudo otorgar 'Iniciar sesion como servicio' a $Account." }
    } finally {
        Remove-Item -Recurse -Force -Path $work -ErrorAction SilentlyContinue
    }
}

function ConvertTo-XmlText([string]$Value) { return [System.Security.SecurityElement]::Escape($Value) }

function Install-AgentService {
    $serviceDir = Join-Path $InstallRoot 'service'
    New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null
    $wrapper = Join-Path $serviceDir "$ServiceName.exe"
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Write-Step "Deteniendo y quitando el servicio $ServiceName anterior"
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        if (Test-Path $wrapper) { & $wrapper uninstall | Out-Null } else { & sc.exe delete $ServiceName | Out-Null }
        Start-Sleep -Seconds 2
    }
    Copy-Item -Force -Path $WinswExe -Destination $wrapper
    $agentPy = Join-Path $agentDir 'agent.py'
    $arguments = "`"$agentPy`" --config `"$configFile`" --quiet serve --interval-minutes $IntervalMinutes"
    $xml = @(
        '<service>',
        "  <id>$(ConvertTo-XmlText $ServiceName)</id>",
        "  <name>OMEGA SAP B1 Agent ($(ConvertTo-XmlText $ServiceName))</name>",
        '  <description>OMEGA push agent for SAP Business One: reads the company schemas and uploads Bronze files.</description>',
        "  <executable>$(ConvertTo-XmlText $venvPython)</executable>",
        "  <arguments>$(ConvertTo-XmlText $arguments)</arguments>",
        "  <workingdirectory>$(ConvertTo-XmlText $DataRoot)</workingdirectory>",
        '  <startmode>Automatic</startmode>',
        '  <delayedAutoStart>true</delayedAutoStart>',
        '  <onfailure action="restart" delay="60 sec"/>',
        '  <onfailure action="restart" delay="300 sec"/>',
        '  <resetfailure>1 hour</resetfailure>',
        '  <stoptimeout>120 sec</stoptimeout>',
        "  <logpath>$(ConvertTo-XmlText (Join-Path $DataRoot 'logs'))</logpath>",
        '  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>',
        '</service>'
    )
    Set-Content -Path (Join-Path $serviceDir "$ServiceName.xml") -Value $xml -Encoding UTF8
    Write-Step "Registrando el servicio $ServiceName (cada $IntervalMinutes min) como $ServiceAccount"
    & $wrapper install | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "WinSW no pudo registrar el servicio $ServiceName." }

    $serviceCredential = Get-ServiceCredential
    $secret = if ($serviceCredential) { $serviceCredential.GetNetworkCredential().Password } else { $null }
    try {
        $service = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'"
        $result = Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{ StartName = $ServiceAccount; StartPassword = $secret }
        if ($result.ReturnValue -ne 0) { throw "No se pudo asignar la cuenta $ServiceAccount al servicio (codigo $($result.ReturnValue))." }
    } finally {
        $secret = $null
        $serviceCredential = $null
    }
    Grant-ServiceLogonRight $aclAccount
    $startName = (Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'").StartName
    $expected = @($ServiceAccount, $aclAccount) | ForEach-Object { $_.ToUpperInvariant() }
    if ($expected -notcontains "$startName".ToUpperInvariant()) { throw "El servicio quedo con la cuenta $startName en lugar de $ServiceAccount." }

    if (Select-String -Path $configFile -Pattern '<[A-Z][A-Z0-9_]*>' -CaseSensitive -Quiet) {
        Write-Host "    El servicio queda detenido: complete $configFile y luego ejecute Start-Service $ServiceName"
    } else {
        Start-Service -Name $ServiceName
        Write-Host "    Servicio $ServiceName iniciado."
    }
}

function Register-AgentTask {
    Write-Step "Registrando la tarea programada '$TaskName' (cada $IntervalHours h) como $ServiceAccount"
    $runScript = Join-Path $agentDir 'run.ps1'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runScript`" extract-all -DataRoot `"$DataRoot`"" `
        -WorkingDirectory $DataRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date).Date.AddMinutes(5)
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
        -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12)
    if ($Gmsa) {
        $principal = New-ScheduledTaskPrincipal -UserId $ServiceAccount -LogonType Password -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Force | Out-Null
        return
    }
    $taskCredential = Get-ServiceCredential
    $secret = $taskCredential.GetNetworkCredential().Password
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -User $taskCredential.UserName -Password $secret -RunLevel Limited -Force | Out-Null
    } finally {
        $secret = $null
        $taskCredential = $null
    }
}

if (-not $NoTask) {
    if ($Mode -eq 'Service') { Install-AgentService } else { Register-AgentTask }
}

Write-Host ''
Write-Host 'Instalacion terminada. Siguientes pasos:'
Write-Host "  1. Edite $configFile (tenant, workspace, HANA, empresas, bucket y claves)."
Write-Host "  2. Pruebe:  & `"$(Join-Path $agentDir 'run.ps1')`" test-connection"
Write-Host "  3. Inventario (conteos por empresa):  & `"$(Join-Path $agentDir 'run.ps1')`" inventory"
Write-Host "  4. Carga inicial de 24 meses (una vez):  & `"$(Join-Path $agentDir 'run.ps1')`" initial-load"
Write-Host "  5. Estado en cualquier momento:  & `"$(Join-Path $agentDir 'run.ps1')`" status"
Write-Host "  Registros: $(Join-Path $DataRoot 'logs\agent.log')"
