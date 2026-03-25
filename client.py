# ~/progjar_app/client.py
import argparse, base64, json, os, socket, struct, sys, threading
from typing import Any, Dict, Optional

CHUNK_RECV = 4096
DOWNLOAD_DIR = "downloads"

def safe_filename(name: str) -> str:
    return os.path.basename(name.strip())

def send_packet(sock: socket.socket, packet: Dict[str, Any]) -> None:
    data = json.dumps(packet).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)

def recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

def recv_packet(sock: socket.socket) -> Optional[Dict[str, Any]]:
    header = recv_exact(sock, 4)
    if header is None:
        return None
    length = struct.unpack(">I", header)[0]
    payload = recv_exact(sock, length)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))

class ClientApp:
    def __init__(self, host: str, port: int, name: str) -> None:
        self.host = host
        self.port = port
        self.name = name
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.stop_event = threading.Event()

    def connect(self) -> None:
        self.sock.connect((self.host, self.port))
        send_packet(self.sock, {"type": "join", "name": self.name})
        print(f"[connected] {self.host}:{self.port} as {self.name}")
        print("Ketik pesan biasa untuk broadcast.")
        print("Commands: /list | /upload <filename> | /download <filename> | /quit")

    def receiver_loop(self) -> None:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        while not self.stop_event.is_set():
            try:
                packet = recv_packet(self.sock)
                if packet is None:
                    print("\n[disconnected] server closed the connection")
                    self.stop_event.set()
                    break
                self.handle_packet(packet)
            except (ConnectionError, OSError, json.JSONDecodeError, struct.error) as exc:
                if not self.stop_event.is_set():
                    print(f"\n[error] receiver: {exc}")
                self.stop_event.set()
                break

    def handle_packet(self, packet: Dict[str, Any]) -> None:
        ptype = packet.get("type")

        if ptype == "broadcast":
            sender = packet.get("sender", "unknown")
            text = packet.get("text", "")
            print(f"\n[{sender}] {text}")

        elif ptype == "system":
            print(f"\n[server] {packet.get('text', '')}")

        elif ptype == "error":
            print(f"\n[error] {packet.get('text', '')}")

        elif ptype == "list_response":
            files = packet.get("files", [])
            print("\n[files on server]")
            if files:
                for name in files:
                    print(f"- {name}")
            else:
                print("(empty)")

        elif ptype == "upload_response":
            print(f"\n[upload] {packet.get('text', '')}")

        elif ptype == "download_response":
            if not packet.get("ok", False):
                print(f"\n[download] {packet.get('text', 'failed')}")
                return

            filename = safe_filename(packet.get("filename", "downloaded_file"))
            raw = base64.b64decode(packet.get("data", ""))
            save_path = os.path.join(DOWNLOAD_DIR, filename)
            with open(save_path, "wb") as fh:
                fh.write(raw)
            print(f"\n[download] saved to {save_path}")

        else:
            print(f"\n[unknown packet] {packet}")

    def input_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                line = input("> ").strip()
            except EOFError:
                line = "/quit"
            except KeyboardInterrupt:
                line = "/quit"

            if not line:
                continue

            if line == "/quit":
                self.stop_event.set()
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
                break

            if line == "/list":
                send_packet(self.sock, {"type": "list_request"})
                continue

            if line.startswith("/upload "):
                path = line.split(maxsplit=1)[1].strip()
                if not os.path.isfile(path):
                    print(f"[upload] file not found: {path}")
                    continue
                filename = safe_filename(path)
                with open(path, "rb") as fh:
                    raw = fh.read()
                send_packet(
                    self.sock,
                    {
                        "type": "upload_request",
                        "filename": filename,
                        "data": base64.b64encode(raw).decode("ascii"),
                    },
                )
                print(f"[upload] sending {filename} ({len(raw)} bytes)")
                continue

            if line.startswith("/download "):
                filename = safe_filename(line.split(maxsplit=1)[1])
                if not filename:
                    print("[download] filename kosong")
                    continue
                send_packet(
                    self.sock,
                    {"type": "download_request", "filename": filename},
                )
                continue

            send_packet(self.sock, {"type": "chat", "text": line})

    def run(self) -> None:
        self.connect()
        receiver = threading.Thread(target=self.receiver_loop, daemon=True)
        receiver.start()
        self.input_loop()
        self.stop_event.set()
        receiver.join(timeout=1.0)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal chat + file transfer client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--name", default=None)
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    name = args.name or input("Nama client: ").strip() or "anonymous"
    app = ClientApp(args.host, args.port, name)
    try:
        app.run()
    except ConnectionRefusedError:
        print(f"Tidak bisa connect ke {args.host}:{args.port}")
        sys.exit(1)