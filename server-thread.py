# ~/progjar_app/server-thread.py
import argparse, base64, json, os, socketserver, struct, threading
from typing import Any, Dict, Optional

STORAGE_DIR = "server_files"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024

def safe_filename(name: str) -> str:
    return os.path.basename(name.strip())

def send_packet(sock, packet: Dict[str, Any]) -> None:
    data = json.dumps(packet).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)

def recv_exact(sock, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

def recv_packet(sock) -> Optional[Dict[str, Any]]:
    header = recv_exact(sock, 4)
    if header is None:
        return None
    length = struct.unpack(">I", header)[0]
    payload = recv_exact(sock, length)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class):
        super().__init__(server_address, handler_class)
        self.storage_dir = STORAGE_DIR
        os.makedirs(self.storage_dir, exist_ok=True)
        self.clients: Dict[object, str] = {}
        self.lock = threading.Lock()

    def add_client(self, sock, name: str) -> None:
        with self.lock:
            self.clients[sock] = name

    def remove_client(self, sock) -> None:
        with self.lock:
            self.clients.pop(sock, None)

    def rename_client(self, sock, name: str) -> None:
        with self.lock:
            if sock in self.clients:
                self.clients[sock] = name

    def broadcast(self, sender: str, text: str) -> None:
        with self.lock:
            items = list(self.clients.items())
        dead = []
        for sock, _ in items:
            try:
                send_packet(sock, {"type": "broadcast", "sender": sender, "text": text})
            except OSError:
                dead.append(sock)
        for sock in dead:
            self.remove_client(sock)

class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: ThreadedTCPServer = self.server
        sock = self.request
        name = f"{self.client_address[0]}:{self.client_address[1]}"
        server.add_client(sock, name)
        print(f"[connected] {name}")

        try:
            while True:
                packet = recv_packet(sock)
                if packet is None:
                    break
                ptype = packet.get("type")

                if ptype == "join":
                    name = packet.get("name", name)
                    server.rename_client(sock, name)
                    send_packet(sock, {"type": "system", "text": f"Welcome, {name}"})
                    server.broadcast("server", f"{name} joined")

                elif ptype == "chat":
                    server.broadcast(name, packet.get("text", ""))

                elif ptype == "list_request":
                    send_packet(sock, {"type": "list_response", "files": sorted(os.listdir(server.storage_dir))})

                elif ptype == "upload_request":
                    filename = safe_filename(packet.get("filename", ""))
                    if not filename:
                        send_packet(sock, {"type": "upload_response", "text": "filename tidak valid"})
                        continue
                    raw = base64.b64decode(packet.get("data", ""))
                    if len(raw) > MAX_UPLOAD_SIZE:
                        send_packet(sock, {"type": "upload_response", "text": "file terlalu besar"})
                        continue
                    with open(os.path.join(server.storage_dir, filename), "wb") as fh:
                        fh.write(raw)
                    send_packet(sock, {"type": "upload_response", "text": f"upload sukses: {filename}"})
                    server.broadcast("server", f"{name} uploaded {filename}")

                elif ptype == "download_request":
                    filename = safe_filename(packet.get("filename", ""))
                    path = os.path.join(server.storage_dir, filename)
                    if not os.path.isfile(path):
                        send_packet(sock, {"type": "download_response", "ok": False, "text": "file tidak ditemukan"})
                        continue
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    send_packet(
                        sock,
                        {
                            "type": "download_response",
                            "ok": True,
                            "filename": filename,
                            "data": base64.b64encode(raw).decode("ascii"),
                        },
                    )

                else:
                    send_packet(sock, {"type": "error", "text": f"unknown packet type: {ptype}"})
        finally:
            server.remove_client(sock)
            print(f"[disconnected] {name}")
            try:
                server.broadcast("server", f"{name} left")
            except OSError:
                pass

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Thread-per-client server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    with ThreadedTCPServer((args.host, args.port), Handler) as server:
        print(f"[thread] listening on {args.host}:{args.port}")
        server.serve_forever()