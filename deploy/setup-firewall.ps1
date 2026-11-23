#Requires -Version 5.1
<#
.SYNOPSIS
    Citadel Level 3.5 host-firewall baseline and IP-forwarding guard (US-104).

.DESCRIPTION
    Run in an elevated Windows PowerShell on each execution PC, passing that PC's role:

      Plant         PC #2  citadel-plant  10.10.0.10                  (Purdue L0-1)
      ControlRoom   PC #3  citadel-hmi    10.10.0.20                  (Purdue L2-3)
      EnterpriseIT  PC #4  citadel-it     192.168.1.244 + 10.10.0.30  (Purdue L4, dual-NIC)

    What it does:
      1. Recreates every rule in the "Citadel" firewall group (idempotent: wipe + recreate).
         Windows Firewall evaluates Block before Allow, so these Block rules also override
         broad Allow rules added by Docker Desktop or FactoryTalk installers.
      2. Disables IPv4 forwarding on this PC's Citadel NIC(s), forces IPEnableRouter = 0,
         turns off Internet Connection Sharing on any adapter, and disables RRAS.
      3. Prints a PASS/FAIL verification report (exit code 1 on any FAIL).

    Docker/WSL/Hyper-V virtual adapters (vEthernet) are reported but deliberately left
    alone: they need forwarding for container networking and do not bridge the physical NICs.

    Full rule matrix and rationale: docs/network.md

.EXAMPLE
    .\deploy\setup-firewall.ps1 -Role EnterpriseIT -WhatIf      # preview, no changes
.EXAMPLE
    .\deploy\setup-firewall.ps1 -Role EnterpriseIT              # apply + verify
.EXAMPLE
    .\deploy\setup-firewall.ps1 -Role Plant -VerifyOnly         # read-only PASS/FAIL report
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Plant', 'ControlRoom', 'EnterpriseIT')]
    [string]$Role,

    [string]$PlantIP       = '10.10.0.10',
    [string]$ControlRoomIP = '10.10.0.20',
    [string]$ItOtIP        = '10.10.0.30',
    [string]$ItCorpIP      = '192.168.1.244',
    [string]$OtSubnet      = '10.10.0.0/24',
    [string]$CorpSubnet    = '192.168.1.0/24',
    [string]$DevIP         = '192.168.1.177',

    [switch]$VerifyOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Group    = 'Citadel'
$TcpipKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters'
$script:Failures = 0

# ------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------

function Write-Check {
    param([string]$Name, [bool]$Pass, [string]$Detail = '')
    if ($Pass) {
        Write-Host ("[PASS] {0} {1}" -f $Name, $Detail) -ForegroundColor Green
    } else {
        Write-Host ("[FAIL] {0} {1}" -f $Name, $Detail) -ForegroundColor Red
        $script:Failures++
    }
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-OtBlockRanges {
    # Every host address in the OT /24 except those PC #4 may talk to (itself + Control Room).
    param([string]$Subnet, [string[]]$Keep)
    $prefix = (($Subnet -split '/')[0]) -replace '\.\d+$', ''
    foreach ($ip in $Keep) {
        if (-not $ip.StartsWith("$prefix.")) { throw "$ip is not inside OT subnet $Subnet" }
    }
    $keepOctets = $Keep | ForEach-Object { [int](($_ -split '\.')[3]) } | Sort-Object -Unique
    $ranges = @()
    $start = 1
    foreach ($k in $keepOctets) {
        if ($k -gt $start) { $ranges += "$prefix.$start-$prefix.$($k - 1)" }
        $start = $k + 1
    }
    if ($start -le 254) { $ranges += "$prefix.$start-$prefix.254" }
    return $ranges
}

function Get-IPEnableRouter {
    $p = Get-ItemProperty -Path $TcpipKey -ErrorAction SilentlyContinue
    if ($p -and ($p.PSObject.Properties.Name -contains 'IPEnableRouter')) { return [int]$p.IPEnableRouter }
    return 0
}

function Get-SharedConnections {
    # Returns names of adapters with Internet Connection Sharing enabled.
    $shared = @()
    try {
        $mgr = New-Object -ComObject HNetCfg.HNetShare
        foreach ($conn in $mgr.EnumEveryConnection) {
            $cfg = $mgr.INetSharingConfigurationForINetConnection.Invoke($conn)
            if ($cfg.SharingEnabled) { $shared += $mgr.NetConnectionProps.Invoke($conn).Name }
        }
    } catch {
        Write-Warning "Could not query Internet Connection Sharing: $($_.Exception.Message)"
    }
    return $shared
}

function Get-CitadelNic {
    # Returns the IPv4 interface holding $IP, or $null.
    param([string]$IP)
    $addr = Get-NetIPAddress -IPAddress $IP -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $addr) { return $null }
    return Get-NetIPInterface -InterfaceIndex $addr.InterfaceIndex -AddressFamily IPv4
}

