<div align="center">
  <img src="logo.png" alt="KPN Experia Box V10" width="200" />
</div>

# Domoticz KPN Experia Box V10 Device Tracker

Welcome! This is a little Python plugin for Domoticz that tracks the presence of devices connected to your trusty KPN Experia Box V10. 

It periodically asks your router who is home, and flips a Domoticz switch for each MAC address it finds. Simple, effective, and hopefully magical! 🪄

## 🚀 Features (Coming Soon!)
* Talks to the Experia Box V10 web interface.
* Tracks LAN and WLAN devices.
* Auto-creates Domoticz switches for presence detection.

## 🛠️ Installation

1. Grab the plugin and toss it into your Domoticz plugins folder:
   ```bash
   cd domoticz/plugins
   git clone https://github.com/adrighem/domoticz-kpn-experia-v10.git experiav10
   ```
2. Give Domoticz a quick reboot:
   ```bash
   sudo systemctl restart domoticz
   ```
3. Head to **Setup** -> **Hardware** in your Domoticz UI.
4. Add **KPN Experia Box V10 Device Tracker**.
5. Feed it your Router IP, Username, and Password.
6. Click **Add** and watch the magic happen! ✨

## 📜 License
Licensed under [GPLv3](LICENSE) — free to use, share, and improve.
