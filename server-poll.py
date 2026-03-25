# ~/progjar_app/server-poll.py
import argparse, base64, json, os, select, socket, struct
from typing import Any, Dict

STORAGE_DIR = "server_files"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
RECV_SIZE = 4096

def safe_filename(name: str) -> str:
    return os.path.basename(name.strip())

def pack_packet(packet: Dict[str, Any]) -> bytes:
    data = json.dumps(packet).encode("utf-8")
    return struct.pack(">I", len(data)) + data

class PollServer:
    def __init__(self, host: str, port: int) -> None:
        if not hasattr(select, "poll"):
            raise RuntimeError("select.poll() tidak tersedia di OS ini")

        self.host = host
        self.port = port
        self.storage_dir = STORAGE_DIR
        os.makedirs(self.storage_dir, exist_ok=True)

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)

        self.poller = select.poll()
        self.fd_to_sock: Dict[int, socket.socket] = {self.server.fileno(): self.server}
        self.clients: Dict[socket.socket, Dict[str, Any]] = {}
        self.poller.register(self.server.fileno(), select.POLLIN)

    def queue_packet(self, sock: socket.socket, packet: Dict[str, Any]) -> None:
        state = self.clients.get(sock)
        if state is None:
            return
        state["out"] += pack_packet(packet)
        self.poller.modify(sock.fileno(), select.POLLIN | select.POLLOUT)

    def broadcast(self, sender: str, text: str) -> None:
        for sock in list(self.clients):
            self.queue_packet(sock, {"type": "broadcast", "sender": sender, "text": text})

    def accept_client(self) -> None:
        sock, addr = self.server.accept()
        self.fd_to_sock[sock.fileno()] = sock
        self.clients[sock] = {
            "addr": addr,
            "name": f"{addr[0]}:{addr[1]}",
            "in": bytearray(),
            "out": bytearray(),
        }
        self.poller.register(sock.fileno(), select.POLLIN)
        print(f"[connected] {addr}")

    def disconnect(self, sock: socket.socket) -> None:
        state = self.clients.pop(sock, None)
        fd = sock.fileno()
        try:
            self.poller.unregister(fd)
        except Exception:
            pass
        self.fd_to_sock.pop(fd, None)
        peer = state["name"] if state else "unknown"
        try:
            sock.close()
        except OSError:
            pass
        print(f"[disconnected] {peer}")
        self.broadcast("server", f"{peer} left")

    def handle_packet(self, sock: socket.socket, packet: Dict[str, Any]) -> None:
        state = self.clients[sock]
        ptype = packet.get("type")

        if ptype == "join":
            state["name"] = packet.get("name", state["name"])
            self.queue_packet(sock, {"type": "system", "text": f"Welcome, {state['name']}"})
            self.broadcast("server", f"{state['name']} joined")

        elif ptype == "chat":
            self.broadcast(state["name"], packet.get("text", ""))

        elif ptype == "list_request":
            self.queue_packet(sock, {"type": "list_response", "files": sorted(os.listdir(self.storage_dir))})

        elif ptype == "upload_request":
            filename = safe_filename(packet.get("filename", ""))
            if not filename:
                self.queue_packet(sock, {"type": "upload_response", "text": "filename tidak valid"})
                return
            raw = base64.b64decode(packet.get("data", ""))
            if len(raw) > MAX_UPLOAD_SIZE:
                self.queue_packet(sock, {"type": "upload_response", "text": "file terlalu besar"})
                return
            with open(os.path.join(self.storage_dir, filename), "wb") as fh:
                fh.write(raw)
            self.queue_packet(sock, {"type": "upload_response", "text": f"upload sukses: {filename}"})
            self.broadcast("server", f"{state['name']} uploaded {filename}")

        elif ptype == "download_request":
            filename = safe_filename(packet.get("filename", ""))
            path = os.path.join(self.storage_dir, filename)
            if not os.path.isfile(path):
                self.queue_packet(sock, {"type": "download_response", "ok": False, "text": "file tidak ditemukan"})
                return
            with open(path, "rb") as fh:
                raw = fh.read()
            self.queue_packet(
                sock,
                {
                    "type": "download_response",
                    "ok": True,
                    "filename": filename,
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            )

        else:
            self.queue_packet(sock, {"type": "error", "text": f"unknown packet type: {ptype}"})

    def read_from_client(self, sock: socket.socket) -> None:
        try:
            data = sock.recv(RECV_SIZE)
        except ConnectionError:
            data = b""
        if not data:
            self.disconnect(sock)
            return
        state = self.clients[sock]
        state["in"].extend(data)
        buf = state["in"]

        while True:
            if len(buf) < 4:
                break
            length = struct.unpack(">I", bytes(buf[:4]))[0]
            if len(buf) < 4 + length:
                break
            payload = bytes(buf[4:4 + length])
            del buf[:4 + length]
            packet = json.loads(payload.decode("utf-8"))
            self.handle_packet(sock, packet)

    def write_to_client(self, sock: socket.socket) -> None:
        state = self.clients.get(sock)
        if state is None:
            return
        out = state["out"]
        if not out:
            self.poller.modify(sock.fileno(), select.POLLIN)
            return
        try:
            sent = sock.send(out)
            del out[:sent]
        except OSError:
            self.disconnect(sock)
            return
        if not out:
            self.poller.modify(sock.fileno(), select.POLLIN)

    def run(self) -> None:
        print(f"[poll] listening on {self.host}:{self.port}")
        try:
            while True:
                for fd, event in self.poller.poll(200):
                    sock = self.fd_to_sock.get(fd)
                    if sock is None:
                        continue
                    if sock is self.server:
                        self.accept_client()
                        continue
                    if event & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                        self.disconnect(sock)
                        continue
                    if event & select.POLLIN:
                        self.read_from_client(sock)
                    if sock in self.clients and event & select.POLLOUT:
                        self.write_to_client(sock)
        except KeyboardInterrupt:
            print("\n[poll] stopped")
        finally:
            for sock in list(self.clients):
                self.disconnect(sock)
            try:
                self.poller.unregister(self.server.fileno())
            except Exception:
                pass
            self.server.close()

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll-based multi-client server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    PollServer(args.host, args.port).run()