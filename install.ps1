# Project Vigil Installer Script (install.ps1)
# Installs Project Vigil to Local AppData, registers Start Menu/Desktop shortcuts, and sets up Windows Uninstall integration.

$ErrorActionPreference = "Stop"

$app_name = "Project Vigil"
$app_id = "ProjectVigil"
$source_dir = Join-Path $PSScriptRoot "dist\ProjectVigil"
$install_dir = Join-Path $env:LOCALAPPDATA "Programs\ProjectVigil"
$exe_path = Join-Path $install_dir "ProjectVigil.exe"

Write-Host "=== Project Vigil Installation Setup ===" -ForegroundColor Cyan

# 1. Verify compilation source exists
if (-not (Test-Path $source_dir)) {
    Write-Error "Source directory not found at: $source_dir. Please run PyInstaller build first."
}

# 2. Stop any existing companion processes to prevent lock conflicts
Write-Host "Checking for running instances of $app_id..." -ForegroundColor Yellow
$running_processes = Get-Process -Name $app_id -ErrorAction SilentlyContinue
if ($running_processes) {
    Write-Host "Stopping running instances..." -ForegroundColor Yellow
    Stop-Process -Name $app_id -Force
    Start-Sleep -Seconds 2
}

# 3. Copy application files to Local AppData Programs folder
Write-Host "Copying files to: $install_dir..." -ForegroundColor Yellow
if (Test-Path $install_dir) {
    Remove-Item -Path $install_dir -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $install_dir | Out-Null
Copy-Item -Path "$source_dir\*" -Destination $install_dir -Recurse -Force

# 4. Create Desktop & Start Menu Shortcuts
Write-Host "Creating shortcuts..." -ForegroundColor Yellow
$shell = New-Object -ComObject WScript.Shell

# Desktop Shortcut
$desktop_lnk = Join-Path $env:USERPROFILE "Desktop\Project Vigil.lnk"
$shortcut = $shell.CreateShortcut($desktop_lnk)
$shortcut.TargetPath = $exe_path
$shortcut.WorkingDirectory = $install_dir
$shortcut.IconLocation = "$exe_path,0"
$shortcut.Description = "Project Vigil Autonomous Outbound Companion Gateway"
$shortcut.Save()

# Start Menu Shortcut
$startmenu_dir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$startmenu_lnk = Join-Path $startmenu_dir "Project Vigil.lnk"
$shortcut = $shell.CreateShortcut($startmenu_lnk)
$shortcut.TargetPath = $exe_path
$shortcut.WorkingDirectory = $install_dir
$shortcut.IconLocation = "$exe_path,0"
$shortcut.Description = "Project Vigil Autonomous Outbound Companion Gateway"
$shortcut.Save()

# 5. Write Registry Add/Remove Programs (Uninstall) Entry
Write-Host "Registering Uninstall entry in Windows Settings..." -ForegroundColor Yellow
$uninstall_reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ProjectVigil"
New-Item -Path $uninstall_reg -Force | Out-Null
New-ItemProperty -Path $uninstall_reg -Name "DisplayName" -Value $app_name -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstall_reg -Name "DisplayIcon" -Value $exe_path -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstall_reg -Name "Publisher" -Value "Antigravity AI" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstall_reg -Name "DisplayVersion" -Value "1.0.0" -PropertyType String -Force | Out-Null

# Native uninstallation PowerShell command block registered as a string
$uninstall_command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command `"& {Stop-Process -Name '$app_id' -Force -ErrorAction SilentlyContinue; Remove-Item -Path '$desktop_lnk' -Force -ErrorAction SilentlyContinue; Remove-Item -Path '$startmenu_lnk' -Force -ErrorAction SilentlyContinue; Remove-Item -Path '$install_dir' -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item -Path '$uninstall_reg' -Force -ErrorAction SilentlyContinue; Write-Host 'Project Vigil was uninstalled successfully!' -ForegroundColor Green; Start-Sleep -Seconds 2}`""
New-ItemProperty -Path $uninstall_reg -Name "UninstallString" -Value $uninstall_command -PropertyType String -Force | Out-Null

Write-Host "=== Installation Completed Successfully! ===" -ForegroundColor Green
Write-Host "You can launch Project Vigil via your Desktop or Start Menu shortcuts." -ForegroundColor Gray
