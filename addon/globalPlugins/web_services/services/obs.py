import json
import os, sys
import queue
import threading
import time

import addonHandler
import websocket
import events
import service

addonHandler.initTranslation()


SERVICE_DISPLAY_NAME = "OBS Studio"


class Service(service.Service):
    """OBS Service controller - Full WebSocket implementation for OBS Studio control."""

    # OBS WebSocket opcodes
    OP_HELLO = 0
    OP_IDENTIFY = 1
    OP_IDENTIFIED = 2
    OP_REIDENTIFY = 3
    OP_EVENT = 5
    OP_REQUEST = 6
    OP_REQUEST_RESPONSE = 7
    OP_REQUEST_BATCH = 8
    OP_REQUEST_BATCH_RESPONSE = 9

    name = "OBS"

    def __init__(self):
        super().__init__(self.name, SERVICE_DISPLAY_NAME)
        self._socket = None
        self._reqId = 0
        self._pendingRequests = {}

        # State tracking
        self._scenes = []
        self._curScene = None
        self._sources = {}  # scene_name -> list of sources
        self._curSceneSources = []

        # Streaming/Recording state
        self._isStreaming = False
        self._isRecording = False
        self._isRecordingPaused = False
        self._streamReconnecting = False
        self._streamTimecode = "00:00:00"
        self._recordTimecode = "00:00:00"
        self._skippedFrames = 0
        self._totalFrames = 0
        self._lastSkippedFrames = 0

        # Virtual camera state
        self._isVirtualCamActive = False

        # Replay buffer state
        self._isReplayBufferActive = False

        # Menu IDs
        self._sceneMenuId = None
        self._sourceMenuId = None
        self._controlMenuId = None
        self._statusMenuId = None

        # Status monitoring
        self._lastStatusCheck = 0
        self._statusCheckInterval = 5  # Check status every 5 seconds

        # Issue tracking for auto-announce
        self._lastIssueAnnounce = 0
        self._issueAnnounceInterval = 10  # Don't spam issues more than every 10 seconds

        self._supported_ops = {
            self.OP_HELLO: "obsHello",
            self.OP_IDENTIFIED: "obsIdentified",
            self.OP_EVENT: "obsEvent",
            self.OP_REQUEST_RESPONSE: "obsResponse"
        }

    def terminate(self):
        self._should_quit = True

    def disconnect(self):
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
        self._socket = None
        self._reqId = 0
        self._scenes = []
        self._curScene = None
        self._sources = {}
        self._curSceneSources = []
        self._isStreaming = False
        self._isRecording = False
        self._isRecordingPaused = False
        self._streamReconnecting = False
        self._sceneMenuId = None
        self._sourceMenuId = None
        self._controlMenuId = None
        self._statusMenuId = None
        self._menus = {}
        self._menuList = []
        self._menuId = 0
        self.disable()
        self.postDisconnected()

    def try_connect(self):
        try:
            self._socket = websocket.create_connection("ws://localhost:4455/")
            self._socket.settimeout(0.5)
        except Exception as ex:
            return None
        self.postLog("Connected to OBS")
        self.enable()
        return self._socket

    def on_menu_get_items(self, event, args):
        """Handle menu item requests from the global plugin."""
        menuId = args["id"]
        if menuId not in self._menus:
            return
        menu = self._menus[menuId]
        items = menu.get("items", [])
        self.postMenuItemsList(menu)

    def on_menu_activate(self, event, args):
        """Handle menu item activation from the global plugin."""
        menuId = args.get("menuId")
        itemIdx = args.get("itemIdx")
        if menuId not in self._menus:
            return
        menu = self._menus[menuId]
        items = menu.get("items", [])
        if itemIdx < 0 or itemIdx >= len(items):
            return
        item = items[itemIdx]
        action = item.get("action")
        actionData = item.get("actionData", {})

        if action == "switchScene":
            self.switchScene(actionData.get("sceneName"))
        elif action == "toggleSource":
            self.toggleSourceVisibility(
                actionData.get("sceneName"),
                actionData.get("sceneItemId"),
                actionData.get("sourceName")
            )
        elif action == "toggleStream":
            self.toggleStream()
        elif action == "toggleRecord":
            self.toggleRecord()
        elif action == "toggleRecordPause":
            self.toggleRecordPause()
        elif action == "toggleVirtualCam":
            self.toggleVirtualCam()
        elif action == "toggleReplayBuffer":
            self.toggleReplayBuffer()
        elif action == "saveReplayBuffer":
            self.saveReplayBuffer()
        elif action == "refreshStatus":
            self.getFullStatus()

    def execute(self):
        """Main service loop - handle WebSocket communication."""
        if self._socket is None:
            self.try_connect()
            if self._socket is None:
                self.disconnect()
                time.sleep(3)
                return

        # Periodic status check for issue detection
        now = time.time()
        if now - self._lastStatusCheck > self._statusCheckInterval:
            self._lastStatusCheck = now
            if self._isStreaming or self._isRecording:
                self.getStreamStatus()
                self.getRecordStatus()

        data = None
        try:
            data = self._socket.recv()
        except websocket._exceptions.WebSocketTimeoutException:
            return
        except Exception as ex:
            self.postLog(f"WebSocket error: {ex}")
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

    # ========== OBS WebSocket Protocol Handlers ==========

    def on_obsHello(self, op, args):
        """Handle OBS Hello message - send identification."""
        # OBS WebSocket 5.x protocol
        self._socket.send(json.dumps({
            "op": self.OP_IDENTIFY,
            "d": {
                "rpcVersion": 1,
                "eventSubscriptions": 0xFFFFFFFF  # Subscribe to all events
            }
        }))

    def on_obsIdentified(self, op, args):
        """Handle successful identification - initialize state."""
        self.postLog("Authenticated with OBS")
        self.postReady()
        self._buildMenus()
        self.getFullStatus()

    def on_obsEvent(self, op, args):
        """Handle OBS events for real-time updates."""
        eventType = args.get("eventType", "")
        eventData = args.get("eventData", {})

        # Scene events
        if eventType == "CurrentProgramSceneChanged":
            self._onSceneChanged(eventData)
        elif eventType == "SceneListChanged":
            self._onSceneListChanged(eventData)
        elif eventType == "SceneItemEnableStateChanged":
            self._onSourceVisibilityChanged(eventData)
        elif eventType == "SceneItemCreated":
            self._onSceneItemCreated(eventData)
        elif eventType == "SceneItemRemoved":
            self._onSceneItemRemoved(eventData)

        # Streaming events
        elif eventType == "StreamStateChanged":
            self._onStreamStateChanged(eventData)

        # Recording events
        elif eventType == "RecordStateChanged":
            self._onRecordStateChanged(eventData)

        # Virtual camera events
        elif eventType == "VirtualcamStateChanged":
            self._onVirtualCamStateChanged(eventData)

        # Replay buffer events
        elif eventType == "ReplayBufferStateChanged":
            self._onReplayBufferStateChanged(eventData)
        elif eventType == "ReplayBufferSaved":
            self.postUserNotification(_("Replay buffer saved"))

        # Output events for issue detection
        elif eventType == "StreamOutputReconnecting":
            self._streamReconnecting = True
            self.postUserNotification(_("Stream reconnecting..."))
        elif eventType == "StreamOutputReconnected":
            self._streamReconnecting = False
            self.postUserNotification(_("Stream reconnected"))

    def on_obsResponse(self, code, args):
        """Handle OBS request responses."""
        rtype = args.get("requestType", "")
        reqId = args.get("requestId", "")
        status = args.get("requestStatus", {})
        rdata = args.get("responseData", {})

        if status.get("code") != 100:
            # Request failed
            errorMsg = status.get("comment", "Unknown error")
            self.postLog(f"{rtype} request failed: {errorMsg}")
            return

        # Handle different response types
        handler = getattr(self, f"_onResponse_{rtype}", None)
        if handler:
            handler(rdata)
        else:
            self.postLog(f"Unhandled response type: {rtype}")

    # ========== Response Handlers ==========

    def _onResponse_GetSceneList(self, data):
        """Handle scene list response."""
        self._curScene = data.get("currentProgramSceneName", "")
        self._scenes = data.get("scenes", [])
        self._updateSceneMenu()
        # Get sources for current scene
        if self._curScene:
            self.getSceneItems(self._curScene)

    def _onResponse_GetSceneItemList(self, data):
        """Handle scene items (sources) response."""
        # Store sources for the current scene
        items = data.get("sceneItems", [])
        self._curSceneSources = items
        self._updateSourceMenu()

    def _onResponse_GetStreamStatus(self, data):
        """Handle stream status response."""
        wasStreaming = self._isStreaming
        self._isStreaming = data.get("outputActive", False)
        self._streamReconnecting = data.get("outputReconnecting", False)
        newSkippedFrames = data.get("outputSkippedFrames", 0)
        self._totalFrames = data.get("outputTotalFrames", 0)
        self._streamTimecode = data.get("outputTimecode", "00:00:00")

        # Check for frame drop issues
        if self._isStreaming and newSkippedFrames > self._skippedFrames:
            droppedNow = newSkippedFrames - self._skippedFrames
            self._announceIssue(_("Dropped {count} frames").format(count=droppedNow))
        self._skippedFrames = newSkippedFrames

        # Check for reconnection
        if self._streamReconnecting:
            self._announceIssue(_("Stream is reconnecting"))

        self._updateStatusMenu()

    def _onResponse_GetRecordStatus(self, data):
        """Handle record status response."""
        self._isRecording = data.get("outputActive", False)
        self._isRecordingPaused = data.get("outputPaused", False)
        self._recordTimecode = data.get("outputTimecode", "00:00:00")
        self._updateStatusMenu()

    def _onResponse_GetVirtualCamStatus(self, data):
        """Handle virtual camera status response."""
        self._isVirtualCamActive = data.get("outputActive", False)
        self._updateControlMenu()

    def _onResponse_GetReplayBufferStatus(self, data):
        """Handle replay buffer status response."""
        self._isReplayBufferActive = data.get("outputActive", False)
        self._updateControlMenu()

    def _onResponse_SetSceneItemEnabled(self, data):
        """Handle source visibility toggle response."""
        # Refresh sources after toggle
        if self._curScene:
            self.getSceneItems(self._curScene)

    # ========== Event Handlers ==========

    def _onSceneChanged(self, data):
        """Handle scene change event."""
        newScene = data.get("sceneName", "")
        if newScene != self._curScene:
            self._curScene = newScene
            self.postUserNotification(_("Scene: {name}").format(name=newScene))
            self._updateSceneMenu()
            # Get sources for new scene
            self.getSceneItems(newScene)

    def _onSceneListChanged(self, data):
        """Handle scene list change event."""
        self._scenes = data.get("scenes", [])
        self._updateSceneMenu()

    def _onSourceVisibilityChanged(self, data):
        """Handle source visibility change event."""
        sceneName = data.get("sceneName", "")
        sceneItemId = data.get("sceneItemId", 0)
        enabled = data.get("sceneItemEnabled", False)

        # Find source name
        sourceName = None
        for source in self._curSceneSources:
            if source.get("sceneItemId") == sceneItemId:
                sourceName = source.get("sourceName", "Unknown")
                source["sceneItemEnabled"] = enabled
                break

        if sourceName and sceneName == self._curScene:
            status = _("visible") if enabled else _("hidden")
            self.postUserNotification(_("{source}: {status}").format(
                source=sourceName, status=status))
            self._updateSourceMenu()

    def _onSceneItemCreated(self, data):
        """Handle new scene item creation."""
        sceneName = data.get("sceneName", "")
        if sceneName == self._curScene:
            self.getSceneItems(self._curScene)

    def _onSceneItemRemoved(self, data):
        """Handle scene item removal."""
        sceneName = data.get("sceneName", "")
        if sceneName == self._curScene:
            self.getSceneItems(self._curScene)

    def _onStreamStateChanged(self, data):
        """Handle streaming state change event."""
        outputState = data.get("outputState", "")
        self._isStreaming = data.get("outputActive", False)

        stateMessages = {
            "OBS_WEBSOCKET_OUTPUT_STARTING": _("Stream starting..."),
            "OBS_WEBSOCKET_OUTPUT_STARTED": _("Stream started"),
            "OBS_WEBSOCKET_OUTPUT_STOPPING": _("Stream stopping..."),
            "OBS_WEBSOCKET_OUTPUT_STOPPED": _("Stream stopped"),
            "OBS_WEBSOCKET_OUTPUT_RECONNECTING": _("Stream reconnecting..."),
            "OBS_WEBSOCKET_OUTPUT_RECONNECTED": _("Stream reconnected"),
        }

        msg = stateMessages.get(outputState)
        if msg:
            self.postUserNotification(msg)

        self._updateStatusMenu()
        self._updateControlMenu()

    def _onRecordStateChanged(self, data):
        """Handle recording state change event."""
        outputState = data.get("outputState", "")
        self._isRecording = data.get("outputActive", False)

        stateMessages = {
            "OBS_WEBSOCKET_OUTPUT_STARTING": _("Recording starting..."),
            "OBS_WEBSOCKET_OUTPUT_STARTED": _("Recording started"),
            "OBS_WEBSOCKET_OUTPUT_STOPPING": _("Recording stopping..."),
            "OBS_WEBSOCKET_OUTPUT_STOPPED": _("Recording stopped"),
            "OBS_WEBSOCKET_OUTPUT_PAUSED": _("Recording paused"),
            "OBS_WEBSOCKET_OUTPUT_RESUMED": _("Recording resumed"),
        }

        msg = stateMessages.get(outputState)
        if msg:
            self.postUserNotification(msg)

        if outputState == "OBS_WEBSOCKET_OUTPUT_PAUSED":
            self._isRecordingPaused = True
        elif outputState == "OBS_WEBSOCKET_OUTPUT_RESUMED":
            self._isRecordingPaused = False

        self._updateStatusMenu()
        self._updateControlMenu()

    def _onVirtualCamStateChanged(self, data):
        """Handle virtual camera state change event."""
        outputState = data.get("outputState", "")
        self._isVirtualCamActive = data.get("outputActive", False)

        if outputState == "OBS_WEBSOCKET_OUTPUT_STARTED":
            self.postUserNotification(_("Virtual camera started"))
        elif outputState == "OBS_WEBSOCKET_OUTPUT_STOPPED":
            self.postUserNotification(_("Virtual camera stopped"))

        self._updateControlMenu()

    def _onReplayBufferStateChanged(self, data):
        """Handle replay buffer state change event."""
        outputState = data.get("outputState", "")
        self._isReplayBufferActive = data.get("outputActive", False)

        if outputState == "OBS_WEBSOCKET_OUTPUT_STARTED":
            self.postUserNotification(_("Replay buffer started"))
        elif outputState == "OBS_WEBSOCKET_OUTPUT_STOPPED":
            self.postUserNotification(_("Replay buffer stopped"))

        self._updateControlMenu()

    # ========== Issue Detection ==========

    def _announceIssue(self, message):
        """Announce an issue, respecting the throttle interval."""
        now = time.time()
        if now - self._lastIssueAnnounce >= self._issueAnnounceInterval:
            self._lastIssueAnnounce = now
            self.postUserNotification(_("Warning: {msg}").format(msg=message))

    # ========== Menu Building ==========

    def _buildMenus(self):
        """Build all menus after connection."""
        self._sceneMenuId = self.addMenu(_("Scenes"))
        self._sourceMenuId = self.addMenu(_("Sources"))
        self._controlMenuId = self.addMenu(_("Controls"))
        self._statusMenuId = self.addMenu(_("Status"))

    def _updateSceneMenu(self):
        """Update the scenes menu with current scene list."""
        if self._sceneMenuId is None:
            return

        items = []
        for scene in reversed(self._scenes):  # OBS returns scenes in reverse order
            sceneName = scene.get("sceneName", "")
            label = sceneName
            if sceneName == self._curScene:
                label = f"* {sceneName}"
            items.append({
                "name": label,
                "action": "switchScene",
                "actionData": {"sceneName": sceneName}
            })

        self._menus[self._sceneMenuId]["items"] = items
        self.postMenuUpdate()

    def _updateSourceMenu(self):
        """Update the sources menu for current scene."""
        if self._sourceMenuId is None:
            return

        items = []
        for source in self._curSceneSources:
            sourceName = source.get("sourceName", "")
            sceneItemId = source.get("sceneItemId", 0)
            enabled = source.get("sceneItemEnabled", True)

            # Show visibility status
            status = _("visible") if enabled else _("hidden")
            label = f"{sourceName} [{status}]"

            items.append({
                "name": label,
                "action": "toggleSource",
                "actionData": {
                    "sceneName": self._curScene,
                    "sceneItemId": sceneItemId,
                    "sourceName": sourceName
                }
            })

        if not items:
            items.append({"name": _("No sources in this scene"), "action": None})

        self._menus[self._sourceMenuId]["items"] = items
        self.postMenuUpdate()

    def _updateControlMenu(self):
        """Update the controls menu."""
        if self._controlMenuId is None:
            return

        items = []

        # Streaming control
        streamLabel = _("Stop streaming") if self._isStreaming else _("Start streaming")
        items.append({
            "name": streamLabel,
            "action": "toggleStream",
            "actionData": {}
        })

        # Recording control
        recordLabel = _("Stop recording") if self._isRecording else _("Start recording")
        items.append({
            "name": recordLabel,
            "action": "toggleRecord",
            "actionData": {}
        })

        # Pause recording (only if recording)
        if self._isRecording:
            pauseLabel = _("Resume recording") if self._isRecordingPaused else _("Pause recording")
            items.append({
                "name": pauseLabel,
                "action": "toggleRecordPause",
                "actionData": {}
            })

        # Virtual camera control
        vcamLabel = _("Stop virtual camera") if self._isVirtualCamActive else _("Start virtual camera")
        items.append({
            "name": vcamLabel,
            "action": "toggleVirtualCam",
            "actionData": {}
        })

        # Replay buffer control
        replayLabel = _("Stop replay buffer") if self._isReplayBufferActive else _("Start replay buffer")
        items.append({
            "name": replayLabel,
            "action": "toggleReplayBuffer",
            "actionData": {}
        })

        # Save replay buffer (only if active)
        if self._isReplayBufferActive:
            items.append({
                "name": _("Save replay buffer"),
                "action": "saveReplayBuffer",
                "actionData": {}
            })

        self._menus[self._controlMenuId]["items"] = items
        self.postMenuUpdate()

    def _updateStatusMenu(self):
        """Update the status menu."""
        if self._statusMenuId is None:
            return

        items = []

        # Stream status
        if self._isStreaming:
            streamStatus = _("Streaming: {time}").format(time=self._streamTimecode)
            if self._streamReconnecting:
                streamStatus += " " + _("(reconnecting)")
            if self._skippedFrames > 0:
                dropPercent = (self._skippedFrames / max(1, self._totalFrames)) * 100
                streamStatus += " " + _("- {dropped} dropped ({percent:.1f}%)").format(
                    dropped=self._skippedFrames, percent=dropPercent)
        else:
            streamStatus = _("Stream: Off")
        items.append({"name": streamStatus, "action": "refreshStatus", "actionData": {}})

        # Record status
        if self._isRecording:
            recordStatus = _("Recording: {time}").format(time=self._recordTimecode)
            if self._isRecordingPaused:
                recordStatus += " " + _("(paused)")
        else:
            recordStatus = _("Recording: Off")
        items.append({"name": recordStatus, "action": "refreshStatus", "actionData": {}})

        # Current scene
        sceneStatus = _("Scene: {name}").format(name=self._curScene or _("None"))
        items.append({"name": sceneStatus, "action": "refreshStatus", "actionData": {}})

        # Refresh option
        items.append({
            "name": _("Refresh status"),
            "action": "refreshStatus",
            "actionData": {}
        })

        self._menus[self._statusMenuId]["items"] = items
        self.postMenuUpdate()

    # ========== OBS API Requests ==========

    def obsRequest(self, requestType, data=None):
        """Send a request to OBS."""
        if self._socket is None:
            return None

        self._reqId += 1
        reqId = f"req_{self._reqId}"

        payload = {
            "op": self.OP_REQUEST,
            "d": {
                "requestType": requestType,
                "requestId": reqId
            }
        }

        if data:
            payload["d"]["requestData"] = data

        try:
            self._socket.send(json.dumps(payload))
        except Exception as ex:
            self.postLog(f"Failed to send request: {ex}")
            return None

        return reqId

    def getFullStatus(self):
        """Get complete OBS status."""
        self.getScenesList()
        self.getStreamStatus()
        self.getRecordStatus()
        self.getVirtualCamStatus()
        self.getReplayBufferStatus()

    def getScenesList(self):
        """Request scene list from OBS."""
        self.obsRequest("GetSceneList")

    def getSceneItems(self, sceneName):
        """Request scene items (sources) for a scene."""
        self.obsRequest("GetSceneItemList", {"sceneName": sceneName})

    def getStreamStatus(self):
        """Request stream status."""
        self.obsRequest("GetStreamStatus")

    def getRecordStatus(self):
        """Request record status."""
        self.obsRequest("GetRecordStatus")

    def getVirtualCamStatus(self):
        """Request virtual camera status."""
        self.obsRequest("GetVirtualCamStatus")

    def getReplayBufferStatus(self):
        """Request replay buffer status."""
        self.obsRequest("GetReplayBufferStatus")

    # ========== OBS Control Actions ==========

    def switchScene(self, sceneName):
        """Switch to a different scene."""
        if sceneName:
            self.obsRequest("SetCurrentProgramScene", {"sceneName": sceneName})

    def toggleSourceVisibility(self, sceneName, sceneItemId, sourceName):
        """Toggle source visibility in a scene."""
        # Find current state
        currentEnabled = True
        for source in self._curSceneSources:
            if source.get("sceneItemId") == sceneItemId:
                currentEnabled = source.get("sceneItemEnabled", True)
                break

        self.obsRequest("SetSceneItemEnabled", {
            "sceneName": sceneName,
            "sceneItemId": sceneItemId,
            "sceneItemEnabled": not currentEnabled
        })

    def toggleStream(self):
        """Toggle streaming on/off."""
        self.obsRequest("ToggleStream")

    def toggleRecord(self):
        """Toggle recording on/off."""
        self.obsRequest("ToggleRecord")

    def toggleRecordPause(self):
        """Toggle recording pause."""
        self.obsRequest("ToggleRecordPause")

    def toggleVirtualCam(self):
        """Toggle virtual camera on/off."""
        self.obsRequest("ToggleVirtualCam")

    def toggleReplayBuffer(self):
        """Toggle replay buffer on/off."""
        self.obsRequest("ToggleReplayBuffer")

    def saveReplayBuffer(self):
        """Save the current replay buffer."""
        self.obsRequest("SaveReplayBuffer")
        
