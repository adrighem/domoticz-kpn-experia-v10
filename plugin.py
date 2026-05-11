# KPN Experia V10 Modem Plugin
#
# Author: Vincent
#
"""
<plugin key="ExperiaV10" name="KPN Experia V10 Modem" author="Vincent" version="1.0.0" wikilink="https://github.com/domoticz/domoticz">
    <description>
        <h2>KPN Experia V10 Modem</h2><br/>
        This plugin tracks connected devices to the KPN Experia V10 modem.
        It creates a switch for each connected device showing its active state.
    </description>
    <params>
        <param field="Address" label="IP Address" width="200px" required="true" default="192.168.2.254"/>
        <param field="Username" label="Username" width="200px" required="true" default="admin"/>
        <param field="Password" label="Password" width="200px" required="true" password="true"/>
        <param field="Mode1" label="Track Wired Devices" width="75px">
            <options>
                <option label="Yes" value="True"/>
                <option label="No" value="False" default="true" />
            </options>
        </param>
    </params>
</plugin>
"""

import DomoticzEx as Domoticz
import urllib.request
import urllib.error
import json
import ssl
import threading
import time

class ExperiaPlugin:
    def __init__(self):
        self.context_id = None
        self.cookie = None
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        self.track_wired = False
        self.stop_event = threading.Event()
        self.poll_thread = None
        self.command_threads = []
        self.last_rx_bytes = None
        self.last_tx_bytes = None

    def onStart(self):
        Domoticz.Log("onStart called")
        self.track_wired = (Parameters.get("Mode1", "False") == "True")
        
        # Start background polling thread
        self.stop_event.clear()
        self.poll_thread = threading.Thread(name="ExperiaV10_PollThread", target=self.poll_loop)
        self.poll_thread.start()

    def onStop(self):
        Domoticz.Log("onStop called")
        # Signal all background threads to stop
        self.stop_event.set()
        
        # Wait for polling thread to exit
        if self.poll_thread and self.poll_thread.is_alive():
            Domoticz.Log("Waiting for background polling thread to exit...")
            self.poll_thread.join(timeout=10)
            if self.poll_thread.is_alive():
                Domoticz.Error("Background polling thread failed to exit gracefully!")
            else:
                Domoticz.Log("Background polling thread stopped.")

        # Wait for command threads to exit
        # Clean up dead threads first
        self.command_threads = [t for t in self.command_threads if t.is_alive()]
        for thread in self.command_threads:
            Domoticz.Log(f"Waiting for command thread {thread.name} to exit...")
            thread.join(timeout=5)
            if thread.is_alive():
                Domoticz.Error(f"Command thread {thread.name} failed to exit gracefully!")

        # Optional: Print warning for any other leaked threads (similar to the example)
        for thread in threading.enumerate():
            if thread.name != threading.current_thread().name:
                Domoticz.Log(f"'{thread.name}' is still running. It must be shutdown otherwise Domoticz will abort on plugin exit.")

    def poll_loop(self):
        """Background thread loop for polling the router."""
        Domoticz.Log("Background thread started.")
        while not self.stop_event.is_set():
            try:
                self.sync_devices()
            except Exception as e:
                Domoticz.Error(f"Error in poll loop: {e}")
            
            # Wait 30 seconds between polls, but break early if stop_event is set
            self.stop_event.wait(30.0)

    def sync_devices(self):
        try:
            devices = self.get_devices()
        except Exception as e:
            Domoticz.Error(f"Error fetching devices: {e}")
            return
            
        # devices is a list of dicts: mac, name, ip, active
        for dev in devices:
            mac = dev["mac"]
            name = dev["name"]
            ip = dev["ip"]
            active = dev["active"]
            
            device_id = mac
            unit = 1
            
            if device_id not in Devices or unit not in Devices[device_id].Units:
                Domoticz.Log(f"Creating device for {name} ({mac}) at IP {ip}")
                Domoticz.Unit(Name=name, DeviceID=device_id, Unit=unit, TypeName="Switch").Create()
                    
            if device_id in Devices and unit in Devices[device_id].Units:
                nValue = 1 if active else 0
                sValue = "On" if active else "Off"
                ha_unit = Devices[device_id].Units[unit]
                
                needs_update = False
                update_props = False
                
                if ha_unit.nValue != nValue or ha_unit.sValue != sValue:
                    needs_update = True
                    
                if ha_unit.Name != name:
                    needs_update = True
                    update_props = True
                    Domoticz.Log(f"Device name changed from '{ha_unit.Name}' to '{name}'")
                    
                if needs_update:
                    Domoticz.Log(f"Updating device {name} ({mac}) to {sValue}")
                    ha_unit.nValue = nValue
                    ha_unit.sValue = sValue
                    if update_props:
                        ha_unit.Name = name
                        ha_unit.Update(Log=True, UpdateProperties=True)
                    else:
                        ha_unit.Update(Log=True)

        # Wi-Fi status sync
        try:
            wifi_on = self.get_wifi_status()
            
            if "WIFI" not in Devices or 1 not in Devices["WIFI"].Units:
                Domoticz.Log("Creating Global Wi-Fi switch")
                Domoticz.Unit(Name="Global Wi-Fi", DeviceID="WIFI", Unit=1, TypeName="Switch").Create()
                
            if "WIFI" in Devices and 1 in Devices["WIFI"].Units:
                ha_unit = Devices["WIFI"].Units[1]
                nValue = 1 if wifi_on else 0
                sValue = "On" if wifi_on else "Off"
                
                if ha_unit.nValue != nValue or ha_unit.sValue != sValue:
                    Domoticz.Log(f"Updating Global Wi-Fi switch to {sValue}")
                    ha_unit.nValue = nValue
                    ha_unit.sValue = sValue
                    ha_unit.Update(Log=True)
        except Exception as e:
            Domoticz.Error(f"Error fetching Wi-Fi status: {e}")

        # Guest Wi-Fi status sync
        try:
            guest_on, guest_uid = self.get_guest_wifi_status()
            
            if "GUEST_WIFI" not in Devices or 1 not in Devices["GUEST_WIFI"].Units:
                Domoticz.Log("Creating Guest Wi-Fi switch")
                Domoticz.Unit(Name="Guest Wi-Fi", DeviceID="GUEST_WIFI", Unit=1, TypeName="Switch").Create()
                
            if "GUEST_WIFI" in Devices and 1 in Devices["GUEST_WIFI"].Units:
                ha_unit = Devices["GUEST_WIFI"].Units[1]
                nValue = 1 if guest_on else 0
                sValue = "On" if guest_on else "Off"
                
                if ha_unit.nValue != nValue or ha_unit.sValue != sValue:
                    Domoticz.Log(f"Updating Guest Wi-Fi switch to {sValue}")
                    ha_unit.nValue = nValue
                    ha_unit.sValue = sValue
                    ha_unit.Update(Log=True)
        except Exception as e:
            Domoticz.Error(f"Error fetching Guest Wi-Fi status: {e}")

        # WAN Info sync
        try:
            wan_info = self.get_wan_info()
            
            if "WAN_STATUS" not in Devices or 1 not in Devices["WAN_STATUS"].Units:
                Domoticz.Log("Creating WAN Status switch")
                Domoticz.Unit(Name="Internet Connection", DeviceID="WAN_STATUS", Unit=1, TypeName="Switch").Create()
            if "WAN_IP" not in Devices or 1 not in Devices["WAN_IP"].Units:
                Domoticz.Log("Creating WAN IP text sensor")
                Domoticz.Unit(Name="External IP", DeviceID="WAN_IP", Unit=1, Type=243, Subtype=19).Create()
                
            if "WAN_STATUS" in Devices and 1 in Devices["WAN_STATUS"].Units:
                ha_unit = Devices["WAN_STATUS"].Units[1]
                nValue = 1 if wan_info["connected"] else 0
                sValue = "On" if wan_info["connected"] else "Off"
                if ha_unit.nValue != nValue or ha_unit.sValue != sValue:
                    ha_unit.nValue = nValue
                    ha_unit.sValue = sValue
                    ha_unit.Update(Log=True)
                    
            if "WAN_IP" in Devices and 1 in Devices["WAN_IP"].Units:
                ha_unit = Devices["WAN_IP"].Units[1]
                sValue = wan_info["external_ip"]
                if ha_unit.sValue != sValue:
                    ha_unit.sValue = sValue
                    ha_unit.Update(Log=True)
        except Exception as e:
            Domoticz.Error(f"Error fetching WAN info: {e}")

        # Traffic Info sync
        try:
            traffic_info = self.get_traffic_info()
            
            # Clean up old Custom Sensor devices if they exist
            if "TRAFFIC_RX_MB" in Devices and 1 in Devices["TRAFFIC_RX_MB"].Units:
                Devices["TRAFFIC_RX_MB"].Units[1].Delete()
            if "TRAFFIC_TX_MB" in Devices and 1 in Devices["TRAFFIC_TX_MB"].Units:
                Devices["TRAFFIC_TX_MB"].Units[1].Delete()
            if "TRAFFIC_RX_INC" in Devices and 1 in Devices["TRAFFIC_RX_INC"].Units:
                Devices["TRAFFIC_RX_INC"].Units[1].Delete()
            if "TRAFFIC_TX_INC" in Devices and 1 in Devices["TRAFFIC_TX_INC"].Units:
                Devices["TRAFFIC_TX_INC"].Units[1].Delete()
            
            if "TRAFFIC_RX" not in Devices or 1 not in Devices["TRAFFIC_RX"].Units:
                Domoticz.Log("Creating Traffic RX Counter")
                Domoticz.Unit(Name="Data Received (KB)", DeviceID="TRAFFIC_RX", Unit=1, TypeName="Counter").Create()
            if "TRAFFIC_TX" not in Devices or 1 not in Devices["TRAFFIC_TX"].Units:
                Domoticz.Log("Creating Traffic TX Counter")
                Domoticz.Unit(Name="Data Sent (KB)", DeviceID="TRAFFIC_TX", Unit=1, TypeName="Counter").Create()
                
            rx_bytes = traffic_info["rx_bytes"]
            tx_bytes = traffic_info["tx_bytes"]

            # Domoticz Counter type expects the absolute total value.
            # Domoticz natively handles counter resets if the new value is lower than the previous one.
            # We pass KB (divide by 1024) to keep it as an integer and avoid losing precision like we would with MBs.
            rx_kb = rx_bytes // 1024
            tx_kb = tx_bytes // 1024
            
            if "TRAFFIC_RX" in Devices and 1 in Devices["TRAFFIC_RX"].Units:
                Devices["TRAFFIC_RX"].Units[1].Update(nValue=0, sValue=str(rx_kb), Log=True)

            if "TRAFFIC_TX" in Devices and 1 in Devices["TRAFFIC_TX"].Units:
                Devices["TRAFFIC_TX"].Units[1].Update(nValue=0, sValue=str(tx_kb), Log=True)

        except Exception as e:
            Domoticz.Error(f"Error fetching Traffic info: {e}")

        # Reboot Button creation
        if "REBOOT_MODEM" not in Devices or 1 not in Devices["REBOOT_MODEM"].Units:
            Domoticz.Log("Creating Reboot Modem button")
            Domoticz.Unit(Name="Reboot Modem", DeviceID="REBOOT_MODEM", Unit=1, Type=244, Subtype=73, Switchtype=9).Create()

    def get_guest_wifi_status(self):
        data = self._request("sah.Device.WiFi.Radio", "get", endpoint="ws")
        status = data.get("status")
        if not isinstance(status, list):
            return False, None
        for entry in status:
            if isinstance(entry, dict) and "Guest" in str(entry.get("SSID", "")):
                return entry.get("Enable", False), entry.get("UID")
        return False, None

    def set_guest_wifi_status(self, enable, uid=None):
        if not uid:
            _, uid = self.get_guest_wifi_status()
        if uid:
            self._request("sah.Device.WiFi.Radio", "set", {"uid": uid, "Enable": enable}, endpoint="ws")

    def get_wan_info(self):
        data = self._request("NMC", "getWANStatus")
        status = data.get("status", False)
        val = data.get("data", {})
        if status and isinstance(val, dict):
            return {
                "external_ip": str(val.get("IPAddress", "")),
                "connected": str(val.get("LinkState", "")).lower() == "up",
                "link_status": str(val.get("LinkState", "Down")),
            }
        return {"external_ip": "", "connected": False, "link_status": "Down"}

    def get_traffic_info(self):
        data = self._request("NeMo.Intf.eth0", "getNetDevStats", endpoint="ws")
        status = data.get("status") or {}
        return {
            "rx_bytes": int(status.get("RxBytes", 0) or 0),
            "tx_bytes": int(status.get("TxBytes", 0) or 0),
        }

    def get_wifi_status(self):
        data = self._request("NMC.Wifi", "get", endpoint="ws")
        status = data.get("status", {})
        if not isinstance(status, dict):
            status = {}
        return not status.get("DisableLocalWiFi", False)

    def set_wifi_status(self, enable):
        disable_val = not enable
        self._request("NMC.Wifi", "set", {"DisableLocalWiFi": disable_val}, endpoint="ws")
        self._request("NeMo.Intf.rad2g0", "set", {"Enable": enable}, endpoint="ws")
        self._request("NeMo.Intf.rad5g0", "set", {"Enable": enable}, endpoint="ws")
        if enable:
            self._request("NeMo.Intf.vap2g0priv", "set", {"PersistentEnable": True}, endpoint="ws")
            self._request("NeMo.Intf.vap5g0priv", "set", {"PersistentEnable": True}, endpoint="ws")

    def _get_context(self):
        host = Parameters["Address"]
        username = Parameters["Username"]
        password = Parameters["Password"]
        
        login_url = f"http://{host}/ws"
        login_payload = {
            "service": "sah.Device.Information",
            "method": "createContext",
            "parameters": {
                "applicationName": "webui",
                "username": username.lower(),
                "password": password,
            },
        }
        
        req = urllib.request.Request(login_url, json.dumps(login_payload).encode('utf-8'))
        req.add_header("Content-Type", "application/x-sah-ws-4-call+json")
        req.add_header("Authorization", "X-Sah-Login")
        req.add_header("User-Agent", self.user_agent)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code != 200:
                # fallback
                login_url = f"http://{host}/ws/NeMo/Intf/lan:getMIBs"
                req.full_url = login_url
                try:
                    resp = urllib.request.urlopen(req, timeout=5, context=ctx)
                    data = json.loads(resp.read().decode('utf-8'))
                except Exception as ex:
                    Domoticz.Error(f"Login failed on fallback: {ex}")
                    return False
            else:
                Domoticz.Error(f"Login failed with HTTP error: {e}")
                return False
        except Exception as e:
            Domoticz.Error(f"Login failed: {e}")
            return False

        try:
            if "data" in data and "contextID" in data["data"]:
                self.context_id = data["data"]["contextID"]
            elif "status" in data and isinstance(data["status"], dict) and "contextID" in data["status"]:
                self.context_id = data["status"]["contextID"]
            else:
                Domoticz.Error(f"Failed to parse contextID. Raw response: {data}")
                return False
                
            cookie_header = resp.info().get("Set-Cookie", "")
            self.cookie = cookie_header.split(";")[0] if cookie_header else ""
            return True
        except KeyError as err:
            Domoticz.Error(f"Context key error: {err}")
            return False

    def _request(self, service, method, parameters=None, endpoint="ws/NeMo/Intf/lan:getMIBs"):
        if not self.context_id or self.cookie is None:
            if not self._get_context():
                return {}

        host = Parameters["Address"]
        if endpoint == "ws/NeMo/Intf/lan:getMIBs" and service in ("sah.Device.Information", "DeviceInfo", "sah.Device.WiFi.Radio"):
            endpoint = "ws"

        url = f"http://{host}/{endpoint}"
        payload = {
            "service": service,
            "method": method,
            "parameters": parameters or {},
        }
        
        req = urllib.request.Request(url, json.dumps(payload).encode('utf-8'))
        req.add_header("Content-Type", "application/x-sah-ws-4-call+json")
        req.add_header("Authorization", f"X-Sah {self.context_id}")
        req.add_header("X-Context", self.context_id)
        req.add_header("Cookie", self.cookie)
        req.add_header("User-Agent", self.user_agent)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            data = json.loads(resp.read().decode('utf-8'))
            
            if isinstance(data, dict):
                error_code = data.get("error")
                if "errors" in data and isinstance(data["errors"], list) and len(data["errors"]) > 0:
                    error_code = data["errors"][0].get("error")
                    
                if error_code is not None:
                    if str(error_code) in ("196621", "196614", "9003"):
                        self.context_id = None
                        self.cookie = None
                        # Re-authenticate once
                        if self._get_context():
                            req.add_header("Authorization", f"X-Sah {self.context_id}")
                            req.add_header("X-Context", self.context_id)
                            req.add_header("Cookie", self.cookie)
                            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
                            data = json.loads(resp.read().decode('utf-8'))
                        else:
                            return {}
                    else:
                        Domoticz.Error(f"Router API returned error {error_code}: {data}")
            return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self.context_id = None
                self.cookie = None
            return {}
        except Exception as e:
            Domoticz.Error(f"API Request failed: {e}")
            self.context_id = None
            self.cookie = None
            return {}

    def _parse_devices(self, status_list, track_wired_devices, results, parent_is_wifi=False):
        for d in status_list:
            if not isinstance(d, dict):
                continue
            
            mac = d.get("PhysAddress")
            if not mac:
                continue

            active = bool(d.get("Active", False))
            tags = str(d.get("Tags", "")).lower().split()
            inf = str(d.get("InterfaceName", d.get("Layer2Interface", ""))).lower()
            
            is_wifi = parent_is_wifi or "wifi" in tags or "ssw_sta" in tags or "wl0" in inf or "wl1" in inf
            is_wired = not is_wifi and ("eth" in tags or "lan" in tags or "eth" in inf)

            if not track_wired_devices and is_wired:
                continue

            results[mac.upper()] = {
                "mac": mac.upper(),
                "name": str(d.get("Name", d.get("Key", mac))),
                "ip": str(d.get("IPAddress", "")),
                "active": active
            }

    def _parse_topology(self, nodes, track_wired_devices, results, parent_is_wifi=False):
        for node in nodes:
            tags = str(node.get("Tags", "")).lower().split()
            inf = str(node.get("InterfaceName", node.get("Layer2Interface", ""))).lower()
            is_wifi = parent_is_wifi or "wifi" in tags or "ssw_sta" in tags or "wl0" in inf or "wl1" in inf
            
            mac = node.get("PhysAddress")
            if mac:
                is_wired = not is_wifi and ("eth" in tags or "lan" in tags or "eth" in inf)
                if not track_wired_devices and is_wired:
                    continue
                results[mac.upper()] = {
                    "mac": mac.upper(),
                    "name": str(node.get("Name", node.get("Key", mac))),
                    "ip": str(node.get("IPAddress", "")),
                    "active": bool(node.get("Active", False))
                }
            
            children = node.get("Children")
            if isinstance(children, list):
                self._parse_topology(children, track_wired_devices, results, parent_is_wifi=is_wifi)

    def get_devices(self):
        results = {}

        data = self._request(
            "Devices", "get", 
            {"expression": "not interface and not self and not voice and .Active==false", "flags": "full_links"},
            endpoint="ws/NeMo/Intf/lan:getMIBs"
        )
        status = data.get("status")
        if isinstance(status, list):
            self._parse_devices(status, self.track_wired, results)
            
        data_active = self._request(
            "Devices", "get", 
            {"expression": "not interface and not self and not voice and .Active==true", "flags": "full_links"},
            endpoint="ws/NeMo/Intf/lan:getMIBs"
        )
        status_active = data_active.get("status")
        if isinstance(status_active, list):
            self._parse_devices(status_active, self.track_wired, results)

        if not results:
            for network in ("lan", "guest"):
                data = self._request(
                    f"Devices.Device.{network}", "topology",
                    {"expression": "not logical", "flags": "no_recurse|no_actions"},
                    endpoint="ws/NeMo/Intf/lan:getMIBs"
                )
                status = data.get("status")
                if isinstance(status, list):
                    self._parse_topology(status, self.track_wired, results)

        return list(results.values())

    def onCommand(self, DeviceID, Unit, Command, Level, Color):
        # Clean up dead threads to prevent list from growing forever
        self.command_threads = [t for t in self.command_threads if t.is_alive()]
        
        if DeviceID == "WIFI" and Unit == 1:
            enable = (Command.lower() == "on")
            Domoticz.Log(f"Setting Wi-Fi to {enable}")
            def set_and_update():
                try:
                    self.set_wifi_status(enable)
                    ha_unit = Devices[DeviceID].Units[Unit]
                    ha_unit.nValue = 1 if enable else 0
                    ha_unit.sValue = "On" if enable else "Off"
                    ha_unit.Update(Log=True)
                except Exception as e:
                    Domoticz.Error(f"Failed to set Wi-Fi: {e}")
            t = threading.Thread(name="ExperiaV10_SetWifi", target=set_and_update)
            self.command_threads.append(t)
            t.start()
        elif DeviceID == "GUEST_WIFI" and Unit == 1:
            enable = (Command.lower() == "on")
            Domoticz.Log(f"Setting Guest Wi-Fi to {enable}")
            def set_and_update_guest():
                try:
                    self.set_guest_wifi_status(enable)
                    ha_unit = Devices[DeviceID].Units[Unit]
                    ha_unit.nValue = 1 if enable else 0
                    ha_unit.sValue = "On" if enable else "Off"
                    ha_unit.Update(Log=True)
                except Exception as e:
                    Domoticz.Error(f"Failed to set Guest Wi-Fi: {e}")
            t = threading.Thread(name="ExperiaV10_SetGuestWifi", target=set_and_update_guest)
            self.command_threads.append(t)
            t.start()
        elif DeviceID == "REBOOT_MODEM" and Unit == 1 and Command.lower() == "on":
            Domoticz.Log("Rebooting Experia V10 Modem...")
            def reboot_modem():
                try:
                    self._request("NMC", "reboot", {"reason": "WebUI reboot"}, endpoint="ws")
                    ha_unit = Devices[DeviceID].Units[Unit]
                    ha_unit.nValue = 1
                    ha_unit.sValue = "On"
                    ha_unit.Update(Log=True)
                    self.stop_event.wait(1.0)
                    ha_unit.nValue = 0
                    ha_unit.sValue = "Off"
                    ha_unit.Update(Log=False)
                except Exception as e:
                    Domoticz.Error(f"Failed to reboot modem: {e}")
            t = threading.Thread(name="ExperiaV10_Reboot", target=reboot_modem)
            self.command_threads.append(t)
            t.start()

global _plugin
_plugin = ExperiaPlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    pass

def onMessage(Connection, Data):
    pass

def onCommand(DeviceID, Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(DeviceID, Unit, Command, Level, Color)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    pass

def onDisconnect(Connection):
    pass
