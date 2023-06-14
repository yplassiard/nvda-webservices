# *-* coding: utf-8 *-*
# translate/__init__.py
#A part of the NVDA Translate add-on
#Copyright (C) 2018 Yannick PLASSIARD
#This file is covered by the GNU General Public License.
#See the file LICENSE for more details.
import os, sys, time, codecs, re
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
import obs
import websocket
import updater
import addonHandler, languageHandler

addonHandler.initTranslation()

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("Web Services")
    enabled = False
    _interfaceGestures = {
        "kb:leftArrow": "previousMenu",
        "kb:rightArrow": "nextMenu",
        "kb:c": "sayCurrentMenu",
        "kb:downArrow": "focusNext",
        "kb:upArrow": "focusPrevious",
        "kb:enter": "activate",
        "kb:f5": "refresh",
        "kb:escape": "toggleInterface",
        "kb:nvda+shift+control+s": "toggleInterface",
    }

    _services = []
        
    def __init__(self):
        """Initializes the global plugin object."""
        super(globalPluginHandler.GlobalPlugin, self).__init__()
        self._services.append(obs.Service())
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
            if addon.name == "obs_control":
                version = addon.version
        if version is None:
            version = 'unknown'
        logHandler.log.info(f"obs_control ({version}) initialized")

        

    def postServiceEvent(self, service, event, params=None):
        data = {"event": event}
        data.extend(params)
        service._inqueue.put(data)
    
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
            ui.message(_("Controlling {self._currentService.name}"))
            self.script_sayCurrentMenu()
            self.bindGestures(self._interfaceGestures)
        else:
            ui.message(_("Off"))
            self.clearGestureBindings()
            self.bindGestures(self.__gestures)
        

    script_toggleInterface.__doc__ = _("Toggles the WebService control interface on or off")

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
        "kb:nvda+shift+control+s": "toggleInterface",
    }
