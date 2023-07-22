import socket
import select
import threading
import queue

from logHandler import log
import service

NET_OPS = {
    "0": "ping",
    "1": "identify",
    "2": "logout",
    "3": "meouAdd",
    "4": "menuDel",
    "5": "menuUpdate",
    "6": "menuItemAdd",
    "7": "menuItemDel",
    "8": "menuItemUpdate",
    "9": "userNotification",
}


class Server(threading.Thread):
    """TCP Server listening for incoming service requests.
    """
    _clients = []
    _sock = None
    _outQueue = queue.Queue()
    _inQueue = queue.Queue()
    
    _port = None
    _gp = None

    def __init__(self, gp, port, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._port = port
        self._gp = gp
        self._shouldQuit = False
        self.create_server()
    def create_server(self):
        """Creates a li,stening socket on specified port"""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
            self._sock.bind(("0.0.0.0", self._port))
            self._sock.listen(5)
        except Exception as ex:
            log.info(f"Unable to bind to {self._port}: {ex}")

    def run(self):
        log.info(f"TCP server running on port {self._port}")
        while self._shouldQuit is False:
            evt = None
            try:
                self._inQueue.get_nowait()
            except queue.Empty:
                pass
            if evt is not None:
                method = f"on_{events.toString(evt['event'])}"
                attr = getattr(self, method, None)
                if attr:
                    try:
                        attr(evt)
                    except Exception as ex:
                        log.info(f"Error executing {method}: {ex}")
                else:
                    log.info(f"{method}: unknown to {self.__class__.__name__}")
            # TCP I/O polling
            input = []
            output = []
            input.append(self._sock)
            for client in self._clients:
                input.append(client._sock)
                if client.out_buf:
                    output.append(client._sock)
            rlist, wlist, xlist = select.select(input, output, [], 0.1)
            for fd in rlist:
                self.on_read(fd)
            for fd in wlist:
                self.on_write(fd)
            
        log.info("TCP Server exiting")
        self._sock.close()
        for client in self._clients:
            client.terminate()

    def on_accept(self, fd):
        """Accepts an incoming connection"""
        sock,addr = _sock.accept()
        self._clients.append(Client(self, sock, addr))
        
    def on_read(self, fd):
        """Data to be read from a file descriptor."""
        if self._sock.fileno() == fd:
            self.on_accept()
            return
        for client in self._clients:
            if client._sock.fileno() == fd:
                ret = client.on_read()
                if not ret:
                    self._clients.remove(client)
                return
    def on_write(self, fd):
        """File descriptor ready for write"""
        for client in self._clients:
            if client.fildno() == fd:
                ret = client.on_write()
                if not ret:
                    self._clients.remove(client)
                return

class Client:
    """Holds a TCP Client session"""
    _sock = None
    _service = None
    _gp = None
    out_buf = bytes()
    in_buf = bytes()

    def __init__(self, server, sock, addr):
        self._sock = sock
        self._server = server
        self._gp = self._server._gp
        self._clientName = f"{addr[0]}, {self._sock.fileno()}"
        

    def on_read(self):
        """Read data from socket"""
        try:
            data = self._sock.recv()
            self.in_buf.append(data.decode("utf-8"))
            self.parse()
        except Exception as ex:
            log.info(f"Client({self._sock.fileno()}): Error reading/ parsing data: {ex}")
            return False
        return True

    def on_write(self):
        """Data to be written to the socket"""
        if self.out_buf:
            try:
                ret = self._sock.send(bytes(self.out_buf, "utf-8"))
                if ret == len(self.out_buf):
                    self.out_buf = ""
                    return True
                if ret > 0:
                    self.out_buf = self.out_buf[ret:]
                    return True
                log.info(f"Client({self._sock.fileno()}): Cannot write data")
                return False
            except Exception as ex:
                log.info(f"Client({self._sock.fileno()}): Cannot write: {ex}")
            return False
        return True

    def parse(self):
        """Extracts a JSON and parses it"""
        if self.in_buf:
            for line in self.in_buf.split("\n"):
                try:
                    data = json.loads(line)
                    self.decode(data)
                except Exception as ex:
                    log.info(f"Client({self._sock.fileno()}): Unable to decode JsÝON data {line}: {ex}")
    def decode(self, data):
        """Handles a client request"""
        try:
            jsdata = json.loads(data)
            op = jsdata.get("op", "unknown_operation")
            method = f"on_{self._ops.get(op, 'unknown_operation')}"
            attr = getattr(self, method, None)
            if attr:
                return attr(jsdata)
            else:
                log.info(f"{method}: No such method")
        except Exception as ex:
            log.info("Unable to parse payload {data}: {ex}")
        return False


    def send(self, code, payload):
        """Sends the given payload to the client"""
        data = {"op": code}
        data.extend(payload)
        self._outbuf += json.dumps(data) + "\n"

    def on_ping(self, jsdata):
        """Answers to a ping command"""
        self._outbuf += json.dumps({"op": "0",
                                    "time": time.time(),
                                    "pong_id": jsdata.get("ping_id", "not_provided")})
        
                                   
    def on_identify(self, jsdata):
        """Performs client identification"""
        try:
            service_name = jsdata.get("service-name", None)
            service_display_name = jsdata.get("display-name", service_name)
            service_version = jsdata.get("version", "")
            service_author = jsdata.get("author", "anonymous")
            if service_name is None:
                self.send("1", {"status": "error",
                                "error": "Invalid service name"})
                return
            new_service = NetService(service_name, service_display_name,
                                     service_version, service_author)
            self._server.registerService(new_service)
            self.send("6", {"status": "ok"})
            return
        except Exception as ex:
            log.error(f"Unable to parse identify command: {ex}")
            self.send("6", {"status": "error",
                            "message": "internal error"})

class NetService(service.Service):
    def __init__(self, name, display_name, author, version):
        super().__init__(name, display_name)
        self._author = author
        self._version = version

        
