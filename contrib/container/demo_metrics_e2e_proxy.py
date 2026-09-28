"""Minimal TCP forwarder for the demo-metrics E2E harness (harness-only).

Runs in a short-lived helper container published on the host loopback port so
the Playwright browser can reach the real Django backend on
``http://127.0.0.1:8127`` even when host->bridge traffic is filtered. Forwards
raw TCP only — it cannot fabricate API responses, so every response the
browser sees is still produced by the real backend.

Usage: python3 demo_metrics_e2e_proxy.py <target-host> <target-port> [listen-port]
"""

import socket
import sys
import threading


def pump(source: socket.socket, target: socket.socket) -> None:
    """Forward bytes from source to target until EOF, then close both."""
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            target.sendall(chunk)
    except OSError:
        pass
    finally:
        for sock in (source, target):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def main() -> None:
    """Accept TCP connections on the listen port and forward them to target."""
    target_host = sys.argv[1]
    target_port = int(sys.argv[2])
    listen_port = int(sys.argv[3]) if len(sys.argv) > 3 else target_port

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', listen_port))
    server.listen(64)
    print(f'proxy 0.0.0.0:{listen_port} -> {target_host}:{target_port}', flush=True)

    while True:
        client, address = server.accept()
        try:
            upstream = socket.create_connection((target_host, target_port), timeout=30)
        except OSError:
            client.close()
            continue
        print(f'connection from {address[0]}:{address[1]}', flush=True)
        threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pump, args=(upstream, client), daemon=True).start()


if __name__ == '__main__':
    main()
