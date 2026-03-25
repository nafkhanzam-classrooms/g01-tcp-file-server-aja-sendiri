# ~/progjar_app/server-select.py
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

class SelectServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.storage_dir = STORAGE_DIR
        os.makedirs(self.storage_dir, exist_ok=True)

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)

        self.inputs = [self.server]
        self.clients: Dict[socket.socket, Dict[str, Any]] = {}

    def queue_packet(self, sock: socket.socket, packet: Dict[str, Any]) -> None:
        state = self.clients.get(sock)
        if state is None:
            return
        state["out"] += pack_packet(packet)

    def broadcast(self, sender: str, text: str) -> None:
        for sock in list(self.clients):
            self.queue_packet(sock, {"type": "broadcast", "sender": sender, "text": text})

    def disconnect(self, sock: socket.socket) -> None:
        state = self.clients.pop(sock, None)
        if sock in self.inputs:
            self.inputs.remove(sock)
        try:
            peer = state["name"] if state else str(sock.getpeername())
        except OSError:
            peer = "unknown"
        try:
            sock.close()
        except OSError:
            pass
        print(f"[disconnected] {peer}")
        self.broadcast("server", f"{peer} left")

    def accept_client(self) -> None:
        sock, addr = self.server.accept()
        self.inputs.append(sock)
        self.clients[sock] = {
            "addr": addr,
            "name": f"{addr[0]}:{addr[1]}",
            "in": bytearray(),
            "out": bytearray(),
        }
        print(f"[connected] {addr}")

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

    def flush_outputs(self) -> None:
        for sock, state in list(self.clients.items()):
            out = state["out"]
            if not out:
                continue
            try:
                sent = sock.send(out)
                del out[:sent]
            except OSError:
                self.disconnect(sock)

    def run(self) -> None:
        print(f"[select] listening on {self.host}:{self.port}")
        try:
            while True:
                readable, _, _ = select.select(self.inputs, [], [], 0.2)
                for sock in readable:
                    if sock is self.server:
                        self.accept_client()
                    else:
                        self.read_from_client(sock)
                self.flush_outputs()
        except KeyboardInterrupt:
            print("\n[select] stopped")
        finally:
            for sock in list(self.clients):
                self.disconnect(sock)
            self.server.close()

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select-based multi-client server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    SelectServer(args.host, args.port).run()