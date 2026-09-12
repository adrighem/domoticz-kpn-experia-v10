# KPN Experia V10 Web UI API Notes

This document summarizes the API information discovered from the router web UI on
2026-06-12. The crawl was read-only: it downloaded static web UI assets and used
read-only API calls where needed to compare behavior. Credentials, session
cookies, UIDs, SSIDs, MAC addresses, IP addresses, and raw router responses are
intentionally not stored here.

## Source Coverage

The current web UI is a Flutter/Ensemble application. Its service worker and
asset manifest expose the screen YAML files that define most API calls.

Observed application assets:

- `/assets/ensemble/apps/kpnApp/config/appConfig.json`
- `/assets/ensemble/apps/kpnApp/scripts/common.js`
- `/assets/ensemble/apps/kpnApp/scripts/wifiUtils.js`
- `/assets/ensemble/apps/kpnApp/scripts/mockData.js`
- screen YAML files under `/assets/ensemble/apps/kpnApp/screens/`

Structured parsing found 428 API definition entries across these screen files
and their manifest-discovered path variants. Some UI screens define the same
service/method more than once for different widgets, states, or parameter sets.

| Screen file | Role |
| --- | --- |
| `Home.yaml` | Router dashboard, WAN, LAN, Wi-Fi, device overview, telephony snippets. |
| `HomeExtender.yaml` | Extender dashboard and extender-local device/Wi-Fi data. |
| `InternetVerbindingExtender.yaml` | Extender internet and uplink state. |
| `Landing.yaml` | Login/bootstrap checks. |
| `Login.yaml` | Authentication, password recovery, role and user checks. |
| `Logout.yaml` | Session release. |
| `LokaalNetwerk.yaml` | LAN devices, topology, DHCP, addressing, Ethernet, schedules. |
| `LokaalNetwerkExtender.yaml` | Extender LAN/device views and extender-local API proxying. |
| `Security.yaml` | Firewall, DNS, DynDNS, port forwarding, DMZ, UPnP, SIP ALG. |
| `Telefoon.yaml` | Telephony lines, handsets, groups, trunks, calls. |
| `WiFi.yaml` | Private Wi-Fi, guest Wi-Fi, radios, timers, SSIDs, visibility, extenders. |

Two manifest entries were present but returned HTTP 404 during the crawl:

- `Internet verbinding.yaml`
- `Modem instellingen.yaml`

## Request Model

The web UI APIs are JSON-RPC-like calls sent to `/ws/<service-path>`. The
payloads identify a `service`, a `method`, and optional `parameters`. For
example, screen YAML entries commonly target:

- `/ws/NeMo/Intf/lan:getMIBs`
- `/ws/NeMo/Intf/lan:setWLANConfig`
- `/ws/NMC/Guest:get`
- `/ws/Devices:get`

The YAML files use `<app.baseUrl>` as the router base URL. Authentication is
handled by the normal web UI session created through `sah.Device.Information`.

## Authority Notes

These notes are important for this plugin because some older endpoints still
exist but no longer represent the state shown by the current web UI.

| Topic | Current finding |
| --- | --- |
| Guest Wi-Fi status | `NMC.Guest.Enable` can be true while the web UI shows guest Wi-Fi off. `NMC.Guest.Status` and guest VAP state matched the web UI in the live read-only check. |
| Guest Wi-Fi VAP state | `vap2g0guest` and `vap5g0guest` expose `Enable`, `PersistentEnable`, and `Status`. These are the fields changed by the current web UI. |
| Guest Wi-Fi toggle | The current web UI uses `NeMo.Intf.lan:setWLANConfig` with `mibs.penable.vap2g0guest` and `mibs.penable.vap5g0guest`. |
| Old radio API | `sah.Device.WiFi.Radio:get` returned router error code `196618` in the live check and does not appear to be the current authoritative guest API for this firmware. |
| Guest bandwidth | The web UI still uses `NMC.Guest:set` for `BandwidthLimitation`. |
| Guest timers | The web UI uses `NMC.WlanTimer` methods with `InterfaceName: guest`. |

## Wi-Fi APIs

### Global Wi-Fi

| Service | Methods | Purpose |
| --- | --- | --- |
| `NMC.Wifi` | `get`, `set` | Global Wi-Fi state and mode. Fields include `Enable`, `DisableLocalWiFi`, and `ConfigurationMode`. |
| `SSW.Steering.MasterConfig` | `get`, `set` | Wi-Fi steering configuration. |
| `WLanManager.AccessPoint` | `get` | Access point details used by Wi-Fi screens. |

