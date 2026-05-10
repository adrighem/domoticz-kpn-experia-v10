# Domoticz KPN Experia Box V10 Plugin

This is a Python plugin for Domoticz to track devices connected to a KPN Experia Box V10. 
It creates a presence switch (On/Off) for devices connected to the router.

## Features (Planned)
* Authenticates with the KPN Experia Box V10 web interface.
* Periodically fetches connected devices (LAN & WLAN).
* Creates and updates Domoticz switch devices to indicate presence.

## Prerequisites
* Domoticz running with Python plugin support enabled.
* Access to the KPN Experia Box V10 web interface (IP address, Username, and Password).

## Installation

1. Clone this repository into your Domoticz plugins directory:

```bash
cd domoticz/plugins
git clone https://github.com/adrighem/domoticz-kpn-experia-v10.git experiav10
```

2. Restart the Domoticz service:

```bash
sudo systemctl restart domoticz
```

3. Go to the Domoticz interface -> **Setup** -> **Hardware**.
4. Add new hardware of type: **KPN Experia Box V10 Device Tracker**.
5. Fill in the **Router IP Address**, **Username**, and **Password**.
6. Set the desired **Update interval** and click **Add**.

## Development
This repository provides the skeleton. The core logic for communicating with the Experia Box V10 is to be implemented.

## License
This project is licensed under the GPLv3 License - see the [LICENSE](LICENSE) file for details.