# ------------------------------------------------------------------------------------------
# Rule definitions (mirrors the matrix in docs/network.md)
# ------------------------------------------------------------------------------------------

function Get-CitadelRules {
    param([string]$Role)
    switch ($Role) {
        'Plant' {
            return @(
                @{ DisplayName = 'Citadel L1 - Allow Modbus TCP 502 from Control Room'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '502'; RemoteAddress = $ControlRoomIP },
                @{ DisplayName = 'Citadel L1 - Allow OPC UA 4840 from Control Room'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '4840'; RemoteAddress = $ControlRoomIP },
                @{ DisplayName = 'Citadel L1 - Allow SSH 22 from Control Room (deploy hop)'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '22'; RemoteAddress = $ControlRoomIP },
                @{ DisplayName = 'Citadel L1 - Allow ping from Control Room'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'ICMPv4'; IcmpType = '8'; RemoteAddress = $ControlRoomIP },
                @{ DisplayName = 'Citadel L1 - BLOCK all inbound from Enterprise IT (L4 never reaches L0-1)'
                   Direction = 'Inbound'; Action = 'Block'; RemoteAddress = $ItOtIP },
                @{ DisplayName = 'Citadel L1 - BLOCK all outbound to Enterprise IT'
                   Direction = 'Outbound'; Action = 'Block'; RemoteAddress = $ItOtIP }
            )
        }
        'ControlRoom' {
            return @(
                @{ DisplayName = 'Citadel L3 - Allow MQTT 1883 from Enterprise IT (L3.5 conduit)'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '1883'; RemoteAddress = $ItOtIP },
                @{ DisplayName = 'Citadel L3 - Allow SSH 22 from Enterprise IT jump-host (deploy)'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '22'; RemoteAddress = $ItOtIP },
                @{ DisplayName = 'Citadel L3 - Allow ping from OT subnet'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'ICMPv4'; IcmpType = '8'; RemoteAddress = $OtSubnet }
            )
        }
        'EnterpriseIT' {
            return @(
                @{ DisplayName = 'Citadel L4 - BLOCK all outbound to Plant Floor (L4 never reaches L0-1)'
                   Direction = 'Outbound'; Action = 'Block'; RemoteAddress = $PlantIP },
                @{ DisplayName = 'Citadel L4 - BLOCK outbound to rest of OT subnet (only Control Room allowed)'
                   Direction = 'Outbound'; Action = 'Block'
                   RemoteAddress = (Get-OtBlockRanges -Subnet $OtSubnet -Keep @($ControlRoomIP, $ItOtIP)) },
                @{ DisplayName = 'Citadel L4 - BLOCK all inbound from OT subnet (OT never initiates into IT)'
                   Direction = 'Inbound'; Action = 'Block'; RemoteAddress = $OtSubnet },
                @{ DisplayName = 'Citadel L4 - Allow Grafana 3000 from corporate LAN'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '3000'; RemoteAddress = $CorpSubnet },
                @{ DisplayName = 'Citadel L4 - Allow Postgres 5432 from Dev workstation'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '5432'; RemoteAddress = $DevIP },
                @{ DisplayName = 'Citadel L4 - Allow ping from corporate LAN'
                   Direction = 'Inbound'; Action = 'Allow'; Protocol = 'ICMPv4'; IcmpType = '8'; RemoteAddress = $CorpSubnet }
            )
        }
    }
}

# ------------------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------------------

Write-Host "Citadel firewall baseline - role: $Role ($env:COMPUTERNAME)" -ForegroundColor Cyan

if (-not $VerifyOnly -and -not $WhatIfPreference -and -not (Test-IsAdmin)) {
    throw 'Run from an elevated PowerShell (Run as Administrator), or add -WhatIf / -VerifyOnly.'
}

$rules  = Get-CitadelRules -Role $Role
$nicIPs = switch ($Role) {
    'Plant'        { @($PlantIP) }
    'ControlRoom'  { @($ControlRoomIP) }
    'EnterpriseIT' { @($ItCorpIP, $ItOtIP) }
}