### Radio Interfaces

| Service | Methods | Purpose |
| --- | --- | --- |
| `NeMo.Intf.rad2g0` | `get`, `set`, `setWLANConfig`, `getSpectrumInfo`, `getScanResults` | 2.4 GHz radio configuration, channel information, spectrum data, and scans. |
| `NeMo.Intf.rad5g0` | `get`, `set`, `setWLANConfig`, `getSpectrumInfo`, `getScanResults` | 5 GHz radio configuration, channel information, spectrum data, and scans. |

### Private Wi-Fi Networks

| Service | Methods | Purpose |
| --- | --- | --- |
| `NeMo.Intf.lan` | `getMIBs` | Reads private Wi-Fi VAP details through MIB queries, especially `wlanvap`. |
| `NeMo.Intf.lan` | `setWLANConfig` | Updates SSID, security, passphrase, visibility, and enabled state for VAPs. |
| `NeMo.Intf.<vap>` | `set` | Some screens set `PersistentEnable` directly for specific interfaces. |

The web UI uses `setWLANConfig` as the broad write API for Wi-Fi configuration
changes. It can update multiple VAPs in one call through the `mibs` parameter.

### Guest Wi-Fi

| Service | Methods | Purpose |
| --- | --- | --- |
| `NMC.Guest` | `get` | Guest network summary. `Status` matched the web UI in the live check; `Enable` did not. |
| `NMC.Guest` | `set` | Guest bandwidth limit settings, including `BandwidthLimitation`. |
| `NeMo.Intf.brguest` | `getMIBs` | Guest Wi-Fi VAP information, using `mibs: wlanvap`, `flag: !backhaul`, and one-level traversal. |
| `NeMo.Intf.brguest` | `getNetDevStats` | Guest bridge traffic counters. |
| `NeMo.Intf.lan` | `setWLANConfig` | Authoritative current web UI write path for guest status, SSID, security, passphrase, and visibility. |
| `NMC.WlanTimer` | `getActivationTimer`, `setActivationTimer`, `disableActivationTimer` | Guest Wi-Fi timer handling with `InterfaceName: guest`. |

Guest status toggle payloads update both guest VAPs:

```json
{
  "service": "NeMo.Intf.lan",
  "method": "setWLANConfig",
  "parameters": {
    "mibs": {
      "penable": {
        "vap2g0guest": {
          "Enable": true,
          "PersistentEnable": true,
          "Status": true
        },
        "vap5g0guest": {
          "Enable": true,
          "PersistentEnable": true,
          "Status": true
        }
      }
    }
  }
}
```

The same shape is used with false values to turn guest Wi-Fi off.

Guest visibility uses the same service and method, but sets
`SSIDAdvertisementEnabled` for `vap2g0guest` and `vap5g0guest`.

### Extra And Extender Wi-Fi

| Service | Methods | Purpose |
| --- | --- | --- |
| `NeMo.Intf.vap2g0ext` | `get` | Extra/extender 2.4 GHz VAP state. |
| `NeMo.Intf.vap5g0ext` | `get` | Extra/extender 5 GHz VAP state. |
| `Devices.Device.<mac>.SSW` | `execAPI` | Executes extender-local APIs through the main router UI. |
| `WLanManager.Repeater` | `get` | Repeater/extender Wi-Fi state. |

The extender screens reuse many Wi-Fi concepts but proxy calls through
`Devices.Device.<mac>.SSW:execAPI`.

## LAN And Device APIs

### Device Inventory And Topology

| Service | Methods | Purpose |
| --- | --- | --- |
| `Devices` | `get` | Main device inventory. Used with different parameters for active, inactive, all, STB, extender, and Wi-Fi-filtered device lists. |
| `Devices` | `destroyDevice` | Removes a known device entry. |
| `Devices.Device.<mac>` | `get`, `setName`, `setType` | Per-device details and user-editable metadata. |
| `Devices.Device.lan` | `topology` | LAN topology discovery. |
| `Devices.Device.HGW` | `get`, `topology` | Router device information and topology root. |
| `Devices.Device.guest` | `topology` | Guest network topology. |
| `NMC.Devices` | `findSSW`, `getDevice` | Extender/device lookup helpers. |
| `SAHPairing` | `unpair` | Removes a paired device. |

