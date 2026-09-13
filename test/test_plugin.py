import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class FakeUnit:
    def __init__(self, domoticz, **kwargs):
        self._domoticz = domoticz
        self.Name = kwargs.get("Name")
        self.DeviceID = kwargs.get("DeviceID")
        self.Unit = kwargs.get("Unit", 1)
        self.Type = kwargs.get("Type", 0)
        self.Switchtype = kwargs.get("Switchtype", kwargs.get("SwitchType", 0))
        if kwargs.get("TypeName") == "Contact":
            self.Switchtype = 2
        elif kwargs.get("TypeName") == "Switch":
            self.Switchtype = 0
        self.SwitchType = self.Switchtype
        self.TypeName = kwargs.get("TypeName")
        self.Options = kwargs.get("Options", {})
        self.nValue = 0
        self.sValue = ""
        self.updates = []
        self.deleted = False

    def Update(self, **kwargs):
        self.updates.append(kwargs)
        if "TypeName" in kwargs:
            self.TypeName = kwargs["TypeName"]
            if kwargs["TypeName"] == "Contact":
                self.Switchtype = 2
                self.SwitchType = 2
            elif kwargs["TypeName"] == "Switch":
                self.Switchtype = 0
                self.SwitchType = 0

    def Delete(self):
        self.deleted = True
        device = self._domoticz.devices.get(self.DeviceID)
        if device:
            device.Units.pop(self.Unit, None)


class FakeDevice:
    def __init__(self):
        self.Units = {}


class FakeUnitCreator:
    def __init__(self, domoticz, kwargs):
        self._domoticz = domoticz
        self._kwargs = kwargs

    def Create(self):
        unit = FakeUnit(self._domoticz, **self._kwargs)
        device = self._domoticz.devices.setdefault(unit.DeviceID, FakeDevice())
        device.Units[unit.Unit] = unit
        return unit


class FakeDomoticz(types.ModuleType):
    def __init__(self):
        super().__init__("DomoticzEx")
        self.devices = {}
        self.logs = []
        self.errors = []
        self.debug = []

    def Unit(self, **kwargs):
        return FakeUnitCreator(self, kwargs)

    def Log(self, message):
        self.logs.append(message)

    def Error(self, message):
        self.errors.append(message)

    def Debug(self, message):
        self.debug.append(message)


class FakeResponse:
    def __init__(self, data, headers=None, raw=False):
        self._data = data
        self._headers = headers or {}
        self._raw = raw

    def read(self):
        if self._raw:
            return self._data
        return json.dumps(self._data).encode("utf-8")

    def info(self):
        return self

    def get(self, key, default=None):
        return self._headers.get(key, default)


