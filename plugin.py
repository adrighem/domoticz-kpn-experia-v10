"""
<plugin key="ExperiaV10" name="KPN Experia Box V10 Device Tracker" author="adrighem" version="1.0.0" wikilink="https://github.com/adrighem/domoticz-kpn-experia-v10" externallink="https://github.com/adrighem/domoticz-kpn-experia-v10">
    <description>
        <h2>KPN Experia Box V10 Device Tracker</h2><br/>
        This plugin tracks the presence of devices connected to a KPN Experia Box V10 router.
        It creates a switch device for each tracked MAC address, indicating whether the device is online or offline.
        <br/>
        <h3>Configuration</h3>
        Please configure the router's IP address, username, and password below.
    </description>
    <params>
        <param field="Address" label="Router IP Address" width="200px" required="true" default="192.168.2.254"/>
        <param field="Username" label="Username" width="200px" required="true" default="Admin"/>
        <param field="Password" label="Password" width="200px" required="true" password="true" default=""/>
        <param field="Mode1" label="Update interval (seconds)" width="100px" required="true" default="30"/>
        <param field="Mode6" label="Debug" width="75px">
            <options>
                <option label="True" value="Debug"/>
                <option label="False" value="Normal" default="true" />
            </options>
        </param>
    </params>
</plugin>
"""
import Domoticz

class BasePlugin:
    enabled = False
    def __init__(self):
        # self.var = 123
        return

    def onStart(self):
        if Parameters["Mode6"] == "Debug":
            Domoticz.Debugging(1)
            DumpConfigToLog()
        
        Domoticz.Log("onStart called")
        # Ensure we check the router status at the specified interval
        interval = int(Parameters["Mode1"])
        if interval < 10:
            interval = 10
        Domoticz.Heartbeat(interval)

    def onStop(self):
        Domoticz.Log("onStop called")

    def onConnect(self, Connection, Status, Description):
        Domoticz.Log("onConnect called")

    def onMessage(self, Connection, Data):
        Domoticz.Log("onMessage called")

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Log("onCommand called for Unit " + str(Unit) + ": Parameter '" + str(Command) + "', Level: " + str(Level))

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Log("Notification: " + Name + "," + Subject + "," + Text + "," + Status + "," + str(Priority) + "," + Sound + "," + ImageFile)

    def onDisconnect(self, Connection):
        Domoticz.Log("onDisconnect called")

    def onHeartbeat(self):
        Domoticz.Log("onHeartbeat called")
        # TODO: Implement the logic to fetch devices from Experia V10 and update Domoticz devices
        # Example:
        # 1. Login to Experia Box
        # 2. Fetch connected devices (LAN/WLAN)
        # 3. Update presence switches in Domoticz

global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

    # Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug( "'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return