### DHCP And Static Leases

| Service | Methods | Purpose |
| --- | --- | --- |
| `DHCPv4.Server` | `getDHCPServerPool` | Discovers DHCP pools, including default and guest pools. |
| `DHCPv4.Server.Pool.default` | `getLeases`, `getStaticLeases`, `addStaticLease`, `setStaticLease`, `deleteStaticLease` | Default LAN lease and reservation management. |
| `DHCPv4.Server.Pool.guest` | `getLeases` | Guest DHCP leases. |

### LAN Addressing

| Service | Methods | Purpose |
| --- | --- | --- |
| `NetMaster.LAN.default.Bridge.lan` | `setIPv4` | LAN IPv4 address and subnet configuration changes. |
| `NetMaster.LAN.default.Bridge.lan` | `getIPv6Configuration`, `setIPv6Configuration` | LAN IPv6 configuration. |
| `NetMaster.LAN.default.Bridge.lan` | `set` | General LAN bridge settings. |
| `NetMaster.LAN.default.Bridge.lan` | `setHostName` | Router/LAN hostname update. |
| `NetMaster.LAN.default.Bridge.lan.DHCPv4` | `get` | LAN DHCPv4 settings. |
| `NetMaster.LAN.default.Bridge.lan.HostName` | `get` | LAN hostname. |
| `NetMaster.LAN.default.Bridge.lan.IPv6.lan.DHCPv6` | `set` | LAN DHCPv6 settings. |
| `NetMaster.LAN.default.Bridge.guest` | `setIPv4` | Guest bridge IPv4 configuration. |
| `NetMaster.LAN.default.Bridge.guest.IPv6.guest.DHCPv6` | `set` | Guest DHCPv6 settings. |
| `NetMaster.LAN.default.Bridge.<intf>` | `addIntf`, `removeIntf` | Adds or removes bridge member interfaces. |
| `NetMaster` | `get`, `set` | Higher-level network manager settings. |

### Ethernet And Bridge Interfaces

| Service | Methods | Purpose |
| --- | --- | --- |
| `NeMo.Intf.ETH0` | `getMIBs`, `getNetDevStats` | Ethernet port state and counters. |
| `NeMo.Intf.ETH1` | `getMIBs`, `getNetDevStats` | Ethernet port state and counters. |
| `NeMo.Intf.ETH2` | `getMIBs`, `getNetDevStats` | Ethernet port state and counters. |
| `NeMo.Intf.ETH3` | `getMIBs`, `getNetDevStats` | Ethernet port state and counters. |
| `NeMo.Intf.bridge` | `get`, `getIntfs`, `getNetDevStats`, `set` | LAN bridge state, membership, counters, and settings. |

### Device Scheduling

| Service | Methods | Purpose |
| --- | --- | --- |
| `Scheduler` | `getCompleteSchedules`, `getSchedule`, `addSchedule`, `overrideSchedule`, `removeSchedules`, `enableSchedule` | Device access schedules and overrides. |
| `ToD` | `configureMST`, `listMST`, `getMST`, `setMST`, `statsMST`, `deleteMST` | Time-of-day schedule data and statistics. |

## WAN And Internet APIs

| Service | Methods | Purpose |
| --- | --- | --- |
| `NMC` | `getWANStatus` | Overall WAN status shown by the UI. |
| `NeMo.Intf.data` | `getMIBs`, `getFirstParameter`, `luckyAddrAddress` | Main data WAN interface details. |
| `NeMo.Intf.data` | `getIntfs` | Child interface discovery for the data WAN. |
| `NeMo.Intf.wwan` | `get`, `configureConnection` | Mobile/WWAN interface state and configuration. |
| `NeMo.Intf.iptv` | `get`, `luckyAddrAddress` | IPTV interface details. |
| `NeMo.Intf.vvlan_iptv` | `getNetDevStats` | IPTV VLAN traffic counters. |
| `NeMo.Intf.dsl0` | `getDSLChannelStats` | DSL channel statistics where applicable. |
| `SpeedTest.Diagnostics.Download` | `runDiagnostics` | Download speed test. |
| `SpeedTest.Diagnostics.Upload` | `runDiagnostics` | Upload speed test. |
| `Tessares` | `get` | Hybrid access/bonding status where supported. |

## Security, Firewall, DNS, And Port APIs

