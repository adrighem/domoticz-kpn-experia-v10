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
        self.Subtype = kwargs.get("Subtype", 0)
        self.Switchtype = kwargs.get("Switchtype", 0)
        self.TypeName = kwargs.get("TypeName")
        self.Options = kwargs.get("Options", {})
        self.nValue = 0
        self.sValue = ""
        self.updates = []
        self.deleted = False

    def Update(self, **kwargs):
        self.updates.append(kwargs)

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


if __name__ == "__main__":
    unittest.main()