def load_plugin():
    fake_domoticz = FakeDomoticz()
    sys.modules["DomoticzEx"] = fake_domoticz
    module_name = "experiav10_plugin_under_test"
    sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(module_name, ROOT / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    module.Parameters = {
        "Address": "192.168.2.254",
        "Username": "admin",
        "Password": "password",
        "Mode1": "False",
    }
    module.Devices = {}
    fake_domoticz.devices = module.Devices
    return module, fake_domoticz


def request_payload(request):
    return json.loads(request.data.decode("utf-8"))


def request_headers(request):
    return {key.lower(): value for key, value in request.header_items()}


class ExperiaPluginTests(unittest.TestCase):
    def test_request_retries_nested_status_auth_error(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        responses = [
            FakeResponse({"data": {"contextID": "abc"}}, {"Set-Cookie": "sid=abc; Path=/"}),
            FakeResponse(
                {
                    "status": {
                        "errors": [
                            {"error": "196614", "description": "Invalid session"}
                        ]
                    }
                }
            ),
            FakeResponse({"data": {"contextID": "def"}}, {"Set-Cookie": "sid=def; Path=/"}),
            FakeResponse({"status": {"UpTime": 123}}),
        ]

        with patch.object(module.urllib.request, "urlopen", side_effect=responses) as urlopen:
            data = plugin._request("NMC", "get", endpoint="ws")

        self.assertEqual(data, {"status": {"UpTime": 123}})
        self.assertEqual(plugin.context_id, "def")
        self.assertEqual(urlopen.call_count, 4)

    def test_request_retries_non_json_timeout_response(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        responses = [
            FakeResponse({"data": {"contextID": "abc"}}, {"Set-Cookie": "sid=abc; Path=/"}),
            FakeResponse(b"<html>Login</html>", raw=True),
            FakeResponse({"data": {"contextID": "def"}}, {"Set-Cookie": "sid=def; Path=/"}),
            FakeResponse({"status": {"UpTime": 456}}),
        ]

        with patch.object(module.urllib.request, "urlopen", side_effect=responses):
            data = plugin._request("NMC", "get", endpoint="ws")

        self.assertEqual(data, {"status": {"UpTime": 456}})
        self.assertEqual(plugin.context_id, "def")

    def test_request_does_not_retry_invalid_arguments(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        responses = [
            FakeResponse({"data": {"contextID": "abc"}}, {"Set-Cookie": "sid=abc; Path=/"}),
            FakeResponse({"errors": [{"error": "9003"}]}),
        ]

        with patch.object(module.urllib.request, "urlopen", side_effect=responses) as urlopen:
            with self.assertRaises(module.ExperiaV10ApiError):
                plugin._request("NMC", "badMethod", endpoint="ws")

        self.assertEqual(urlopen.call_count, 2)

    def test_optional_permission_denied_does_not_clear_context(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        responses = [
            FakeResponse({"data": {"contextID": "abc"}}, {"Set-Cookie": "sid=abc; Path=/"}),
            FakeResponse(
                {
                    "status": None,
                    "errors": [
                        {
                            "error": 13,
                            "description": "Permission denied",
                            "info": "NMC.Wifi",
                        }
                    ],
                }
            ),
        ]

        with patch.object(module.urllib.request, "urlopen", side_effect=responses):
            with self.assertRaises(module.ExperiaV10PermissionDeniedError):
                plugin._request("NMC.Wifi", "get", endpoint="ws")

        self.assertEqual(plugin.context_id, "abc")
        self.assertEqual(plugin.cookie, "sid=abc")

    def test_get_context_records_creation_time(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        response = FakeResponse(
            {"data": {"contextID": "abc"}},
            {"Set-Cookie": "sid=abc; Path=/"},
        )

        with (
            patch.object(module.urllib.request, "urlopen", return_value=response),
            patch.object(module.time, "monotonic", return_value=123.0),
        ):
            self.assertTrue(plugin._get_context())

        self.assertEqual(plugin.context_id, "abc")
        self.assertEqual(plugin.cookie, "sid=abc")
        self.assertEqual(plugin.context_created_at, 123.0)

    def test_request_reuses_context_before_proactive_refresh_interval(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.context_id = "abc"
        plugin.cookie = "sid=abc"
        plugin.context_created_at = 100.0
        response = FakeResponse({"status": {"UpTime": 123}})

        with (
            patch.object(module.urllib.request, "urlopen", return_value=response) as urlopen,
            patch.object(module.time, "monotonic", return_value=1000.0),
        ):
            data = plugin._request("NMC", "get", endpoint="ws")

        request = urlopen.call_args_list[0][0][0]
        headers = request_headers(request)
        self.assertEqual(data, {"status": {"UpTime": 123}})
        self.assertEqual(headers["x-context"], "abc")
        self.assertEqual(headers["cookie"], "sid=abc")
        self.assertEqual(urlopen.call_count, 1)

    def test_request_refreshes_context_before_timeout(self):
        module, domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.context_id = "abc"
        plugin.cookie = "sid=abc"
        plugin.context_created_at = 100.0
        responses = [
            FakeResponse(
                {"data": {"contextID": "def"}},
                {"Set-Cookie": "sid=def; Path=/"},
            ),
            FakeResponse({"status": {"UpTime": 456}}),
        ]

        with (
            patch.object(module.urllib.request, "urlopen", side_effect=responses) as urlopen,
            patch.object(module.time, "monotonic", return_value=1601.0),
        ):
            data = plugin._request("NMC", "get", endpoint="ws")

        login_request = urlopen.call_args_list[0][0][0]
        data_request = urlopen.call_args_list[1][0][0]
        headers = request_headers(data_request)
        self.assertEqual(request_payload(login_request)["method"], "createContext")
        self.assertEqual(data, {"status": {"UpTime": 456}})
        self.assertEqual(plugin.context_id, "def")
        self.assertEqual(plugin.cookie, "sid=def")
        self.assertEqual(plugin.context_created_at, 1601.0)
        self.assertEqual(headers["x-context"], "def")
        self.assertEqual(headers["cookie"], "sid=def")
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn(
            "Refreshing router context before session timeout",
            domoticz.debug,
        )

    def test_guest_wifi_status_falls_back_to_radio(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        calls = []

        def fake_request(service, method, parameters=None, endpoint=None, **kwargs):
            calls.append((service, method, parameters))
            if service == "NMC.Guest":
                raise module.ExperiaV10ApiError("unsupported")
            return {
                "status": [
                    {"SSID": "KPN", "UID": "private", "Enable": True},
                    {"SSID": "KPN Guest", "UID": "guest", "Enable": False},
                ]
            }

        plugin._request = fake_request

        self.assertEqual(plugin.get_guest_wifi_status(), (False, "guest"))
        self.assertEqual(calls[0][:2], ("NMC.Guest", "get"))
        self.assertEqual(calls[1][:2], ("sah.Device.WiFi.Radio", "get"))

    def test_set_guest_wifi_falls_back_to_radio_uid(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        calls = []

        def fake_request(service, method, parameters=None, endpoint=None, **kwargs):
            calls.append((service, method, parameters))
            if service == "NMC.Guest":
                raise module.ExperiaV10ApiError("unsupported")
            if service == "sah.Device.WiFi.Radio" and method == "get":
                return {
                    "status": [
                        {"SSID": "KPN Guest", "UID": "guest", "Enable": False}
                    ]
                }
            return {"status": True}

        plugin._request = fake_request
        plugin.set_guest_wifi_status(True)

        self.assertEqual(
            calls[-1],
            ("sah.Device.WiFi.Radio", "set", {"uid": "guest", "Enable": True}),
        )

    def test_throughput_and_new_device_detection(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        with patch.object(module.time, "monotonic", side_effect=[100.0, 110.0]):
            self.assertEqual(plugin._calculate_throughput(2000, 1000), (0.0, 0.0))
            self.assertEqual(plugin._calculate_throughput(4000, 2000), (200.0, 100.0))

        first_devices = [{"mac": "AA", "name": "Phone", "active": True}]
        second_devices = first_devices + [{"mac": "BB", "name": "Tablet", "active": True}]

        self.assertFalse(plugin._detect_new_devices(first_devices))
        with patch.object(module.time, "monotonic", side_effect=[200.0, 201.0]):
            self.assertTrue(plugin._detect_new_devices(second_devices))

        self.assertEqual(plugin.last_new_device_info, "Tablet (BB)")

    def test_sync_creates_diagnostic_devices(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.get_devices = Mock(
            return_value=[
                {"mac": "AA:BB", "name": "Phone", "ip": "192.168.2.10", "active": True},
                {"mac": "CC:DD", "name": "Laptop", "ip": "192.168.2.11", "active": False},
            ]
        )
        plugin.get_router_info = Mock(
            return_value={
                "model": "H369A",
                "hardware_version": "V1",
                "software_version": "V10.C.26",
                "serial_number": "SN1",
                "uptime": 3600,
            }
        )
        plugin.get_wifi_status = Mock(return_value=True)
        plugin.get_guest_wifi_status = Mock(return_value=(False, None))
        plugin.get_wan_info = Mock(
            return_value={
                "external_ip": "1.2.3.4",
                "connected": True,
                "link_status": "up",
            }
        )
        plugin.get_traffic_info = Mock(
            return_value={
                "rx_bytes": 4096,
                "tx_bytes": 2048,
                "rx_packets": 4,
                "tx_packets": 2,
            }
        )
        plugin.get_parental_control_schedules = Mock(return_value={})

        with patch.object(module.time, "monotonic", return_value=100.0):
            plugin.sync_devices()

        devices = module.Devices
        self.assertEqual(devices["CLIENT_COUNT"].Units[1].sValue, "1")
        self.assertEqual(devices["NEW_DEVICE"].Units[1].sValue, "Off")
        self.assertEqual(devices["LAST_NEW_DEVICE"].Units[1].sValue, "")
        self.assertEqual(devices["ROUTER_UPTIME"].Units[1].sValue, "3600")
        self.assertIn("H369A", devices["ROUTER_INFO"].Units[1].sValue)
        self.assertEqual(devices["WAN_LINK_STATUS"].Units[1].sValue, "up")
        self.assertEqual(devices["THROUGHPUT_DOWN_MBPS"].Units[1].sValue, "0")
        self.assertIn("REBOOT_MODEM", devices)

    def test_sync_preserves_transient_zero_uptime(self):
        module, domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.last_router_uptime = 3600
        plugin.get_router_info = Mock(
            return_value={
                "model": "H369A",
                "hardware_version": "V1",
                "software_version": "V10.C.26",
                "serial_number": "SN1",
                "uptime": 0,
            }
        )

        plugin._sync_router_info()

        self.assertEqual(module.Devices["ROUTER_UPTIME"].Units[1].sValue, "3600")
        self.assertEqual(plugin.last_router_uptime, 3600)
        self.assertFalse(plugin.router_reboot_detected)
        self.assertIn(
            "Ignoring transient zero router uptime during update",
            domoticz.debug,
        )

    def test_sync_detects_router_reboot_from_lower_nonzero_uptime(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.last_router_uptime = 3600
        plugin.get_router_info = Mock(
            return_value={
                "model": "H369A",
                "hardware_version": "V1",
                "software_version": "V10.C.26",
                "serial_number": "SN1",
                "uptime": 30,
            }
        )

        plugin._sync_router_info()

        self.assertEqual(module.Devices["ROUTER_UPTIME"].Units[1].sValue, "30")
        self.assertEqual(plugin.last_router_uptime, 30)
        self.assertTrue(plugin.router_reboot_detected)

    def test_sync_preserves_transient_zero_traffic_counters(self):
        module, domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        previous_traffic = {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
            "rx_packets": 4,
            "tx_packets": 2,
        }
        plugin.last_traffic_info = previous_traffic
        plugin.last_rx_bytes = previous_traffic["rx_bytes"]
        plugin.last_tx_bytes = previous_traffic["tx_bytes"]
        plugin.last_traffic_time = 100.0
        plugin.last_throughput_down = 1250000.0
        plugin.last_throughput_up = 625000.0
        plugin.get_traffic_info = Mock(
            return_value={
                "rx_bytes": 0,
                "tx_bytes": 0,
                "rx_packets": 0,
                "tx_packets": 0,
            }
        )

        plugin._sync_traffic_info()

        self.assertEqual(module.Devices["TRAFFIC_RX"].Units[1].sValue, "4")
        self.assertEqual(module.Devices["TRAFFIC_TX"].Units[1].sValue, "2")
        self.assertEqual(module.Devices["THROUGHPUT_DOWN_MBPS"].Units[1].sValue, "10")
        self.assertEqual(module.Devices["THROUGHPUT_UP_MBPS"].Units[1].sValue, "5")
        self.assertEqual(plugin.last_rx_bytes, 4096)
        self.assertEqual(plugin.last_tx_bytes, 2048)
        self.assertEqual(plugin.last_traffic_time, 100.0)
        self.assertEqual(plugin.last_traffic_info, previous_traffic)
        self.assertIn(
            "Ignoring transient zero traffic counters during update",
            domoticz.debug,
        )

    def test_sync_accepts_zero_traffic_counters_after_reboot(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        previous_traffic = {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
            "rx_packets": 4,
            "tx_packets": 2,
        }
        zero_traffic = {
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_packets": 0,
            "tx_packets": 0,
        }
        plugin.router_reboot_detected = True
        plugin.last_traffic_info = previous_traffic
        plugin.last_rx_bytes = previous_traffic["rx_bytes"]
        plugin.last_tx_bytes = previous_traffic["tx_bytes"]
        plugin.last_traffic_time = 100.0
        plugin.last_throughput_down = 12.0
        plugin.last_throughput_up = 6.0
        plugin.get_traffic_info = Mock(return_value=zero_traffic)

        with patch.object(module.time, "monotonic", return_value=130.0):
            plugin._sync_traffic_info()

        self.assertEqual(module.Devices["TRAFFIC_RX"].Units[1].sValue, "0")
        self.assertEqual(module.Devices["TRAFFIC_TX"].Units[1].sValue, "0")
        self.assertEqual(module.Devices["THROUGHPUT_DOWN_MBPS"].Units[1].sValue, "0")
        self.assertEqual(module.Devices["THROUGHPUT_UP_MBPS"].Units[1].sValue, "0")
        self.assertEqual(plugin.last_rx_bytes, 0)
        self.assertEqual(plugin.last_tx_bytes, 0)
        self.assertEqual(plugin.last_traffic_time, 130.0)
        self.assertEqual(plugin.last_traffic_info, zero_traffic)

    def test_optional_wifi_permission_denied_preserves_existing_switch(self):
        module, _domoticz = load_plugin()
        module.Domoticz.Unit(Name="Global Wi-Fi", DeviceID="WIFI", Unit=1, TypeName="Switch").Create()
        wifi_unit = module.Devices["WIFI"].Units[1]
        wifi_unit.nValue = 1
        wifi_unit.sValue = "On"

        plugin = module.ExperiaPlugin()
        plugin.get_devices = Mock(return_value=[])
        plugin.get_router_info = Mock(
            return_value={
                "model": "H369A",
                "hardware_version": "",
                "software_version": "",
                "serial_number": "",
                "uptime": 0,
            }
        )
        plugin.get_wifi_status = Mock(
            side_effect=module.ExperiaV10PermissionDeniedError("denied")
        )
        plugin.get_guest_wifi_status = Mock(return_value=(False, None))
        plugin.get_wan_info = Mock(
            return_value={"external_ip": "", "connected": False, "link_status": "Down"}
        )
        plugin.get_traffic_info = Mock(
            side_effect=module.ExperiaV10PermissionDeniedError("denied")
        )
        plugin.get_parental_control_schedules = Mock(return_value={})

        plugin.sync_devices()

        self.assertEqual(wifi_unit.nValue, 1)
        self.assertEqual(wifi_unit.sValue, "On")

    def test_sync_updates_legacy_throughput_devices_if_present(self):
        module, _domoticz = load_plugin()
        module.Domoticz.Unit(Name="Download Speed", DeviceID="THROUGHPUT_DOWN", Unit=1, Type=243, Subtype=31).Create()
        module.Domoticz.Unit(Name="Upload Speed", DeviceID="THROUGHPUT_UP", Unit=1, Type=243, Subtype=31).Create()

        plugin = module.ExperiaPlugin()
        plugin.get_devices = Mock(return_value=[])
        plugin.get_router_info = Mock(return_value={
            "model": "H369A", "hardware_version": "", "software_version": "", "serial_number": "", "uptime": 0
        })
        plugin.get_wifi_status = Mock(return_value=True)
        plugin.get_guest_wifi_status = Mock(return_value=(False, None))
        plugin.get_wan_info = Mock(return_value={"external_ip": "", "connected": False, "link_status": "Down"})
        plugin.get_traffic_info = Mock(return_value={
            "rx_bytes": 4096, "tx_bytes": 2048, "rx_packets": 4, "tx_packets": 2
        })
        plugin.get_parental_control_schedules = Mock(return_value={})

        plugin.sync_devices()

        devices = module.Devices
        # The legacy devices should be automatically renamed and updated
        self.assertEqual(devices["THROUGHPUT_DOWN"].Units[1].Name, "Download Speed (Legacy B/s)")
        self.assertEqual(devices["THROUGHPUT_UP"].Units[1].Name, "Upload Speed (Legacy B/s)")

        # New Mbps devices should also be created and updated
        self.assertIn("THROUGHPUT_DOWN_MBPS", devices)
        self.assertIn("THROUGHPUT_UP_MBPS", devices)

    def test_parameterized_poll_interval(self):
        module, _domoticz = load_plugin()
        module.Parameters["Mode2"] = "60"
        plugin = module.ExperiaPlugin()
        plugin.fetch_all_data = Mock(return_value={})
        plugin.onStart()
        self.assertEqual(plugin.poll_interval, 60)
        plugin.onStop()

    def test_empty_poll_interval_uses_default(self):
        module, _domoticz = load_plugin()
        module.Parameters["Mode2"] = ""
        plugin = module.ExperiaPlugin()
        plugin.fetch_all_data = Mock(return_value={})
        plugin.onStart()
        self.assertEqual(plugin.poll_interval, 30)
        plugin.onStop()

    def test_parental_control_toggle_parameter(self):
        module, _domoticz = load_plugin()
        # Default is True
        plugin = module.ExperiaPlugin()
        plugin.fetch_all_data = Mock(return_value={})
        plugin.onStart()
        self.assertTrue(plugin.enable_parental_control)
        plugin.onStop()

        # Explicit True
        module.Parameters["Mode3"] = "True"
        plugin = module.ExperiaPlugin()
        plugin.fetch_all_data = Mock(return_value={})
        plugin.onStart()
        self.assertTrue(plugin.enable_parental_control)
        plugin.onStop()

        # Explicit False
        module.Parameters["Mode3"] = "False"
        plugin = module.ExperiaPlugin()
        plugin.fetch_all_data = Mock(return_value={})
        plugin.onStart()
        self.assertFalse(plugin.enable_parental_control)
        plugin.onStop()

    def test_parental_control_disabled_behavior(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.enable_parental_control = False

        # 1. fetch_all_data does not query parental schedules
        with patch.object(plugin, "get_parental_control_schedules") as mock_pc:
            plugin.get_devices = Mock(return_value=[])
            plugin.get_router_info = Mock(return_value={})
            plugin.get_wifi_status = Mock(return_value=True)
            plugin.get_guest_wifi_status = Mock(return_value=(False, None))
            plugin.get_wan_info = Mock(return_value={})
            plugin.get_traffic_info = Mock(return_value={})
            data = plugin.fetch_all_data()
            mock_pc.assert_not_called()
            self.assertIsNone(data["parental_schedules"])

        # 2. sync_devices does not query parental schedules
        with patch.object(plugin, "get_parental_control_schedules") as mock_pc:
            plugin.get_devices = Mock(return_value=[])
            plugin.get_router_info = Mock(return_value={"model": "M", "hardware_version": "H", "software_version": "S", "serial_number": "SN", "uptime": 100})
            plugin.get_wifi_status = Mock(return_value=True)
            plugin.get_guest_wifi_status = Mock(return_value=(False, None))
            plugin.get_wan_info = Mock(return_value={})
            plugin.get_traffic_info = Mock(return_value={})
            plugin.sync_devices(data=None)
            mock_pc.assert_not_called()

        # 3. _sync_tracked_devices does not create Unit 2
        devices = [{"mac": "AA:BB:CC:DD:EE:01", "name": "Phone", "ip": "192.168.2.10", "active": True}]
        plugin._sync_tracked_devices(devices, parental_schedules=None)
        devs = module.Devices
        self.assertIn(1, devs["AA:BB:CC:DD:EE:01"].Units)
        self.assertNotIn(2, devs["AA:BB:CC:DD:EE:01"].Units)

        # 4. onCommand for Unit 2 is ignored
        with patch.object(plugin, "set_parental_control") as mock_set:
            plugin.onCommand("AA:BB:CC:DD:EE:01", 2, "On", 0, "")
            mock_set.assert_not_called()

        # 5. onDeviceModified for Unit 1 does not touch Unit 2 if it existed
        module.Domoticz.Unit(Name="Old Unit 2", DeviceID="AA:BB:CC:DD:EE:01", Unit=2, TypeName="Switch").Create()
        devs["AA:BB:CC:DD:EE:01"].Units[1].Name = "New Phone Name"
        with patch.object(plugin, "_request"):
            plugin.onDeviceModified("AA:BB:CC:DD:EE:01", 1)
        self.assertEqual(devs["AA:BB:CC:DD:EE:01"].Units[2].Name, "Old Unit 2")

    def test_on_heartbeat_processes_queue(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        task_called = []
        plugin.queue.put(lambda: task_called.append(True))

        plugin.onHeartbeat()

        self.assertTrue(plugin.queue.empty())
        self.assertEqual(task_called, [True])

    def test_on_command_queues_unit_update(self):
        module, _domoticz = load_plugin()
        module.Domoticz.Unit(Name="Global Wi-Fi", DeviceID="WIFI", Unit=1, TypeName="Switch").Create()
        plugin = module.ExperiaPlugin()
        plugin.set_wifi_status = Mock()

        plugin.onCommand("WIFI", 1, "on", 0, "")

        # Wait for command thread to finish
        plugin.onStop()

        self.assertEqual(plugin.set_wifi_status.call_count, 1)
        self.assertEqual(plugin.set_wifi_status.call_args[0][0], True)

        # The queue should contain the task, and executing it updates the unit
        self.assertFalse(plugin.queue.empty())
        plugin.onHeartbeat()
        self.assertEqual(module.Devices["WIFI"].Units[1].sValue, "On")
        self.assertEqual(module.Devices["WIFI"].Units[1].nValue, 1)

    def test_poll_loop_queues_sync(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        def fake_fetch_all_data():
            plugin.stop_event.set()
            return {"devices": []}

        plugin.fetch_all_data = fake_fetch_all_data
        plugin.sync_devices = Mock()
        plugin.poll_interval = 0.01

        plugin.poll_loop()

        self.assertTrue(plugin.stop_event.is_set())
        self.assertFalse(plugin.queue.empty())
        plugin.onHeartbeat()
        self.assertEqual(plugin.sync_devices.call_count, 1)

    def test_parse_devices_classification(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        raw_devices = [
            # A non-dict element to verify robustness
            "not-a-dict",
            # Active Wi-Fi device using Tags
            {
                "PhysAddress": "aa:bb:cc:dd:ee:11",
                "Name": "My iPhone",
                "IPAddress": "192.168.42.50",
                "Active": True,
                "Tags": "lan edev mac physical wifi ipv4",
                "InterfaceName": "wl0.1",
            },
            # Inactive Wi-Fi device using InterfaceName
            {
                "PhysAddress": "AA:BB:CC:DD:EE:22",
                "Key": "Laptop-Key",
                "IPAddress": "192.168.42.51",
                "Active": False,
                "Tags": "lan edev mac physical",
                "InterfaceName": "wl1",
            },
            # Active Wired device using Tags and InterfaceName
            {
                "PhysAddress": "11:22:33:44:55:66",
                "IPAddress": "192.168.42.100",
                "Active": True,
                "Tags": "lan edev mac physical eth ipv4",
                "InterfaceName": "eth0",
            },
            # Record missing PhysAddress
            {
                "Name": "Ghost Device",
                "IPAddress": "192.168.42.200",
                "Active": True,
            }
        ]

        # Scenario A: track_wired = False (wired device should be excluded)
        results = {}
        plugin._parse_devices(raw_devices, track_wired_devices=False, results=results)

        self.assertIn("AA:BB:CC:DD:EE:11", results)
        self.assertEqual(results["AA:BB:CC:DD:EE:11"]["name"], "My iPhone")
        self.assertEqual(results["AA:BB:CC:DD:EE:11"]["ip"], "192.168.42.50")
        self.assertTrue(results["AA:BB:CC:DD:EE:11"]["active"])

        self.assertIn("AA:BB:CC:DD:EE:22", results)
        self.assertEqual(results["AA:BB:CC:DD:EE:22"]["name"], "Laptop-Key")  # Key fallback
        self.assertEqual(results["AA:BB:CC:DD:EE:22"]["ip"], "192.168.42.51")
        self.assertFalse(results["AA:BB:CC:DD:EE:22"]["active"])

        # Wired device should NOT be present
        self.assertNotIn("11:22:33:44:55:66", results)

        # Scenario B: track_wired = True (wired device should be included)
        results_with_wired = {}
        plugin._parse_devices(raw_devices, track_wired_devices=True, results=results_with_wired)
        self.assertIn("11:22:33:44:55:66", results_with_wired)
        self.assertEqual(results_with_wired["11:22:33:44:55:66"]["name"], "11:22:33:44:55:66")  # MAC fallback
        self.assertEqual(results_with_wired["11:22:33:44:55:66"]["ip"], "192.168.42.100")
        self.assertTrue(results_with_wired["11:22:33:44:55:66"]["active"])

    def test_parse_topology_recursive(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        nested_topology = [
            {
                "Key": "bridge-node",
                "Tags": "self lan mac nemo bridge",
                "Active": True,
                "Children": [
                    # A wireless vap (has no MAC address of its own but children inherit wifi)
                    {
                        "Key": "wireless-vap",
                        "Tags": "self lan vap wifi nemo",
                        "Active": True,
                        "Children": [
                            # Device under vap (wireless child)
                            {
                                "PhysAddress": "aa:bb:cc:dd:ee:33",
                                "Name": "Wireless-Child",
                                "IPAddress": "192.168.42.10",
                                "Active": True,
                                "Tags": "lan edev mac physical",
                            }
                        ]
                    },
                    # A wired child on the lan bridge
                    {
                        "PhysAddress": "aa:bb:cc:dd:ee:44",
                        "Name": "Wired-Child",
                        "IPAddress": "192.168.42.20",
                        "Active": True,
                        "Tags": "lan edev mac physical eth",
                        "InterfaceName": "eth1",
                    }
                ]
            }
        ]

        # Scenario A: track_wired = False
        results = {}
        plugin._parse_topology(nested_topology, track_wired_devices=False, results=results)

        # Wireless child should be parsed successfully (inherits parent_is_wifi=True)
        self.assertIn("AA:BB:CC:DD:EE:33", results)
        self.assertEqual(results["AA:BB:CC:DD:EE:33"]["name"], "Wireless-Child")

        # Wired child should be excluded
        self.assertNotIn("AA:BB:CC:DD:EE:44", results)

        # Scenario B: track_wired = True
        results_all = {}
        plugin._parse_topology(nested_topology, track_wired_devices=True, results=results_all)
        self.assertIn("AA:BB:CC:DD:EE:33", results_all)
        self.assertIn("AA:BB:CC:DD:EE:44", results_all)
        self.assertEqual(results_all["AA:BB:CC:DD:EE:44"]["name"], "Wired-Child")

    def test_get_devices_api_fallbacks(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.track_wired = False

        # Scenario A: Flat query succeeds and returns devices
        responses_flat_ok = [
            {"status": [{"PhysAddress": "aa:bb:cc:dd:ee:11", "Active": True, "Tags": "wifi"}]},
            {"status": []},  # Second flat query (inactive)
        ]
        with patch.object(plugin, "_request", side_effect=responses_flat_ok) as mock_request:
            devices = plugin.get_devices()
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0]["mac"], "AA:BB:CC:DD:EE:11")
            self.assertEqual(mock_request.call_count, 2)

        # Scenario B: Flat query returns empty list. Fallback to LAN and guest topology.
        responses_fallback = [
            {"status": []},  # First flat query (active)
            {"status": []},  # Second flat query (inactive)
            # LAN Topology response containing a wifi device
            {
                "status": [
                    {
                        "PhysAddress": "aa:bb:cc:dd:ee:22",
                        "Active": True,
                        "Tags": "wifi",
                    }
                ]
            },
            # Guest Topology response empty
            {"status": []},
        ]
        with patch.object(plugin, "_request", side_effect=responses_fallback) as mock_request:
            devices = plugin.get_devices()
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0]["mac"], "AA:BB:CC:DD:EE:22")
            # 2 flat queries + 2 fallback queries
            self.assertEqual(mock_request.call_count, 4)

        # Scenario C: Queries completely fail. It should raise the final Exception.
        responses_fail = [
            module.ExperiaV10ApiError("Flat query 1 failed"),
            module.ExperiaV10ApiError("Flat query 2 failed"),
            module.ExperiaV10ApiError("LAN topology failed"),
            module.ExperiaV10ApiError("Guest topology failed"),
        ]
        with patch.object(plugin, "_request", side_effect=responses_fail) as mock_request:
            with self.assertRaises(module.ExperiaV10ApiError) as context:
                plugin.get_devices()
            self.assertIn("Guest topology failed", str(context.exception))
            self.assertEqual(mock_request.call_count, 4)

    def test_get_router_info_all_paths(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        # Path 1: Primary query succeeds
        with patch.object(plugin, "_request", return_value={"status": {"ModelName": "H369A", "UpTime": 1000}}) as mock_request:
            info = plugin.get_router_info()
            self.assertEqual(info["model"], "H369A")
            self.assertEqual(info["uptime"], 1000)
            mock_request.assert_called_once_with("DeviceInfo", "get", endpoint="ws")

        # Path 2: Primary returns none/empty, falls back to Nemo query
        responses_nemo = [
            {"status": {}},  # DeviceInfo empty
            {"status": [{"ProductClass": "v10", "UpTime": 2000}]}  # Nemo fallback list
        ]
        with patch.object(plugin, "_request", side_effect=responses_nemo) as mock_request:
            info = plugin.get_router_info()
            self.assertEqual(info["model"], "v10")
            self.assertEqual(info["uptime"], 2000)
            self.assertEqual(mock_request.call_count, 2)

        # Path 3: Primary returns 0 uptime, falls back to NMC query
        responses_nmc = [
            {"status": {"ModelName": "H369A", "UpTime": 0}},  # DeviceInfo returns 0 uptime
            {"status": {"UpTime": 3000}}  # NMC query for uptime
        ]
        with patch.object(plugin, "_request", side_effect=responses_nmc) as mock_request:
            info = plugin.get_router_info()
            self.assertEqual(info["model"], "H369A")
            self.assertEqual(info["uptime"], 3000)
            self.assertEqual(mock_request.call_count, 2)

    def test_get_wan_info(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        # Success case
        with patch.object(plugin, "_request", return_value={"status": True, "data": {"IPAddress": "8.8.8.8", "LinkState": "up"}}) as mock_request:
            wan = plugin.get_wan_info()
            self.assertEqual(wan["external_ip"], "8.8.8.8")
            self.assertTrue(wan["connected"])
            self.assertEqual(wan["link_status"], "up")

        # Failure/offline case
        with patch.object(plugin, "_request", return_value={"status": False}) as mock_request:
            wan = plugin.get_wan_info()
            self.assertEqual(wan["external_ip"], "")
            self.assertFalse(wan["connected"])
            self.assertEqual(wan["link_status"], "Down")

    def test_get_traffic_info(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        with patch.object(plugin, "_request", return_value={"status": {"RxBytes": 100, "TxBytes": 200, "RxPackets": 10, "TxPackets": 20}}):
            traffic = plugin.get_traffic_info()
            self.assertEqual(traffic["rx_bytes"], 100)
            self.assertEqual(traffic["tx_bytes"], 200)
            self.assertEqual(traffic["rx_packets"], 10)
            self.assertEqual(traffic["tx_packets"], 20)

    def test_get_wifi_status_and_set_wifi_status(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        # get_wifi_status wifi on
        with patch.object(plugin, "_request", return_value={"status": {"DisableLocalWiFi": False}}):
            self.assertTrue(plugin.get_wifi_status())

        # get_wifi_status wifi off
        with patch.object(plugin, "_request", return_value={"status": {"DisableLocalWiFi": True}}):
            self.assertFalse(plugin.get_wifi_status())

        # set_wifi_status
        with patch.object(plugin, "_request", return_value={}) as mock_request:
            plugin.set_wifi_status(True)
            self.assertEqual(mock_request.call_count, 5)

    def test_fetch_all_data_all_exceptions(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        plugin.get_devices = Mock(side_effect=Exception("devices failed"))
        plugin.get_router_info = Mock(side_effect=Exception("router failed"))
        plugin.get_wifi_status = Mock(side_effect=module.ExperiaV10PermissionDeniedError("wifi perm denied"))
        plugin.get_guest_wifi_status = Mock(side_effect=module.ExperiaV10PermissionDeniedError("guest perm denied"))
        plugin.get_wan_info = Mock(side_effect=Exception("wan failed"))
        plugin.get_traffic_info = Mock(side_effect=module.ExperiaV10PermissionDeniedError("traffic perm denied"))
        plugin.get_parental_control_schedules = Mock(side_effect=module.ExperiaV10PermissionDeniedError("parental perm denied"))

        data = plugin.fetch_all_data()
        self.assertIsNone(data["devices"])
        self.assertIsNone(data["router_info"])
        self.assertIsNone(data["wifi_on"])
        self.assertIsNone(data["guest_wifi"])
        self.assertIsNone(data["wan_info"])
        self.assertIsNone(data["traffic_info"])
        self.assertIsNone(data["parental_schedules"])

    def test_sync_devices_synchronous(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        plugin.get_devices = Mock(return_value=[])
        plugin.get_router_info = Mock(return_value={"model": "M", "hardware_version": "H", "software_version": "S", "serial_number": "SN", "uptime": 100})
        plugin.get_wifi_status = Mock(return_value=True)
        plugin.get_guest_wifi_status = Mock(return_value=(False, None))
        plugin.get_wan_info = Mock(return_value={"external_ip": "1.1.1.1", "connected": True, "link_status": "up"})
        plugin.get_traffic_info = Mock(return_value={"rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0})
        plugin.get_parental_control_schedules = Mock(return_value={})

        # Test successful sync_devices(None)
        plugin.sync_devices(data=None)
        self.assertIn("CLIENT_COUNT", module.Devices)

        # Test sync_devices(None) with exceptions to cover catch-all branches
        plugin.get_devices = Mock(side_effect=Exception("devices err"))
        plugin.get_router_info = Mock(side_effect=Exception("router err"))
        plugin.get_wifi_status = Mock(side_effect=module.ExperiaV10PermissionDeniedError("wifi perm err"))
        plugin.get_guest_wifi_status = Mock(side_effect=module.ExperiaV10PermissionDeniedError("guest perm err"))
        plugin.get_wan_info = Mock(side_effect=Exception("wan err"))
        plugin.get_traffic_info = Mock(side_effect=module.ExperiaV10PermissionDeniedError("traffic perm err"))
        plugin.get_parental_control_schedules = Mock(side_effect=module.ExperiaV10PermissionDeniedError("parental perm err"))

        plugin.sync_devices(data=None)  # Should not raise exception

    def test_sync_devices_asynchronous_exceptions(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        plugin._sync_router_info = Mock(side_effect=Exception("router info sync err"))
        plugin._sync_wifi_status = Mock(side_effect=Exception("wifi sync err"))
        plugin._sync_guest_wifi_status = Mock(side_effect=Exception("guest wifi sync err"))
        plugin._sync_wan_info = Mock(side_effect=Exception("wan sync err"))
        plugin._sync_traffic_info = Mock(side_effect=Exception("traffic sync err"))

        data = {
            "devices": [],
            "router_info": {},
            "wifi_on": True,
            "guest_wifi": (False, None),
            "wan_info": {},
            "traffic_info": {}
        }
        plugin.sync_devices(data=data) # Should run completely and catch all exceptions

    def test_on_device_modified_ignores_special_devices(self):
        module, _domoticz = load_plugin()
        module.Domoticz.Unit(Name="Global Wi-Fi", DeviceID="WIFI", Unit=1, TypeName="Switch").Create()

        plugin = module.ExperiaPlugin()
        plugin._request = Mock()

        plugin.onDeviceModified("WIFI", 1)
        self.assertEqual(len(plugin.command_threads), 0)
        plugin._request.assert_not_called()

    def test_on_device_modified_pushes_name_change_to_router(self):
        module, _domoticz = load_plugin()
        mac = "AA:BB:CC:DD:EE:FF"
        module.Domoticz.Unit(Name="Phone New", DeviceID=mac, Unit=1, TypeName="Switch").Create()

        plugin = module.ExperiaPlugin()
        calls = []

        def fake_request(service, method, parameters=None, endpoint=None, **kwargs):
            calls.append((service, method, parameters))
            if method == "get":
                return {"status": {"Name": "Phone Old"}}
            return {"status": True}

        plugin._request = fake_request
        plugin.onDeviceModified(mac, 1)

        for thread in plugin.command_threads:
            thread.join(timeout=2)

        self.assertEqual(
            calls,
            [
                (f"Devices.Device.{mac}", "get", {"flags": "full_links"}),
                (f"Devices.Device.{mac}", "setName", {"name": "Phone New"}),
            ],
        )

    def test_on_device_modified_ignores_matching_name(self):
        module, _domoticz = load_plugin()
        mac = "AA:BB:CC:DD:EE:FF"
        module.Domoticz.Unit(Name="Phone Same", DeviceID=mac, Unit=1, TypeName="Switch").Create()

        plugin = module.ExperiaPlugin()
        calls = []

        def fake_request(service, method, parameters=None, endpoint=None, **kwargs):
            calls.append((service, method, parameters))
            return {"status": {"Name": "Phone Same"}}

        plugin._request = fake_request
        plugin.onDeviceModified(mac, 1)

        for thread in plugin.command_threads:
            thread.join(timeout=2)

        self.assertEqual(
            calls,
            [
                (f"Devices.Device.{mac}", "get", {"flags": "full_links"}),
            ],
        )

    def test_get_devices_raises_when_all_discovery_endpoints_fail(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin._request = Mock(side_effect=module.ExperiaV10ApiError("API failure"))

        with self.assertRaises(module.ExperiaV10ApiError):
            plugin.get_devices()

    def test_get_devices_returns_empty_list_for_successful_empty_discovery(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin._request = Mock(return_value={"status": []})

        self.assertEqual(plugin.get_devices(), [])

    def test_get_devices_keeps_successful_partial_results(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        responses = [
            {
                "status": [
                    {
                        "PhysAddress": "AA:BB:CC:DD:EE:FF",
                        "Name": "Phone",
                        "IPAddress": "192.168.2.10",
                        "Active": True,
                        "Tags": "wifi",
                    }
                ]
            },
            module.ExperiaV10ApiError("second query failed"),
        ]

        def fake_request(*args, **kwargs):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        plugin._request = fake_request
        devices = plugin.get_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["mac"], "AA:BB:CC:DD:EE:FF")

    def test_get_parental_control_schedules_parsing(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        fake_response = {
            "status": True,
            "data": {
                "scheduleInfo": [
                    {
                        "ID": "AA:BB:CC:DD:EE:01",
                        "enable": True,
                        "override": "Disable",
                        "value": "Disable",
                        "schedule": [],
                    },
                    {
                        "ID": "AA:BB:CC:DD:EE:02",
                        "enable": False,
                        "override": "",
                        "value": "Enable",
                        "schedule": [],
                    },
                    {
                        "ID": "AA:BB:CC:DD:EE:03",
                        "enable": True,
                        "override": "",
                        "value": "Enable",
                        "schedule": [{"state": "Disable", "begin": 36000, "end": 43200}],
                    },
                ]
            },
        }

        with patch.object(plugin, "_request", return_value=fake_response) as mock_req:
            schedules = plugin.get_parental_control_schedules()
            mock_req.assert_called_once_with(
                service="Scheduler",
                method="getCompleteSchedules",
                parameters={"type": "ToD"},
                endpoint="ws/NeMo/Intf/lan:getMIBs",
            )

        self.assertFalse(schedules["AA:BB:CC:DD:EE:01"]["scheduled"])
        self.assertTrue(schedules["AA:BB:CC:DD:EE:01"]["blocked"])

        self.assertFalse(schedules["AA:BB:CC:DD:EE:02"]["scheduled"])
        self.assertFalse(schedules["AA:BB:CC:DD:EE:02"]["blocked"])

        self.assertTrue(schedules["AA:BB:CC:DD:EE:03"]["scheduled"])
        self.assertFalse(schedules["AA:BB:CC:DD:EE:03"]["blocked"])

    def test_set_parental_control_block_existing_and_new(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        # Case 1: schedule exists -> overrideSchedule
        calls = []

        def fake_req(service, method, parameters=None, endpoint="ws"):
            calls.append((service, method, parameters, endpoint))
            if method == "getSchedule":
                return {"status": True, "data": {}}
            return {"status": True}

        with patch.object(plugin, "_request", side_effect=fake_req):
            plugin.set_parental_control("AA:BB:CC:DD:EE:01", block=True)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "getSchedule")
        self.assertEqual(calls[1][1], "overrideSchedule")
        self.assertEqual(calls[1][2]["override"], "Disable")

        # Case 2: schedule does not exist -> addSchedule
        calls.clear()

        def fake_req_not_found(service, method, parameters=None, endpoint="ws"):
            calls.append((service, method, parameters, endpoint))
            if method == "getSchedule":
                return {"status": False}
            return {"status": True}

        with patch.object(plugin, "_request", side_effect=fake_req_not_found):
            plugin.set_parental_control("AA:BB:CC:DD:EE:02", block=True)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "getSchedule")
        self.assertEqual(calls[1][1], "addSchedule")
        self.assertEqual(calls[1][2]["info"]["override"], "Disable")

    def test_set_parental_control_unblock(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        calls = []

        def fake_req(service, method, parameters=None, endpoint="ws"):
            calls.append((service, method, parameters, endpoint))
            return {"status": True}

        with patch.object(plugin, "_request", side_effect=fake_req):
            plugin.set_parental_control("AA:BB:CC:DD:EE:01", block=False)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "overrideSchedule")
        self.assertEqual(calls[0][2]["override"], "Enable")
        self.assertEqual(calls[1][1], "removeSchedules")
        self.assertEqual(calls[1][2]["ID"], ["AA:BB:CC:DD:EE:01"])

    def test_sync_tracked_devices_creates_and_updates_parental_units(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        devices = [
            {"mac": "AA:BB:CC:DD:EE:01", "name": "Phone", "ip": "192.168.2.10", "active": True},
            {"mac": "AA:BB:CC:DD:EE:02", "name": "Tablet", "ip": "192.168.2.11", "active": False},
        ]
        schedules = {
            "AA:BB:CC:DD:EE:01": {"scheduled": False, "blocked": True},
            "AA:BB:CC:DD:EE:02": {"scheduled": True, "blocked": False},
        }

        plugin._sync_tracked_devices(devices, schedules)

        devs = module.Devices
        phone_u1 = devs["AA:BB:CC:DD:EE:01"].Units[1]
        phone_u2 = devs["AA:BB:CC:DD:EE:01"].Units[2]
        self.assertEqual(phone_u1.Name, "Phone")
        self.assertEqual(phone_u1.sValue, "On")
        self.assertEqual(phone_u2.Name, "Phone - Internet Access")
        self.assertEqual(phone_u2.sValue, "Off")
        self.assertEqual(phone_u2.TypeName, "Switch")
        self.assertEqual(phone_u2.SwitchType, 0)
        self.assertNotIn("AA:BB:CC:DD:EE:01", plugin.scheduled_macs)

        tab_u1 = devs["AA:BB:CC:DD:EE:02"].Units[1]
        tab_u2 = devs["AA:BB:CC:DD:EE:02"].Units[2]
        self.assertEqual(tab_u1.Name, "Tablet")
        self.assertEqual(tab_u1.sValue, "Off")
        self.assertEqual(tab_u2.Name, "Tablet - Internet Access")
        self.assertEqual(tab_u2.sValue, "On")
        self.assertEqual(tab_u2.TypeName, "Contact")
        self.assertEqual(tab_u2.SwitchType, 2)
        self.assertIn("AA:BB:CC:DD:EE:02", plugin.scheduled_macs)

        # Router changes name of Phone -> 'Vincent Phone'
        devices[0]["name"] = "Vincent Phone"
        plugin._sync_tracked_devices(devices, schedules)

        self.assertEqual(phone_u1.Name, "Vincent Phone")
        self.assertEqual(phone_u2.Name, "Vincent Phone - Internet Access")

        # Tablet transitions from scheduled to manual
        schedules["AA:BB:CC:DD:EE:02"] = {"scheduled": False, "blocked": False}
        plugin._sync_tracked_devices(devices, schedules)
        self.assertEqual(tab_u2.TypeName, "Switch")
        self.assertEqual(tab_u2.SwitchType, 0)
        self.assertNotIn("AA:BB:CC:DD:EE:02", plugin.scheduled_macs)

    def test_on_command_unit_2_scheduled_rejection(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()
        plugin.scheduled_macs.add("AA:BB:CC:DD:EE:01")

        module.Domoticz.Unit(
            Name="Phone - Internet Access",
            DeviceID="AA:BB:CC:DD:EE:01",
            Unit=2,
            TypeName="Contact",
        ).Create()

        with patch.object(plugin, "set_parental_control") as mock_set:
            plugin.onCommand("AA:BB:CC:DD:EE:01", 2, "Off", 0, "")
            mock_set.assert_not_called()

        errors = [err for err in _domoticz.errors if "Cannot toggle Internet Access" in err]
        self.assertEqual(len(errors), 1)

    def test_on_command_unit_2_manual_switch(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        module.Domoticz.Unit(
            Name="Phone - Internet Access",
            DeviceID="AA:BB:CC:DD:EE:01",
            Unit=2,
            TypeName="Switch",
        ).Create()

        with patch.object(plugin, "set_parental_control") as mock_set:
            plugin.onCommand("AA:BB:CC:DD:EE:01", 2, "Off", 0, "")
            for t in list(plugin.command_threads):
                t.join(timeout=1.0)
            mock_set.assert_called_once_with("AA:BB:CC:DD:EE:01", True)

        plugin.onHeartbeat()
        u2 = module.Devices["AA:BB:CC:DD:EE:01"].Units[2]
        self.assertEqual(u2.sValue, "Off")
        self.assertEqual(u2.nValue, 0)

    def test_on_device_modified_unit_1_updates_unit_2_and_router(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        u1 = module.Domoticz.Unit(Name="Phone", DeviceID="AA:BB:CC:DD:EE:01", Unit=1, TypeName="Switch").Create()
        u2 = module.Domoticz.Unit(Name="Phone - Internet Access", DeviceID="AA:BB:CC:DD:EE:01", Unit=2, TypeName="Switch").Create()

        u1.Name = "New Phone Name"

        router_calls = []

        def fake_req(service, method, parameters=None, endpoint="ws"):
            router_calls.append((service, method, parameters))
            if method == "get":
                return {"status": {"Name": "Phone"}}
            return {"status": True}

        with patch.object(plugin, "_request", side_effect=fake_req):
            plugin.onDeviceModified("AA:BB:CC:DD:EE:01", 1)
            for t in list(plugin.command_threads):
                t.join(timeout=1.0)

        # Unit 2 name updated in Domoticz
        self.assertEqual(u2.Name, "New Phone Name - Internet Access")
        # Router received setName call with new name
        set_name_calls = [c for c in router_calls if c[1] == "setName"]
        self.assertEqual(len(set_name_calls), 1)
        self.assertEqual(set_name_calls[0][2], {"name": "New Phone Name"})

    def test_on_device_modified_unit_2_ignored(self):
        module, _domoticz = load_plugin()
        plugin = module.ExperiaPlugin()

        module.Domoticz.Unit(Name="Phone", DeviceID="AA:BB:CC:DD:EE:01", Unit=1, TypeName="Switch").Create()
        module.Domoticz.Unit(Name="Custom Internet Name", DeviceID="AA:BB:CC:DD:EE:01", Unit=2, TypeName="Switch").Create()

        with patch.object(plugin, "_request") as mock_req:
            plugin.onDeviceModified("AA:BB:CC:DD:EE:01", 2)
            for t in list(plugin.command_threads):
                t.join(timeout=1.0)
            mock_req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