### DNS And Dynamic DNS

| Service | Methods | Purpose |
| --- | --- | --- |
| `DNS` | `get`, `setMode` | DNS operating mode. |
| `DNS.Server` | `getServers` | Configured DNS servers. |
| `DNS.Server.Route` | `get` | DNS route information. |
| `DynDNS` | `getHosts`, `addHost`, `delHost`, `setGlobalEnable` | Dynamic DNS host and global enable management. |

### Firewall

| Service | Methods | Purpose |
| --- | --- | --- |
| `Firewall` | `get`, `set` | General firewall data and settings. |
| `Firewall` | `getFirewallLevel`, `setFirewallLevel`, `setFirewallIPv6Level` | IPv4 and IPv6 firewall levels. |
| `Firewall` | `getChainPolicy`, `setChainPolicy` | Chain policy management. |
| `Firewall` | `deleteCustomRule`, `getCustomRule`, `setCustomRule` | Custom firewall rules. |
| `Firewall` | `deleteDMZ`, `getDMZ`, `setDMZ` | DMZ configuration. |
| `Firewall` | `getPinhole`, `setPinhole`, `deletePinhole` | Open port/pinhole configuration. |
| `Firewall` | `getPortForwarding`, `setPortForwarding`, `deletePortForwarding` | Port forwarding rules. |
| `Firewall` | `getRespondToPing`, `setRespondToPing` | WAN ping response setting. |
| `Firewall.ConnectionTracking.SIP` | `get`, `set` | SIP ALG / SIP connection tracking setting. |

## Telephony APIs

| Service | Methods | Purpose |
| --- | --- | --- |
| `VoiceService.VoiceApplication` | `listTrunks`, `listGroups`, `listHandsets`, `getCallList` | Telephony inventory and call history. |
| `VoiceService.VoiceApplication` | `ring` | Rings a handset for identification. |
| `VoiceService.VoiceApplication` | `setGroups`, `setHandset`, `setTrunk` | Telephony configuration changes. |
| `VoiceService.VoiceApplication.VoiceProfile` | `get` | Voice profile details. |
| `VoiceService.VoiceApplication.VoiceProfile.SIP-Trunk<lineNumber>` | `set` | SIP trunk settings for a specific line. |

## Time And System APIs

| Service | Methods | Purpose |
| --- | --- | --- |
| `Time` | `getTime` | Router time. |
| `sah.Device.Information` | `createContext`, `releaseContext` | Login/session creation and release. |
| `HTTPService` | `getCurrentUser` | Current authenticated UI user. |
| `UserManagement.User.admin` | `get` | Admin user information. |
| `UserManagement` | `changePasswordSec` | Admin password change flow. |
| `PasswordRecovery` | `start` | Password recovery flow. |
| `WebuiupgradeService` | `getLatestVersion` | Web UI upgrade/version information. |
| `MQTT.Client.usp-client` | `get` | USP MQTT client status. |

## LEDs

| Service | Methods | Purpose |
| --- | --- | --- |
| `LEDs` | `getRootLEDs` | Lists root LEDs. |
| `LEDs.LED` | `get` | LED state. |
| `LEDs.LED.Root` | `get`, `set` | Root LED state and configuration. |
| `LEDs.LED.WifiGreen` | `set` | Wi-Fi LED state/configuration. |

## Diagnostics And Miscellaneous APIs

| Service | Methods | Purpose |
| --- | --- | --- |
| `DeviceInfo` | `get` | Router device information. |
| `IPPingDiagnostics` | `execDiagnostic` | Ping diagnostic execution. |
| `LocalAgent` | `get` | Local agent state. |
| `MSS` | `get` | Mesh/subsystem state used by the UI. |
| `MSS.Config` | `get` | Mesh/subsystem configuration. |
| `NMC.GroupFunction` | `ResetAdminPassword` | Admin password reset group function. |
| `NMC.Role` | `get` | Current role/capability information. |
| `eventmanager` | `open_channel`, `get_events` | Event channel management, used by password recovery and live UI flows. |
| `NeMo.Intf.<interface>` | `set` | Dynamic interface setting helper used by parameterized screens. |
| `NeMo.Intf.<ethNum>` | `get` | Dynamic Ethernet interface read helper. |
| `NeMo.Intf.<intf>.Stats` | `get` | Dynamic interface statistics helper. |
| `NeMo.Intf.<logicalIntf>` | `getIntfs` | Dynamic logical interface child discovery. |
| `NeMo.Intf.<port>` | `set` | Dynamic port setting helper. |
| `NeMo.Intf.<type>` | `getNetDevStats` | Dynamic interface traffic counter helper. |
| `<service>` | `set` | Fully dynamic service setter used where the screen computes the target service. |

