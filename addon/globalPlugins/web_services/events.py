QUIT = 0
DISCONNECTED = 1
LOG = 2
USER_NOTIFICATION = 3
READY = 4
MENU_UPDATE = 5
MENU_GET_ITEMS = 6
SERVICE_NEW = 7
SERVICE_DEL = 8


EVT_NAMES = {
    QUIT: "quit",
    DISCONNECTED: "disconnected",
    LOG: "log",
    USER_NOTIFICATION: "userNotification",
    READY: "ready",
    MENU_UPDATE: "menuUpdate",
    MENU_GET_ITEMS: "menuGetItems",
    SERVICE_NEW: "serviceNew",
    SERVICE_DEL: "serviceDel"
    }

def toString(code):
    """Returns an event's name based on its code."""
    return EVT_NAMES.get(code, "unknownEvent")
