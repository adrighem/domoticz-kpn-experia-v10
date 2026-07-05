# KPN Experia V10 Modem Plugin
#
# Author: Vincent
#
"""
<plugin key="ExperiaV10" name="KPN Experia V10 Modem" author="Vincent" version="1.1.0" wikilink="https://github.com/domoticz/domoticz">
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

_AUTH_ERROR_CODES = {"196621", "196614"}
_AUTH_ERROR_TEXT_MARKERS = (
    "invalid session",
    "session expired",
    "session timeout",
    "not authenticated",
    "authentication",
)
_OPTIONAL_PERMISSION_DENIED_SERVICES = {
    "Devices.Device.guest",
    "NeMo.Intf.eth0",
    "NMC.Wifi",
}


class ExperiaV10ApiError(Exception):
    """Base exception for router API errors."""


class ExperiaV10AuthenticationError(ExperiaV10ApiError):
    """Raised when router authentication fails after retry."""


class ExperiaV10PermissionDeniedError(ExperiaV10ApiError):
    """Raised when the router denies access to a specific API endpoint."""


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
        self.last_traffic_time = None
        self.known_macs = None
        self.last_new_device_time = None
        self.last_new_device_info = None

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

    def _debug(self, message):
        debug = getattr(Domoticz, "Debug", None)
        if callable(debug):
            debug(message)

    def _clear_context(self):
        self.context_id = None
        self.cookie = None

    def _ensure_text_device(self, device_id, name):
        if device_id not in Devices or 1 not in Devices[device_id].Units:
            Domoticz.Log(f"Creating {name} text sensor")
            Domoticz.Unit(Name=name, DeviceID=device_id, Unit=1, Type=243, Subtype=19).Create()

    def _ensure_custom_sensor(self, device_id, name, unit):
        if device_id not in Devices or 1 not in Devices[device_id].Units:
            Domoticz.Log(f"Creating {name} sensor")
            Domoticz.Unit(
                Name=name,
                DeviceID=device_id,
                Unit=1,
                Type=243,
                Subtype=31,
                Options={"Custom": f"1;{unit}"},
            ).Create()

    def _ensure_switch(self, device_id, name, **kwargs):
        if device_id not in Devices or 1 not in Devices[device_id].Units:
            Domoticz.Log(f"Creating {name} switch")
            Domoticz.Unit(Name=name, DeviceID=device_id, Unit=1, TypeName="Switch", **kwargs).Create()

    def _format_number(self, value):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(round(value, 2)) if isinstance(value, float) else str(value)

    def _update_unit(self, device_id, name, nValue=None, sValue=None, log=True):
        if device_id not in Devices or 1 not in Devices[device_id].Units:
            return

        unit = Devices[device_id].Units[1]
        needs_update = False
        update_props = False

        if unit.Name != name:
            unit.Name = name
            needs_update = True
            update_props = True

        if nValue is not None and unit.nValue != nValue:
            unit.nValue = nValue
            needs_update = True

        if sValue is not None and unit.sValue != sValue:
            unit.sValue = sValue
            needs_update = True

        if needs_update:
            if update_props:
                unit.Update(Log=log, UpdateProperties=True)
            else:
                unit.Update(Log=log)

    def _update_text_device(self, device_id, name, value, log=True):
        self._ensure_text_device(device_id, name)
        self._update_unit(device_id, name, nValue=0, sValue=str(value), log=log)

    def _update_custom_sensor(self, device_id, name, value, unit, log=True):
        self._ensure_custom_sensor(device_id, name, unit)
        self._update_unit(device_id, name, nValue=0, sValue=self._format_number(value), log=log)

    def _update_switch(self, device_id, name, active, log=True, **kwargs):
        self._ensure_switch(device_id, name, **kwargs)
        nValue = 1 if active else 0
        sValue = "On" if active else "Off"
        self._update_unit(device_id, name, nValue=nValue, sValue=sValue, log=log)

    def _calculate_throughput(self, rx_bytes, tx_bytes):
        current_time = time.monotonic()
        throughput_down = 0.0
        throughput_up = 0.0

        if (
            self.last_rx_bytes is not None
            and self.last_tx_bytes is not None
            and self.last_traffic_time is not None
        ):
            time_delta = current_time - self.last_traffic_time
            rx_delta = rx_bytes - self.last_rx_bytes
            tx_delta = tx_bytes - self.last_tx_bytes
            if time_delta > 0 and rx_delta >= 0 and tx_delta >= 0:
                throughput_down = rx_delta / time_delta
                throughput_up = tx_delta / time_delta

        self.last_rx_bytes = rx_bytes
        self.last_tx_bytes = tx_bytes
        self.last_traffic_time = current_time
        return throughput_down, throughput_up

    def _detect_new_devices(self, devices):
        current_macs = {device["mac"] for device in devices}
        if self.known_macs is None:
            self.known_macs = current_macs
            return False

        new_macs = current_macs - self.known_macs
        if new_macs:
            self.known_macs.update(new_macs)
            for device in devices:
                if device["mac"] in new_macs:
                    self.last_new_device_info = f"{device['name'] or device['mac']} ({device['mac']})"
                    self.last_new_device_time = time.monotonic()
                    break

        if self.last_new_device_time is not None:
            return time.monotonic() - self.last_new_device_time < 300

        return False

    def sync_devices(self):
        try:
            devices = self.get_devices()
        except Exception as e:
            Domoticz.Error(f"Error fetching devices: {e}")
            devices = None

        if devices is not None:
            self._sync_tracked_devices(devices)
            self._sync_client_diagnostics(devices)

        try:
            self._sync_router_info()
        except Exception as e:
            Domoticz.Error(f"Error fetching router info: {e}")

        try:
            self._sync_wifi_status()
        except ExperiaV10PermissionDeniedError as e:
            self._debug(f"Optional Wi-Fi status unavailable: {e}")
        except Exception as e:
            Domoticz.Error(f"Error fetching Wi-Fi status: {e}")

        try:
            self._sync_guest_wifi_status()
        except ExperiaV10PermissionDeniedError as e:
            self._debug(f"Optional Guest Wi-Fi status unavailable: {e}")
        except Exception as e:
            Domoticz.Error(f"Error fetching Guest Wi-Fi status: {e}")

        try:
            self._sync_wan_info()
        except Exception as e:
            Domoticz.Error(f"Error fetching WAN info: {e}")

        try:
            self._sync_traffic_info()
        except ExperiaV10PermissionDeniedError as e:
            self._debug(f"Optional traffic counters unavailable: {e}")
        except Exception as e:
            Domoticz.Error(f"Error fetching Traffic info: {e}")

        # Reboot Button creation
        if "REBOOT_MODEM" not in Devices or 1 not in Devices["REBOOT_MODEM"].Units:
            Domoticz.Log("Creating Reboot Modem button")
            Domoticz.Unit(Name="Reboot Modem", DeviceID="REBOOT_MODEM", Unit=1, Type=244, Subtype=73, Switchtype=9).Create()

    def _sync_tracked_devices(self, devices):
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

    def _sync_client_diagnostics(self, devices):
        active_clients = len([device for device in devices if device["active"]])
        new_device_detected = self._detect_new_devices(devices)
        last_new_device = self.last_new_device_info or ""

        self._update_custom_sensor("CLIENT_COUNT", "Active Clients", active_clients, "clients")
        self._update_switch("NEW_DEVICE", "New Device Detected", new_device_detected)
        self._update_text_device("LAST_NEW_DEVICE", "Last New Device", last_new_device)

    def _sync_router_info(self):
        router_info = self.get_router_info()
        info_text = (
            f"{router_info['model']} | "
            f"HW {router_info['hardware_version'] or '-'} | "
            f"SW {router_info['software_version'] or '-'} | "
            f"SN {router_info['serial_number'] or '-'}"
        )

        self._update_text_device("ROUTER_INFO", "Router Info", info_text)
        self._update_text_device("ROUTER_MODEL", "Router Model", router_info["model"])
        self._update_text_device("ROUTER_SOFTWARE", "Router Software Version", router_info["software_version"])
        self._update_text_device("ROUTER_SERIAL", "Router Serial Number", router_info["serial_number"])
        self._update_custom_sensor("ROUTER_UPTIME", "Uptime", router_info["uptime"], "s")

    def _sync_wifi_status(self):
        wifi_on = self.get_wifi_status()
        self._update_switch("WIFI", "Global Wi-Fi", wifi_on)

    def _sync_guest_wifi_status(self):
        guest_on, _guest_uid = self.get_guest_wifi_status()
        self._update_switch("GUEST_WIFI", "Guest Wi-Fi", guest_on)

    def _sync_wan_info(self):
        wan_info = self.get_wan_info()
        self._update_switch("WAN_STATUS", "Internet Connection", wan_info["connected"])
        self._update_text_device("WAN_IP", "External IP", wan_info["external_ip"])
        self._update_text_device("WAN_LINK_STATUS", "WAN Link Status", wan_info["link_status"])

    def _sync_traffic_info(self):
        traffic_info = self.get_traffic_info()

        # Clean up old Custom Sensor devices if they exist
        for old_dev in ["TRAFFIC_RX_MB", "TRAFFIC_TX_MB", "TRAFFIC_RX_INC", "TRAFFIC_TX_INC", "TRAFFIC_RX", "TRAFFIC_TX"]:
            if old_dev in Devices and 1 in Devices[old_dev].Units:
                try:
                    # Only delete if it's the broken TypeName="Counter" which ended up as Type 243 (General)
                    if Devices[old_dev].Units[1].Type != 113:
                        Devices[old_dev].Units[1].Delete()
                except Exception as e:
                    Domoticz.Log(f"Notice: Failed to delete {old_dev} (may already be removed): {e}")

        if "TRAFFIC_RX" not in Devices or 1 not in Devices["TRAFFIC_RX"].Units:
            Domoticz.Log("Creating Traffic RX Counter")
            Domoticz.Unit(Name="Data Received (KB)", DeviceID="TRAFFIC_RX", Unit=1, Type=113, Subtype=0, Switchtype=3).Create()
        if "TRAFFIC_TX" not in Devices or 1 not in Devices["TRAFFIC_TX"].Units:
            Domoticz.Log("Creating Traffic TX Counter")
            Domoticz.Unit(Name="Data Sent (KB)", DeviceID="TRAFFIC_TX", Unit=1, Type=113, Subtype=0, Switchtype=3).Create()

        rx_bytes = traffic_info["rx_bytes"]
        tx_bytes = traffic_info["tx_bytes"]
        throughput_down, throughput_up = self._calculate_throughput(rx_bytes, tx_bytes)

        # Domoticz Counter type expects the absolute total value.
        # Domoticz natively handles counter resets if the new value is lower than the previous one.
        # We pass KB (divide by 1024) to keep it as an integer and avoid losing precision like we would with MBs.
        rx_kb = rx_bytes // 1024
        tx_kb = tx_bytes // 1024

        self._update_unit("TRAFFIC_RX", "Data Received (KB)", nValue=0, sValue=str(rx_kb))
        self._update_unit("TRAFFIC_TX", "Data Sent (KB)", nValue=0, sValue=str(tx_kb))
        self._update_custom_sensor("THROUGHPUT_DOWN", "Download Speed", throughput_down, "B/s")
        self._update_custom_sensor("THROUGHPUT_UP", "Upload Speed", throughput_up, "B/s")

    def get_guest_wifi_status(self):
        try:
            data = self._request("NMC.Guest", "get", endpoint="ws")
            status = data.get("status", {})
            if isinstance(status, dict):
                return bool(status.get("Enable", False)), "NMC.Guest"
        except ExperiaV10ApiError as e:
            self._debug(f"Failed to get Guest Wi-Fi via NMC.Guest: {e}")

        return self._get_guest_wifi_status_from_radio()

    def _get_guest_wifi_status_from_radio(self):
        data = self._request("sah.Device.WiFi.Radio", "get", endpoint="ws")
        status = data.get("status")
        if not isinstance(status, list):
            return False, None

        for entry in status:
            if isinstance(entry, dict) and "Guest" in str(entry.get("SSID", "")):
                return bool(entry.get("Enable", False)), entry.get("UID")
        return False, None

    def set_guest_wifi_status(self, enable, uid=None):
        try:
            data = self._request("NMC.Guest", "set", {"Enable": enable}, endpoint="ws")
            if data:
                return
        except ExperiaV10ApiError as e:
            self._debug(f"Failed to set Guest Wi-Fi via NMC.Guest: {e}")

        self._set_guest_wifi_status_from_radio(enable, uid)

    def _set_guest_wifi_status_from_radio(self, enable, uid=None):
        if not uid:
            _, uid = self._get_guest_wifi_status_from_radio()
        if not uid:
            raise ExperiaV10ApiError("Guest Wi-Fi interface not found")
        self._request("sah.Device.WiFi.Radio", "set", {"uid": uid, "Enable": enable}, endpoint="ws")

    def get_router_info(self):
        data = self._request("DeviceInfo", "get", endpoint="ws")
        status = data.get("status")
        if not isinstance(status, dict) or not status:
            data = self._request(
                "Devices",
                "get",
                {"expression": "self && wan && hgw"},
                endpoint="ws/NeMo/Intf/lan:getMIBs",
            )
            status = data.get("status")
            if isinstance(status, list) and status:
                status = status[0]

        if not isinstance(status, dict):
            status = {}

        uptime = int(status.get("UpTime", 0) or 0)
        if uptime == 0:
            try:
                nmc_data = self._request("NMC", "get")
                nmc_status = nmc_data.get("status", {})
                if isinstance(nmc_status, dict):
                    uptime = int(nmc_status.get("UpTime", 0) or 0)
            except Exception:
                pass

        return {
            "model": str(status.get("ModelName", status.get("ProductClass", "Experia Box V10"))),
            "hardware_version": str(status.get("HardwareVersion", "")),
            "software_version": str(status.get("SoftwareVersion", "")),
            "serial_number": str(status.get("SerialNumber", "")),
            "uptime": uptime,
        }

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
            "rx_packets": int(status.get("RxPackets", 0) or 0),
            "tx_packets": int(status.get("TxPackets", 0) or 0),
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

    def _build_request(self, url, payload, headers):
        req = urllib.request.Request(url, json.dumps(payload).encode("utf-8"))
        for key, value in headers.items():
            req.add_header(key, value)
        return req

    def _read_json_response(self, resp):
        return json.loads(resp.read().decode("utf-8"))

    def _extract_error_details(self, data):
        details = [data]

        for key in ("status", "data"):
            value = data.get(key)
            if isinstance(value, dict):
                details.append(value)

        for detail in details:
            if not isinstance(detail, dict):
                continue

            error_code = detail.get("error")
            error_text_parts = [
                str(detail.get(key, ""))
                for key in ("description", "info", "message", "reason")
            ]

            errors = detail.get("errors")
            if isinstance(errors, list) and errors:
                first_error = errors[0]
                if isinstance(first_error, dict):
                    error_code = first_error.get("error", error_code)
                    error_text_parts.extend(
                        str(first_error.get(key, ""))
                        for key in ("description", "info", "message", "reason")
                    )

            if error_code is not None:
                return str(error_code), " ".join(error_text_parts).lower()

        return None, ""

    def _is_auth_error(self, error_code, error_text):
        if error_code in _AUTH_ERROR_CODES:
            return True

        return bool(error_text) and any(
            marker in error_text for marker in _AUTH_ERROR_TEXT_MARKERS
        )

    def _should_retry_permission_denied(self, service):
        return service not in _OPTIONAL_PERMISSION_DENIED_SERVICES

    def _retry_after_auth_error(self, service, method, parameters, endpoint, retry_on_auth_error, error_message):
        self._clear_context()
        if retry_on_auth_error:
            return self._request(
                service,
                method,
                parameters,
                endpoint,
                retry_on_auth_error=False,
            )
        raise ExperiaV10AuthenticationError(error_message)

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
        headers = {
            "Content-Type": "application/x-sah-ws-4-call+json",
            "Authorization": "X-Sah-Login",
            "User-Agent": self.user_agent,
        }

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            req = self._build_request(login_url, login_payload, headers)
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            response_info = resp.info()
            data = self._read_json_response(resp)
        except urllib.error.HTTPError as e:
            if e.code != 200:
                login_url = f"http://{host}/ws/NeMo/Intf/lan:getMIBs"
                try:
                    req = self._build_request(login_url, login_payload, headers)
                    resp = urllib.request.urlopen(req, timeout=5, context=ctx)
                    response_info = resp.info()
                    data = self._read_json_response(resp)
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

            cookie_header = response_info.get("Set-Cookie", "")
            self.cookie = cookie_header.split(";")[0] if cookie_header else ""
            return True
        except KeyError as err:
            Domoticz.Error(f"Context key error: {err}")
            return False

    def _request(self, service, method, parameters=None, endpoint="ws/NeMo/Intf/lan:getMIBs", retry_on_auth_error=True):
        if not self.context_id or self.cookie is None:
            if not self._get_context():
                raise ExperiaV10AuthenticationError("Router login failed")

        host = Parameters["Address"]
        if endpoint == "ws/NeMo/Intf/lan:getMIBs" and service in ("sah.Device.Information", "DeviceInfo", "sah.Device.WiFi.Radio"):
            endpoint = "ws"

        url = f"http://{host}/{endpoint}"
        payload = {
            "service": service,
            "method": method,
            "parameters": parameters or {},
        }
        headers = {
            "Content-Type": "application/x-sah-ws-4-call+json",
            "Authorization": f"X-Sah {self.context_id}",
            "X-Context": self.context_id,
            "Cookie": self.cookie,
            "User-Agent": self.user_agent,
        }

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            req = self._build_request(url, payload, headers)
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            data = self._read_json_response(resp)

            if isinstance(data, dict):
                error_code, error_text = self._extract_error_details(data)

                if error_code is not None:
                    if self._is_auth_error(error_code, error_text):
                        return self._retry_after_auth_error(
                            service,
                            method,
                            parameters,
                            endpoint,
                            retry_on_auth_error,
                            f"Router session expired after retry: {data}",
                        )
                    elif error_code == "196618" and service == "sah.Device.WiFi.Radio":
                        self._debug("Ignoring 196618 error for Wi-Fi Radio (disabled)")
                        return {}
                    elif error_code == "13":
                        if self._should_retry_permission_denied(service):
                            return self._retry_after_auth_error(
                                service,
                                method,
                                parameters,
                                endpoint,
                                retry_on_auth_error,
                                f"Router permission denied after authentication retry: {data}",
                            )
                        raise ExperiaV10PermissionDeniedError(
                            f"Router API returned permission denied for {service}: {data}"
                        )
                    else:
                        raise ExperiaV10ApiError(f"Router API returned error {error_code}: {data}")
            return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return self._retry_after_auth_error(
                    service,
                    method,
                    parameters,
                    endpoint,
                    retry_on_auth_error,
                    f"Router authentication failed with HTTP {e.code}",
                )
            self._clear_context()
            raise ExperiaV10ApiError(f"Router HTTP error {e.code}: {e}") from e
        except json.JSONDecodeError:
            return self._retry_after_auth_error(
                service,
                method,
                parameters,
                endpoint,
                retry_on_auth_error,
                "Router returned a non-JSON response after authentication retry",
            )
        except ExperiaV10PermissionDeniedError:
            raise
        except ExperiaV10ApiError:
            self._clear_context()
            raise
        except Exception as e:
            self._clear_context()
            raise ExperiaV10ApiError(f"API request failed: {e}") from e

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
        successful_query = False
        last_error = None

        for expression in (
            "not interface and not self and not voice and .Active==false",
            "not interface and not self and not voice and .Active==true",
        ):
            try:
                data = self._request(
                    "Devices",
                    "get",
                    {"expression": expression, "flags": "full_links"},
                    endpoint="ws/NeMo/Intf/lan:getMIBs",
                )
                successful_query = True
                status = data.get("status")
                if isinstance(status, list):
                    self._parse_devices(status, self.track_wired, results)
            except Exception as e:
                last_error = e
                self._debug(f"Failed to get devices via targeted query: {e}")

        if not results:
            for network in ("lan", "guest"):
                try:
                    data = self._request(
                        f"Devices.Device.{network}",
                        "topology",
                        {"expression": "not logical", "flags": "no_recurse|no_actions"},
                        endpoint="ws/NeMo/Intf/lan:getMIBs",
                    )
                    successful_query = True
                    status = data.get("status")
                    if isinstance(status, list):
                        self._parse_topology(status, self.track_wired, results)
                except Exception as e:
                    last_error = e
                    self._debug(f"Failed to get {network} topology: {e}")

        if not results and not successful_query and last_error:
            raise last_error

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