## Complete Service/Method Inventory

This is the de-duplicated service/method inventory from the parsed web UI
screens. Parameter variants are not expanded here; see the topical sections
above for the important semantics.

### Wi-Fi

| Service | Methods |
| --- | --- |
| `LEDs.LED.WifiGreen` | `set` |
| `NMC.Guest` | `get`, `set` |
| `NMC.Wifi` | `get`, `set` |
| `NMC.WlanTimer` | `disableActivationTimer`, `getActivationTimer`, `setActivationTimer` |
| `NeMo.Intf.brguest` | `getMIBs`, `getNetDevStats`, `set` |
| `NeMo.Intf.rad2g0` | `get`, `getScanResults`, `getSpectrumInfo`, `set`, `setWLANConfig` |
| `NeMo.Intf.rad5g0` | `get`, `getScanResults`, `getSpectrumInfo`, `set`, `setWLANConfig` |
| `NeMo.Intf.vap2g0ext` | `get` |
| `NeMo.Intf.vap5g0ext` | `get` |
| `SSW.Steering.MasterConfig` | `get`, `set` |
| `WLanManager.AccessPoint` | `get` |
| `WLanManager.Repeater` | `get` |

### LAN And Devices

| Service | Methods |
| --- | --- |
| `DHCPv4.Server` | `getDHCPServerPool` |
| `DHCPv4.Server.Pool.default` | `addStaticLease`, `deleteStaticLease`, `getLeases`, `getStaticLeases`, `setStaticLease` |
| `DHCPv4.Server.Pool.guest` | `getLeases` |
| `Devices` | `destroyDevice`, `get` |
| `Devices.Device.${mac}` | `get`, `setName`, `setType` |
| `Devices.Device.${mac}.SSW` | `execAPI` |
| `Devices.Device.HGW` | `get`, `topology` |
| `Devices.Device.guest` | `topology` |
| `Devices.Device.lan` | `topology` |
| `NMC.Devices` | `findSSW`, `getDevice` |
| `NeMo.Intf.ETH0` | `getMIBs`, `getNetDevStats` |
| `NeMo.Intf.ETH1` | `getMIBs`, `getNetDevStats` |
| `NeMo.Intf.ETH2` | `getMIBs`, `getNetDevStats` |
| `NeMo.Intf.ETH3` | `getMIBs`, `getNetDevStats` |
| `NeMo.Intf.bridge` | `get`, `getIntfs`, `getNetDevStats`, `set` |
| `NetMaster.LAN.default.Bridge.${intf}` | `addIntf`, `removeIntf` |
| `NetMaster.LAN.default.Bridge.guest` | `setIPv4` |
| `NetMaster.LAN.default.Bridge.guest.IPv6.guest.DHCPv6` | `set` |
| `NetMaster.LAN.default.Bridge.lan` | `getIPv6Configuration`, `set`, `setHostName`, `setIPv4`, `setIPv6Configuration` |
| `NetMaster.LAN.default.Bridge.lan.DHCPv4` | `get` |
| `NetMaster.LAN.default.Bridge.lan.HostName` | `get` |
| `NetMaster.LAN.default.Bridge.lan.IPv6.lan.DHCPv6` | `set` |
| `SAHPairing` | `unpair` |

### WAN And Internet

| Service | Methods |
| --- | --- |
| `NMC` | `getWANStatus` |
| `NeMo.Intf.data` | `getFirstParameter`, `getIntfs`, `getMIBs`, `luckyAddrAddress` |
| `NeMo.Intf.dsl0` | `getDSLChannelStats` |
| `NeMo.Intf.iptv` | `get`, `luckyAddrAddress` |
| `NeMo.Intf.vvlan_iptv` | `getNetDevStats` |
| `NeMo.Intf.wwan` | `configureConnection`, `get` |
| `SpeedTest.Diagnostics.Download` | `runDiagnostics` |
| `SpeedTest.Diagnostics.Upload` | `runDiagnostics` |
| `Tessares` | `get` |

### Security, Firewall, And DNS

