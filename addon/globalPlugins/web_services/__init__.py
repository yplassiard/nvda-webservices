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
    _menuItems = {}
    _currentService = None
    _menuIdx = 0
    _itemIdx = 0
    _serviceIdx = None

    def __init__(self):
        """Initializes the global plugin object."""
        super(globalPluginHandler.GlobalPlugin, self).__init__()
        self._net = netservice.Server(self, 62100)
        self._net.start()
        self.discoverServices()
        for service in self._services:
            service.start()
        if len(self._services):
            self.focusService(self._services[0])
            
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
        logHandler.log.info(f"web_services ({version}) initialized")

    def discoverServices(self):
        """Discovers all services and try to load them"""
        configPath = config.getUserDefaultConfigPath()
        curDir = os.getcwd()
        pathList = [os.path.join(configPath, "webServices"),
                    os.path.join(configPath, "addons", "web_services", "globalPlugins",
                                 "web_services", "services")]
        for path in pathList:
            dir_generator = None
            try:
                dir_generator = os.scandir(path)
            except Exception:
                continue
            for entry in dir_generator:
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


    def bindCustomizedGestures(self):
        """When a service is focused, bind per-service customized gestures, if any"""
        gestures = self._currentService.getCustomizedGestures()
        for gesture in gestures:
            self.bindGesture(gesture, "execScriptGesture")

    def unbindCustomizedGestures(self):
        self.clearGestureBindings()
        self.bindGestures(self.__gestures)
        self.bindGestures(self._interfaceGestures)

    def focusService(self, service):
        """Gives the given service the virtual focus"""
        self.unbindCustomizedGestures()
        if service:
            self._currentService = service
        if service.isAvailable():
            self.postServiceEvent(service, events.MENU_UPDATE)
        self._serviceIdx = self._services.index(service)
        

    def postServiceEvent(self, service, event, params=None):
        data = {"event": event}
        data.update(params)
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
            logHandler.log.info(f"{service}: {data['message']}")
        elif code == events.DISCONNECTED:
            if service.isAvailable():
                ui.message(_(f"{service} disconnected"))
        elif code == events.USER_NOTIFICATION:
            ui.message(f"{service.name}: {data['message']}")
        elif code == events.READY:
            ui.message(_(f"{service.name} ready"))
        elif code == events.MENU_UPDATE:
            self._menus[service.name] = data["menus"]
        elif code == events.MENU_GET_ITEMS:
            self._menuItems[service.name] = data["items"]
            if self._itemIdx >= len(self._menuItems[service.name]):
                self._itemIdx = 0

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
            ui.message(_(f"Controlling {self._currentService}"))
            self.script_sayCurrentMenu()
            self.bindGestures(self._interfaceGestures)
            self.bindCustomizedGestures()
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
        self._currentService = self._services[self._serviceIdx]
        ui.message(_(f"Service {self._currentService}"))
    script_previousService.__doc__ = _("Switch to the previous webservice")

    def script_nextService(self, gesture):
        if len(self._services) == 0:
            ui.message(_("No service registered"))
            return
        self._serviceIdx += 1
        if self._serviceIdx >= len(self._services):
            self._serviceIdx = 0
        self._currentService = self._services[self._serviceIdx]
        ui.message(_(f"Service {self._currentService}"))
    script_previousService.__doc__ = _("Switch to the previous webservice")

    
    def script_previousMenu(self, gesture):
        if not self._currentService.isAvailable():
            ui.message(_(f"{self._currentService}"))
            return
        menus = self._menus.get(self._currentService.name, None)
        if menus is None:
            ui.message(_("No menu for this service"))
            return
        self._menuIdx -= 1
        if self._menuIdx == -1:
            self._menuIdx = len(self._menus) - 1
        self.script_sayCurrentMenu()

    script_previousMenu.__doc__ = _("Select previous menu for the active service")


    def script_nextMenu(self, gesture):
        if not self._currentService.isAvailable():
            ui.message(_(f"{self._currentService}"))
            return
        menus = self._menus.get(self._currentService.name, None)
        if menus is None:
            ui.message(_("No menu for this service"))
            return
        self._menuIdx += 1
        self._menuIdx %= len(menus)
        self.script_sayCurrentMenu()
    script_nextMenu.__doc__ = _("Select next menu for the active service")

    def script_sayCurrentMenu(self, gesture=None):
        try:
            menuId = self._menus[self._currentService.name][self._menuIdx][0]
            self.postServiceEvent(self._currentService, events.MENU_GET_ITEMS, {"id": menuId})
            menuName = self._menus[self._currentService.name][self._menuIdx][1]
            ui.message(_(f"{menuName} menu"))
        except Exception as ex:
            logHandler.log.error(f"Unable to present menu {self._menuIdx}: {ex}")
            ui.message(_("No menu selected"))
    script_sayCurrentMenu.__doc__ = _("Speaks the current menu name")

    def script_focusPrevious(self, gesture):
        self._itemIdx -= 1
        if self._itemIdx < 0:
            self._itemIdx = len(self._items) - 1
        self.script_sayItem(None)
    script_focusPrevious.__doc__ = _("Focus the previous menu item")
    
    def script_focusNext(self, gesture):
        self._itemIdx = (self._itemIdx + 1) % len(self._menuItems)
        self.script_sayItem(None)
    script_focusNext.__doc__ = _("Focus the next menu item")

    def script_sayItem(self, gesture):
        ui.message(self._menuItems[self._itemIdx])
    script_sayItem.__doc__ = _("Speaks the selected menu item")
        
    def script_activate(self, gesture):
        ui.message(_("Unimplemented"))
    script_activate.__doc__ = _("Activates this menu item")

    def script_refresh(self, gesture):
        self.discoverServices()
    script_refresh.__doc__ = _("Refreshes the interface")

    __gestures = {
        "kb:nvda+shift+control+space": "toggleInterface",
    }
