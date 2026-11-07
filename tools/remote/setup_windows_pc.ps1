<#
.SYNOPSIS
  Prepare a Windows PC for remote spike runs over SSH through a Cloudflare Tunnel.

.DESCRIPTION
  Run once, in PowerShell "Run as administrator":

    powershell -ExecutionPolicy Bypass -File setup_windows_pc.ps1 -PublicKey "ssh-ed25519 AAAA... mojo-studio-mac"

  What it does:
    1. Installs and starts the built-in OpenSSH Server, key-only login (passwords off),
       PowerShell as the SSH shell, inbound port 22 limited to this machine and the LAN.
    2. Authorises the given public key for this user (admins: administrators_authorized_keys).
    3. Installs Git, Python 3.12, ffmpeg (full build: nvenc/qsv/amf), cloudflared via winget.
    4. Prints the machine's specs (CPU, RAM, GPU, Windows) and the hardware encoders ffmpeg finds.

  It does not create the tunnel: that is one command copied from the Cloudflare dashboard
  (cloudflared service install <token>), typed on this PC by its owner.
#>
param(
    [Parameter(Mandatory = $true)][string]$PublicKey
)

$ErrorActionPreference = "Stop"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in PowerShell opened with 'Run as administrator'."
}
if ($PublicKey -notmatch '^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?$') {
    throw "PublicKey does not look like an OpenSSH public key."
}

Step "OpenSSH Server"
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server*"
if ($cap.State -ne "Installed") { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
Set-Service sshd -StartupType Automatic
Start-Service sshd   # first start creates C:\ProgramData\ssh\sshd_config

$config = "C:\ProgramData\ssh\sshd_config"
$text = Get-Content $config -Raw
foreach ($pair in @(
        @("PasswordAuthentication", "no"),
        @("PubkeyAuthentication", "yes"),
        @("KbdInteractiveAuthentication", "no"))) {
    $name, $value = $pair
    if ($text -match "(?m)^#?\s*$name\s+.*$") {
        $text = $text -replace "(?m)^#?\s*$name\s+.*$", "$name $value"
    } else {
        $text = "$name $value`r`n" + $text
    }
}
Set-Content -Path $config -Value $text -Encoding ascii

New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
    -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null

$rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
if ($rule) {
    # cloudflared connects to localhost:22; nobody outside the LAN needs the port
    $rule | Set-NetFirewallRule -RemoteAddress LocalSubnet
}

Step "Authorised key"
# The script runs elevated, so this account is an administrator: Windows OpenSSH reads
# admin keys only from administrators_authorized_keys (owned by Administrators and SYSTEM).
$keys = "C:\ProgramData\ssh\administrators_authorized_keys"
if (-not (Test-Path $keys) -or -not (Select-String -Path $keys -SimpleMatch $PublicKey -Quiet)) {
    Add-Content -Path $keys -Value $PublicKey -Encoding ascii
}
icacls $keys /inheritance:r /grant "*S-1-5-32-544:F" /grant "SYSTEM:F" | Out-Null
Restart-Service sshd
Write-Host "SSH user: $(whoami)   key file: $keys"

Step "Tools (winget)"
foreach ($id in @("Git.Git", "Python.Python.3.12", "Gyan.FFmpeg", "Cloudflare.cloudflared")) {
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
}

Step "Machine"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors | Format-List
"RAM GB: {0:N1}" -f ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion | Format-List
(Get-CimInstance Win32_OperatingSystem).Caption
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    & ffmpeg -hide_banner -version | Select-Object -First 1
    & ffmpeg -hide_banner -encoders | Select-String "nvenc|qsv|amf"
} else {
    Write-Host "ffmpeg not on PATH yet: open a new PowerShell window and run 'ffmpeg -version'."
}

Write-Host "`nDone. Next: install the Cloudflare tunnel with the command from the dashboard." -ForegroundColor Green
