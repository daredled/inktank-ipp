# Setup that needs administrator rights. Run from an elevated PowerShell or cmd:
#
#   powershell -ExecutionPolicy Bypass -File setup-admin.ps1 [-Port 8631] [-AddLocalPrinter]
#
# 1. Removes the "Query User" rules that block uv's python.exe
#    (Windows creates them when the firewall prompt is dismissed or denied).
# 2. Allows the IPP port (TCP) and mDNS (UDP 5353) on private networks.
#    The rules are per port rather than per program, because the python.exe
#    path changes with every Python version.
# 3. Optional: adds an IPP printer on this PC pointing at the server.
param(
    [int]$Port = 8631,
    [switch]$AddLocalPrinter,
    [string]$PrinterName = "HP Ink Tank 315 (inktank)"
)
$ErrorActionPreference = "Stop"

Get-NetFirewallRule -Direction Inbound |
    Where-Object { $_.Action -eq "Block" -and $_.Name -like "*Query User*" } |
    Where-Object { ($_ | Get-NetFirewallApplicationFilter).Program -like "*\uv\python\*python.exe" } |
    ForEach-Object { Write-Output "removing block rule: $($_.Name)"; $_ | Remove-NetFirewallRule }

foreach ($rule in @(
    @{ Name = "inktank-ipp"; DisplayName = "inktank IPP (TCP $Port)"; Protocol = "TCP"; LocalPort = $Port },
    @{ Name = "inktank-mdns"; DisplayName = "inktank mDNS (UDP 5353)"; Protocol = "UDP"; LocalPort = 5353 }
)) {
    Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule @rule -Direction Inbound -Action Allow -Profile Private | Out-Null
    Write-Output "rule created: $($rule.DisplayName), private networks only"
}

if ($AddLocalPrinter) {
    if (-not (Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue)) {
        Add-Printer -Name $PrinterName -IppURL "http://localhost:$Port/ipp/print"
    }
    Get-Printer -Name $PrinterName | Format-List Name, DriverName, PortName
}
