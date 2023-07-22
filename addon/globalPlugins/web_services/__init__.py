# *-* coding: utf-8 *-*
# translate/__init__.py
#A part of the NVDA Translate add-on
#Copyright (C) 2018 Yannick PLASSIARD
#This file is covered by the GNU General Public License.
#See the file LICENSE for more details.
import os, sys, time, codecs, re
import importlib
import globalVars
import globalPluginHandler, logHandler, scriptHandler
import api, controlTypes
import ui, wx, gui
import core, config
import wx
import speech
from speech import *
import json
import queue
curDir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, curDir)
sys.path.insert(0, os.path.join(curDir, "html"))
import events
import netservice
import websocket
import updater
import addonHandler, languageHandler

addonHandler.initTranslation()

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("Web Services")
    enabled = False
    _interfaceGestures = {
        "kb:shift+leftArrow": "previousService",
        "kb:shift+rightArrow": "nextService",
        "kb:leftArrow": "previousMenu",
        "kb:rightArrow": "nextMenu",
        "kb:c": "sayCurrentMenu",
        "kb:downArrow": "focusNext",
        "kb:upArrow": "focusPrevious",
        "kb:enter": "activate",
        "kb:f5": "refresh",
        "kb:escape": "toggleInterface",
        "kb:nvda+shift+control+space": "toggleInterface",
    }

    _services = []
    _menus = {}
    _currentService = None

    def __init__(self):
        """Initializes the global plugin object."""
        super(globalPluginHandler.GlobalPlugin, self).__init__()
        self._net = netservice.Server(self, 62100)
        self._net.start()
        self.discoverServices()
        for service in self._services:
            service.start()
        self.updater = updater.ExtensionUpdater()
        self.updater.start()
        self.inTimer = False
        self.hasBeenUpdated = False
        wx.CallLater(1000, self.onUpdaterTimer)
        wx.CallLater(500, self.onServiceTimer)
        import addonHandler
        version = None
        for addon in addonHandler.getAvailableAddons():
            if addon.name == "web_services":
                version = addon.version
        if version is None:
            version = "unknown"
        logHandler.log.info(f"obs_control ({version}) initialized")

    def discoverServices(self):
        """Discovers all services and try to load them"""
        configPath = config.getUserDefaultConfigPath()
        curDir = os.getcwd()
        pathList = [os.path.join(configPath, "webServices"),
                    os.path.join(configPath, "addons", "web_services", "globalPlugins",
                                 "web_services", "services")]
        for path in pathList:
            for entry in os.scandir(path):
                m = re.match("^(.*)\.py$", entry.name)
                if m:
                    moduleDir = os.path.dirname(entry.path)
                    if moduleDir not in sys.path:
                        sys.path.insert(0, moduleDir)
                    mod = None
                    try:
                        logHandler.log.info("Importing " + m.group(1))
                        mod = importlib.import_module(m.group(1))
                        service = getattr(mod, "Service", None)
                        if service is None:
                            logHandler.log.error(f"{m.group(1)} has no \!Service\" attribute")
                            continue
                        logHandler.log.info(f"Loading service {m.group(1)} ...")
                        serviceInstance = service()
                        self._services.append(serviceInstance)
                    except Exception as ex:
                        logHandler.log.error(f"Failed to load service: {ex}")
        logHandler.log.info(f"{len(self._services)} services loaded")

                    

    def postServiceEvent(self, service, event, params=None):
        data = {"event": event}
        data.extend(params)
        service._inqueue.put(data)

    def registerService(self, service):
        """Registers a service to be used"""
        self._services.append(service)
        logHandler.log.info(f"Registering service {service}")

    def unregisterService(self, service):
        """Unregisters the service"""
        for srv in self._services:
            if srv == service:
                service.terminate()
                self._services.remove(service)
                lohHandler.log.info(f"Unregistericng {service}")
                return
        logHandler.log.error(f"Service {service} canot be unregistered")

    def terminateServices(self):
        for service in self._services:
            self.postServiceEvent(service, events.QUIT)
        for service in self._services:
            service.join()
    def terminate(self):
        """Called when this plugin is terminated"""
        self.updater.quit = True
        self.terminateServices()
        self.updater.join()

    def onUpdaterTimer(self):
        if self.inTimer is True or self.hasBeenUpdated is True:
            return
        self.inTimer = True
        try:
            evt = self.updater.queue.get_nowait()
        except queue.Empty:
            evt = None
        if evt is not None:
            filepath = evt.get("download", None)
            if filepath is not None:
                import addonHandler
                for prev in addonHandler.getAvailableAddons():
                    if prev.name == updater.ADDON_NAME:
                        prev.requestRemove()
                bundle = addonHandler.AddonBundle(filepath)
                addonHandler.installAddonBundle(bundle)
                logHandler.log.info("Installed version %s, restart NVDA to make the changes permanent" %(evt["version"]))
                self.hasBeenUpdated = True
        self.inTimer = False
        wx.CallLater(1000, self.onUpdaterTimer)

    def onServiceTimer(self):
        wx.CallLater(500, self.onServiceTimer)
        for service in self._services:
            evt = None
            try:
                evt = service._outqueue.get_nowait()
            except queue.Empty:
                pass
            if evt is not None:
                self.dispatchServiceEvent(service, evt)
    def dispatchServiceEvent(self, service, data):
        code = data["event"]
        if code == events.LOG:
            logHandler.log.info(f"{service.name}: {data['message']}")
        elif code == events.DISCONNECTED:
            if self.enabled:
                ui.message(_(f"{service.name} disconnected"))
                self.script_toggleInterface(None)
        elif code == events.USER_NOTIFICATION:
            ui.message(f"{service.name}: {data['message']}")
        elif code == events.READY:
            ui.message(_(f"{service.name} ready"))
        elif code == events.MENU_UPDATE:
            self._menus[service.name] = data["menus"]            
        else:
            logHandler.log.error(f"Failed to parse event: {service.name}, {data}")

    def script_toggleInterface(self, gesture):
        self.enabled = not self.enabled
        if self.enabled:
            if len(self._services) == 0:
                ui.message(_("No service registered"))
                self.enabled = False
            if self._currentService is None:
                self._currentService = self._services[0]
                self._serviceIdx = 0
            ui.message(_(f"Controlling {self._currentService.name}"))
            self.script_sayCurrentMenu()
            self.bindGestures(self._interfaceGestures)
        else:
            ui.message(_("Off"))
            self.clearGestureBindings()
            self.bindGestures(self.__gestures)
        

    script_toggleInterface.__doc__ = _("Toggles the WebService control interface on or off")

    def script_previousService(self, gesture):
        if len(self._services) == 0:
            ui.message(_("No service registered"))
            return
        self._serviceIdx -= 1
        if self._serviceIdx < 0:
            self._serviceIdx = len(self._services) - 1
        ui.message(_(f"Service {self._currentService.name}"))
    script_previousService.__doc__ = _("Switch to the previous webservice")

    def script_nextService(self, gesture):
        if len(self._services) == 0:
            ui.message(_("No service registered"))
            return
        self._serviceIdx += 1
        if self._serviceIdx >= len(self._services):
            self._serviceIdx = 0
        ui.message(_(f"Service {self._currentService.name}"))
    script_previousService.__doc__ = _("Switch to the previous webservice")

    
    def script_previousMenu(self, gesture):
        if self._catIdx is None or len(self._categories) == 0:
            tones.beep(220, 100)
            return
        self._catIdx -= 1
        if self._catIdx == -1:
            self._catIdx = len(self._categories) - 1
        self.script_sayCurrentCategory()

    def script_nextMenu(self, gesture):
        if self._catIdx is None or len(self._categories) == 0:
            tones.beep(220, 100)
            return
        self._catIdx = (self._catIdx + 1) % len(self._categories)
        self.script_sayCurrentCategory()

    def script_sayCurrentMenu(self, gesture=None):
        try:
            catName = self._categories[self._catIdx][1]
            ui.message(_(f"{catName} menu"))
        except:
            ui.message(_("No menu selected"))
    def script_focusPrevious(self, gesture):
        pass
    def script_focusNext(sepb, gesture):
        pass
    def script_activate(self, gesture):
        pass
    def scriptÆrefresh(self, gesture):
        pass
    
    __gestures = {
        "kb:nvda+shift+control+space": "toggleInterface",
    }
