# ~/progjar_app/server-sync.py
import argparse, base64, json, os, socketserver, struct
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

class SyncTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class):
        super().__init__(server_address, handler_class)
        self.storage_dir = STORAGE_DIR
        os.makedirs(self.storage_dir, exist_ok=True)
        self.clients = []

    def broadcast(self, sender: str, text: str) -> None:
        dead = []
        for sock in list(self.clients):
            try:
                send_packet(sock, {"type": "broadcast", "sender": sender, "text": text})
            except OSError:
                dead.append(sock)
        for sock in dead:
            if sock in self.clients:
                self.clients.remove(sock)

class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: SyncTCPServer = self.server
        sock = self.request
        server.clients.append(sock)
        name = f"{self.client_address[0]}:{self.client_address[1]}"
        print(f"[connected] {name}")

        try:
            while True:
                packet = recv_packet(sock)
                if packet is None:
                    break
                ptype = packet.get("type")

                if ptype == "join":
                    name = packet.get("name", name)
                    send_packet(sock, {"type": "system", "text": f"Welcome, {name}"})
                    server.broadcast("server", f"{name} joined")

                elif ptype == "chat":
                    text = packet.get("text", "")
                    server.broadcast(name, text)

                elif ptype == "list_request":
                    files = sorted(os.listdir(server.storage_dir))
                    send_packet(sock, {"type": "list_response", "files": files})

                elif ptype == "upload_request":
                    filename = safe_filename(packet.get("filename", ""))
                    data_b64 = packet.get("data", "")
                    if not filename:
                        send_packet(sock, {"type": "upload_response", "text": "filename tidak valid"})
                        continue
                    raw = base64.b64decode(data_b64)
                    if len(raw) > MAX_UPLOAD_SIZE:
                        send_packet(sock, {"type": "upload_response", "text": "file terlalu besar"})
                        continue
                    path = os.path.join(server.storage_dir, filename)
                    with open(path, "wb") as fh:
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
            if sock in server.clients:
                server.clients.remove(sock)
            print(f"[disconnected] {name}")
            try:
                server.broadcast("server", f"{name} left")
            except OSError:
                pass

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronous server (one client at a time)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    with SyncTCPServer((args.host, args.port), Handler) as server:
        print(f"[sync] listening on {args.host}:{args.port}")
        print("Catatan: model ini sinkron, jadi efektif melayani satu client aktif pada satu waktu.")
        server.serve_forever()