if (-not $VerifyOnly) {
    # 1. Firewall rules -------------------------------------------------------------------
    $existing = @(Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0 -and
        $PSCmdlet.ShouldProcess("$($existing.Count) existing rule(s) in group '$Group'", 'Remove')) {
        $existing | Remove-NetFirewallRule
    }
    foreach ($rule in $rules) {
        if ($PSCmdlet.ShouldProcess($rule.DisplayName, 'New-NetFirewallRule')) {
            New-NetFirewallRule @rule -Group $Group -Profile Any -Enabled True | Out-Null
        }
    }

    # 2. IP forwarding guards -------------------------------------------------------------
    foreach ($ip in $nicIPs) {
        $nic = Get-CitadelNic -IP $ip
        if (-not $nic) { Write-Warning "No local interface holds $ip - skipping forwarding change."; continue }
        if ($nic.Forwarding -ne 'Disabled' -and
            $PSCmdlet.ShouldProcess("$($nic.InterfaceAlias) ($ip)", 'Disable IPv4 forwarding')) {
            Set-NetIPInterface -InterfaceIndex $nic.InterfaceIndex -AddressFamily IPv4 -Forwarding Disabled
        }
    }

    if ((Get-IPEnableRouter) -ne 0 -and $PSCmdlet.ShouldProcess('IPEnableRouter', 'Set to 0')) {
        Set-ItemProperty -Path $TcpipKey -Name IPEnableRouter -Value 0 -Type DWord
        Write-Warning 'IPEnableRouter changed to 0 - reboot required for it to take effect.'
    }

    foreach ($name in (Get-SharedConnections)) {
        if ($PSCmdlet.ShouldProcess($name, 'Disable Internet Connection Sharing')) {
            $mgr = New-Object -ComObject HNetCfg.HNetShare
            foreach ($conn in $mgr.EnumEveryConnection) {
                if ($mgr.NetConnectionProps.Invoke($conn).Name -eq $name) {
                    $mgr.INetSharingConfigurationForINetConnection.Invoke($conn).DisableSharing()
                }
            }
        }
    }

    $rras = Get-Service -Name RemoteAccess -ErrorAction SilentlyContinue
    if ($rras -and ($rras.Status -ne 'Stopped' -or $rras.StartType -ne 'Disabled') -and
        $PSCmdlet.ShouldProcess('RemoteAccess (RRAS)', 'Stop and disable')) {
        Stop-Service -Name RemoteAccess -Force
        Set-Service -Name RemoteAccess -StartupType Disabled
    }
}

if ($WhatIfPreference) {
    Write-Host 'WhatIf mode - no changes made, verification skipped.' -ForegroundColor Yellow
    exit 0
}

# 3. Verification ---------------------------------------------------------------------------
Write-Host "`n--- Verification ($Role) ---" -ForegroundColor Cyan

foreach ($p in Get-NetFirewallProfile) {
    Write-Check "Firewall profile '$($p.Name)' enabled" ([bool]$p.Enabled)
}

$present = @(Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue)
foreach ($rule in $rules) {
    $match = $present | Where-Object { $_.DisplayName -eq $rule.DisplayName -and $_.Enabled -eq 'True' }
    Write-Check "Rule: $($rule.DisplayName)" ([bool]$match)
}

foreach ($ip in $nicIPs) {
    $nic = Get-CitadelNic -IP $ip
    if (-not $nic) { Write-Check "Interface holding $ip present" $false; continue }
    Write-Check "IPv4 forwarding disabled on '$($nic.InterfaceAlias)' ($ip)" ($nic.Forwarding -eq 'Disabled')
}

Write-Check 'IPEnableRouter = 0' ((Get-IPEnableRouter) -eq 0)

$shared = @(Get-SharedConnections)
Write-Check 'No adapter has Internet Connection Sharing enabled' ($shared.Count -eq 0) ($shared -join ', ')

$rras = Get-Service -Name RemoteAccess -ErrorAction SilentlyContinue
Write-Check 'RRAS (RemoteAccess) not running' (-not $rras -or $rras.Status -eq 'Stopped')

if ($Role -eq 'EnterpriseIT') {
    $otNic = Get-CitadelNic -IP $ItOtIP
    if ($otNic) {
        $otDefault = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceIndex $otNic.InterfaceIndex `
            -ErrorAction SilentlyContinue)
        Write-Check 'No default gateway on OT NIC (10.10.0.30)' ($otDefault.Count -eq 0) `
            '(clear the gateway field on NIC 2 if this fails)'
    }
}

$virtualFwd = @(Get-NetIPInterface -AddressFamily IPv4 |
    Where-Object { $_.Forwarding -eq 'Enabled' -and $_.InterfaceAlias -like 'vEthernet*' })
foreach ($v in $virtualFwd) {
    Write-Host "[INFO] Virtual adapter '$($v.InterfaceAlias)' has forwarding enabled (Docker/WSL - expected, left alone)" `
        -ForegroundColor DarkGray
}

Write-Host ''
if ($script:Failures -gt 0) {
    Write-Host "$script:Failures check(s) FAILED." -ForegroundColor Red
    exit 1
}
Write-Host 'All checks passed.' -ForegroundColor Green
exit 0
