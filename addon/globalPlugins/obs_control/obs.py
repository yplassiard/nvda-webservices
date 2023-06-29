import json
import os, sys
import queue
import threading
import time

import addonHandler
# curDir = os.path.abspath(os.path.dirname(__file__))
# sys.path.insert(0, curDir)
# sys.path.insert(0, os.path.join(curDir, "html"))
import websocket
import events
import service

addonHandler.initTranslation()


class Service(service.Service):
    """OBS Service controller"""
    _socket = None
    _categories = []
    _status = [_("Stream status: unknown"), _("Recording status: unknown")]
    _scenes = []
    _reqId = 0
    _supported_ops = {0: "obsHello",
                      2: "obsIdentified",
                      5: "obsEvent",
                      7: "obsResponse"}
    name = "OBS"

    def __init__(self):
        super().__init__()
        _socket = None
    def terminate(self):
        self.should_quit = True

    def disconnect(self):
        if self._socket:
            self._socket.close()
        self._socket = None
        self._categories = []
        self._reqId = 0
        self._scenes = []
        self.postDisconnected()

    def try_connect(self):
        try:
            self._socket = websocket.create_connection("ws://localhost:4455/")
            self._socket.settimeout(0.5)
        except Exception as ex:
            return None
        self.postLog("Connected to OBS")
        return self._socket
    def on_menu_get_items(self, event, args):
        id = args["id"]
        self.postMenuItemsList(self._menus[id])
    
    def execute(self):
        if self._socket is None:
            self.try_connect()
            if self._socket is None:
                self.disconnect()
                time.sleep(3)
                return
        data = None
        try:
            data = self._socket.recv()
        except websocket._exceptions.WebSocketTimeoutException:
            return
        except Exception as ex:
            self.disconnect()
            return
        try:
            jsdata = json.loads(data)
            op = jsdata["op"]
            args = jsdata["d"]
            op_method = self._supported_ops.get(op, None)
            if op_method is None:
                self.postLog(f"Op {op} not supported yet")
                return
            attr = getattr(self, f"on_{op_method}", None)
            if attr is not None:
                attr(op, args)
            else:
                self.postLog(f"{op}: Unhandled. data: {data}")
        except Exception as ex:
            self.postLog(f"Exception while handling {data}: {ex}")

    def on_obsHello(self, op, args):
        self._socket.send(json.dumps({"op": 1,
                                      "d": {"rpcVersion": 1}}))

    def on_obsIdentified(self, op, args):
        self.postLog("authenticated")
        self.postReady()
        self.getScenesList()
        self.getStatus()
        
    def on_obsResponse(self, code, args):
        rtype = args["requestType"]
        status = args["requestStatus"]
        if status["code"] != 100:
            self.postLog(f"{rtype} request failed: {status}")
            return
        rdata = args["responseData"]
        if rtype == "GetSceneList":
            self._curScene = rdata["currentProgramSceneName"]
            self._scenes = rdata["scenes"]
            catName = _("Scenes")
            if catName not in self._categories:
                self._categories.append(("scenes", catName))
                self.postMenuUpdate()
        elif rtype == "GetStreamStatus":
            menu = _("Status")
            if menu not in self._categories:
                self._categories.append(("status", menu))
                self.postMenuUpdate()
            outActive = rdata["outputActive"]
            outReconnect = rdata["outputReconnecting"]
            outSkippedFrames = rdata["outputSkippedFrames"]
            outTotalFrames = rdata["outputTotalFrames"]
            msg = _("Stream Status: ")
            strList = []
            if outActive:
                strList.append(_("streaming"))
            else:
                strList.append(_("disconnected"))
            if outReconnect:
                strList.appene(_("Reconnecting"))
            if outActive and outSkippedFrames:
                strList(_("{outSkippedFrames} skipped frames"))
            msg += ", ".join(strList) + "."
            if msg != self._status[0]:
                self._status[0] = msg
                self.postUserNotification(msg)
            
        elif rtype == "GetRecordStatus":
            menu = _("Status")
            if menu not in self._categories:
                self._categories.append(("status", menu))
                self.postMenuUpdate()
            outActive = rdata["outputActive"]
            outTimecode = rdata["outputTimecode"]
            msg = _("Record Status: ")
            strList = []
            if outActive:
                strList.append(_("recording"))
            else:
                strList.append(_("off"))
            if outActive and outTimecode != "00:00:00.000":
                strList(_(f"time: {outTimecode}"))
            msg += ", ".join(strList) + "."
            if msg != self._status[1]:
                self._status[1] = msg
                self.postUserNotification(msg)
            
        else:
            self.postLog(f"{rtype} Unhandled request response: {args}")
        
    def obsRequest(self, request_type, data={}):
        if self._socket is None:
            return
        self._reqId = str(time.time())
        payload = {"op": 6,
                   "d": {"requestType": request_type,
                         "requestId": self._reqId}}
        if len(data) > 0:
            payload["d"]["requestData"] = data
        self._socket.send(json.dumps(payload))

    def getScenesList(self):
        if self._socket is not None:
            self.obsRequest("GetSceneList")
    

    def getStatus(self):
        self.obsRequest("GetStreamStatus")
        self.obsRequest("GetRecordStatus")
        
