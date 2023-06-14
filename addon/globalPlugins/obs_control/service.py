#service.py
#
# Base class for all services used by the addon.
#

import os
import sys
import time
import threading
import queue

import addonHandler

import events

class Service(threading.Thread):
    _inqueue = queue.Queue()
    _outqueue = queue.Queue()
    _menus = {}
    _menuId = 0

    def __init__(self, params=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config = params
    
    def handleInputEvent(self):
        """Gets en avent from the input queue and handles it."""
        try:
            data = self._inqueue.get_nowait()
            code = data["event"]
            if code == events.QUIT:
                self.should_quit = True
                self.postLog("Exiting")
            else:
                attr = getattr(self, f"on_{events.NAMES[code]}", None)
                if attr:
                    attr(code, data)
                else:
                    self.postLog(f"Unhandled event {events.NAMES[code]}: {data}")
        except queue.Empty:
            pass
        except Exception as ex:
            self.postLog(f"Failed to handle event: {ex}")

    def run(self):
        """Service's main loop."""
        service_inloop = getattr(self, "execute", None)
        self._should_quit = False
        while self._should_quit is False:
            self.handleInputEvent()
            if service_inloop:
                try:
                    service_inloop()
                except Exception as ex:
                    self.postLog(f"{self.__class__.__name__}.execute() failed: {ex}.")
                    service_inloop = None
            time.sleep(0.1) # sleep 100ms to avoid CPU load
        self.postLog(f"{self.name} thread exiting")

    # sevvice API
    ## basic helpers

    def addMenu(self, name, initialChoices=[]):
        if name is None or name == "":
            self.postLog(f"aedMenu({name}, {initialChoices}): Invalid arguments")
        self._menuId += 1
        self._menus[self._menuId] = {"name": name,
                                     "items": initialChoices}
        self._menuList.append((self._menuId, name))
        self.postMenuUpdate(_menuList)
        return self._menuId

    def removeMenu(self, menuId):
        if menuId not in self._menus:
            return
        self.postLog(f"Removing menu {self._menus[menuId]['name']}")
        del self._menus[menuId]
        i = 0
        for menu in self._menuList:
            if menu[0] == menuId:
                self._menuList.remove(i)
                return
        return
    
    # helpers to post events to the add-on main thread
    
    def postEvent(self, payload):
        """Generic event posting"""
        self._outqueue.put(payload)

    def postDisconnected(self):
        """Service has been disconnected and is no longher available to the user."""
        self.postEvent({"event": events.DISCONNECTED})

    def postReady(self):
        """Service is ready to be used"""
        self.postEvent({"event": events.READY})
    
    def postLog(self, msg):
        """Asks the add-on to log a message"""
        self.postEvent({"event": events.LOG, "message": msg})

    def postUserNotification(self, msg):
        """Sends a notification to the user using NVDA's ui.message()"""
        self.postEvent({"event": events.USER_NOTIFICATION, "message": msg})

    def postMenuUpdate(self):
        """Menu list has been updated"""
        self.postEvent({"event": events.MENU_UPDATE, "menus": self._menuList})

    def postMenuItemsList(self, items):
        """Items has been updated for a given menu"""
        self.postEvent({"event": events.CATEGORY_MENU_ITEMS_LIST, "items": items})
        
