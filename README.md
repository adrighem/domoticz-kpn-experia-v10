<div align="center">
  <img src="logo.png" alt="KPN Experia Box V10" width="200" />
</div>

# Domoticz KPN Experia Box V10 Device Tracker

Welcome! This is a Python plugin for Domoticz designed to track the presence of devices connected to your trusty KPN Experia Box V10, along with a bunch of other handy metrics and controls.

It periodically asks your router who is home, and flips a Domoticz switch for each MAC address it finds. Simple, effective, and hopefully magical! 🪄

## 🚀 Features
* **Who's home?** Tracks all connected wireless devices (and optionally wired devices) using network topology traversal.
* **The Big Buttons:** Reboot the router, toggle Global Wi-Fi, or toggle Guest Wi-Fi straight from Domoticz.
* **Vitals:** Monitors your Internet Connection status and your External IP address.
* **Speed & Greed:** Keeps track of total data received and sent (Incremental counters in MB).

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