| Service | Methods |
| --- | --- |
| `DNS` | `get`, `setMode` |
| `DNS.Server` | `getServers` |
| `DNS.Server.Route` | `get` |
| `DynDNS` | `addHost`, `delHost`, `getHosts`, `setGlobalEnable` |
| `Firewall` | `deleteCustomRule`, `deleteDMZ`, `deletePinhole`, `deletePortForwarding`, `get`, `getChainPolicy`, `getCustomRule`, `getDMZ`, `getFirewallLevel`, `getPinhole`, `getPortForwarding`, `getRespondToPing`, `set`, `setChainPolicy`, `setCustomRule`, `setDMZ`, `setFirewallIPv6Level`, `setFirewallLevel`, `setPinhole`, `setPortForwarding`, `setRespondToPing` |
| `Firewall.ConnectionTracking.SIP` | `get`, `set` |

### Telephony

| Service | Methods |
| --- | --- |
| `VoiceService.VoiceApplication` | `getCallList`, `listGroups`, `listHandsets`, `listTrunks`, `ring`, `setGroups`, `setHandset`, `setTrunk` |
| `VoiceService.VoiceApplication.VoiceProfile` | `get` |
| `VoiceService.VoiceApplication.VoiceProfile.SIP-Trunk${lineNumber}` | `set` |

### Time And Scheduling

| Service | Methods |
| --- | --- |
| `Scheduler` | `addSchedule`, `enableSchedule`, `getCompleteSchedules`, `getSchedule`, `overrideSchedule`, `removeSchedules` |
| `Time` | `getTime` |
| `ToD` | `configureMST`, `deleteMST`, `getMST`, `listMST`, `setMST`, `statsMST` |

### System And Administration

| Service | Methods |
| --- | --- |
| `HTTPService` | `getCurrentUser` |
| `LEDs` | `getRootLEDs` |
| `LEDs.LED` | `get` |
| `LEDs.LED.DimPin` | `set` |
| `LEDs.LED.Root` | `get`, `set` |
| `MQTT.Client.usp-client` | `get` |
| `PasswordRecovery` | `start` |
| `UserManagement` | `changePasswordSec` |
| `UserManagement.User.admin` | `get` |
| `WebuiupgradeService` | `getLatestVersion`, `upgrade` |
| `sah.Device.Information` | `createContext`, `releaseContext` |

### Diagnostics And Dynamic Services

| Service | Methods |
| --- | --- |
| `${service}` | `set` |
| `DeviceInfo` | `get` |
| `IPPingDiagnostics` | `execDiagnostic` |
| `LocalAgent` | `get` |
| `MSS` | `get` |
| `MSS.Config` | `get` |
| `NMC.GroupFunction` | `ResetAdminPassword` |
| `NMC.Role` | `get` |
| `NeMo.Intf.${ethNum}` | `get` |
| `NeMo.Intf.${interface}` | `set` |
| `NeMo.Intf.${intf}.Stats` | `get` |
| `NeMo.Intf.${logicalIntf}` | `getIntfs` |
| `NeMo.Intf.${port}` | `set` |
| `NeMo.Intf.${service}` | `set` |
| `NeMo.Intf.${type}` | `getNetDevStats` |
| `NeMo.Intf.lan` | `getMIBs`, `setWLANConfig` |
| `NetMaster` | `get`, `set` |
| `eventmanager` | `get_events`, `open_channel` |

## Plugin-Relevant Recommendations

1. Treat `NeMo.Intf.lan:setWLANConfig` as the current guest Wi-Fi write API.
2. Treat guest VAP state and `NMC.Guest.Status` as more authoritative than
   `NMC.Guest.Enable` for read state.
3. Keep `NMC.Guest:set` only for guest-specific settings such as bandwidth, not
   as the primary status toggle.
4. Avoid relying on `sah.Device.WiFi.Radio` for guest Wi-Fi on this firmware.
   It failed read-only probing with router error code `196618`.
5. For device discovery, keep fallback logic across `Devices:get` and topology
   APIs because the web UI itself uses multiple device and topology views.
6. For traffic counters, prefer interface-specific `getNetDevStats` APIs such
   as `NeMo.Intf.brguest`, `NeMo.Intf.bridge`, Ethernet interfaces, IPTV VLAN
   interfaces, or the dynamic `NeMo.Intf.<type>` helper according to the metric
   being displayed.
