QUIT = 0
DISCONNECTED = 1
LOG = 2
USER_NOTIFICATION = 3
READY = 4
MENU_UPDATE = 5
MENU_GET_ITEMS = 6
SERVICE_NEW = 7
SERVICE_DEL = 8
MENU_ACTIVATE = 9


EVT_NAMES = {
    QUIT: "quit",
    DISCONNECTED: "disconnected",
    LOG: "log",
    USER_NOTIFICATION: "user_notification",
    READY: "ready",
    MENU_UPDATE: "menu_update",
    MENU_GET_ITEMS: "menu_get_items",
    SERVICE_NEW: "service_new",
    SERVICE_DEL: "service_del",
    MENU_ACTIVATE: "menu_activate"
    }

def toString(code):
    """Returns an event's name based on its code."""
    return EVT_NAMES.get(code, "unknown_event")
