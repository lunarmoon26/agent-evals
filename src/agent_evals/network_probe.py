"""Fixed no-inference in-container network controls; never used as a subject agent."""

import errno
import json
import socket
import ssl
import subprocess
import sys
import time


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def direct_denial():
    require([name for _, name in socket.if_nameindex()] == ["lo"], "non-loopback interface")
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        with socket.socket(socket.AF_INET, kind) as stream:
            stream.settimeout(1)
            try:
                if kind == socket.SOCK_STREAM:
                    stream.connect(("192.0.2.1", 443))
                else:
                    stream.sendto(b"synthetic-no-secret", ("8.8.8.8", 53))
            except OSError as error:
                require(error.errno == errno.ENETUNREACH, "denial is not network-unreachable")
            else:
                raise RuntimeError("direct TCP/DNS route exists")
    child = subprocess.run([sys.executable, "-c",
                            "import socket; s=socket.socket(); s.settimeout(1); s.connect(('192.0.2.1',443))"],
                           capture_output=True, timeout=3)
    require(child.returncode != 0 and b"Network is unreachable" in child.stderr, "subprocess bypass")
    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except PermissionError:
        pass
    else:
        raw.close()
        raise RuntimeError("raw ICMP authority exists")


def connect(authority):
    stream = socket.create_connection(("127.0.0.1", 8080), timeout=5)
    try:
        stream.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())
        header = bytearray()
        while not header.endswith(b"\r\n\r\n") and len(header) <= 8192:
            chunk = stream.recv(1)
            if not chunk:
                break
            header.extend(chunk)
        return stream, bytes(header)
    except BaseException:
        stream.close()
        raise


def probe(case):
    direct_denial()
    relay = subprocess.Popen([sys.executable, "/opt/egress_proxy.py", "--relay", "/egress/provider.sock"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", 8080), timeout=0.1):
                    break
            except OSError:
                require(relay.poll() is None, "relay exited")
                time.sleep(0.05)
        else:
            raise RuntimeError("relay not ready")
        if case == "timeout":
            time.sleep(120)
            raise RuntimeError("timeout did not stop subject")
        if case == "proxy-stop":
            try:
                stream, header = connect("chatgpt.com:443")
                stream.close()
                require(not header.startswith(b"HTTP/1.1 200"), "proxy failure widened authority")
            except (ConnectionResetError, BrokenPipeError):
                pass  # Closed/reset relay is expected when its only upstream is gone.
            print(json.dumps({"direct_and_subprocess_denied": True, "proxy_stop_fail_closed": True}))
            return
        allowed = "auth.openai.com" if case == "auth-route" else "chatgpt.com"
        excluded = "chatgpt.com" if case == "auth-route" else "auth.openai.com"
        for authority in ("example.com:443", "127.0.0.1:443", "169.254.169.254:443", "[::1]:443",
                          "localhost:443", f"{allowed}:80", f"{excluded}:443", f"{allowed}.example.com:443"):
            stream, header = connect(authority)
            stream.close()
            require(header.startswith(b"HTTP/1.1 403"), "forbidden CONNECT accepted")
        # Reject a permitted CONNECT carrying another TLS SNI before upstream dial.
        stream, header = connect(allowed + ":443")
        require(header.startswith(b"HTTP/1.1 200"), "permitted CONNECT not accepted")
        outgoing, incoming = ssl.MemoryBIO(), ssl.MemoryBIO()
        client = ssl.create_default_context().wrap_bio(incoming, outgoing, server_side=False, server_hostname="example.com")
        try:
            client.do_handshake()
        except ssl.SSLWantReadError:
            pass
        stream.sendall(outgoing.read())
        try:
            require(not stream.recv(1), "mismatched SNI forwarded")
        except ConnectionResetError:
            pass
        finally:
            stream.close()
        # TLS handshake only: no authenticated request, no HTTP payload, no inference.
        stream, header = connect(allowed + ":443")
        require(header.startswith(b"HTTP/1.1 200"), "provider CONNECT failed")
        with ssl.create_default_context().wrap_socket(stream, server_hostname=allowed) as tls:
            require(tls.version() in {"TLSv1.2", "TLSv1.3"}, "TLS provider handshake failed")
        print(json.dumps({"direct_and_subprocess_denied": True, "forbidden_routes_denied": True,
                          "mismatched_sni_denied": True, "provider_tls_handshake": allowed,
                          "inference_requests": 0}))
    finally:
        relay.terminate()
        try:
            relay.wait(timeout=3)
        except subprocess.TimeoutExpired:
            relay.kill()
            relay.wait()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"auth-route", "agent-route", "proxy-stop", "timeout"}:
        raise SystemExit("unknown fixed network control")
    probe(sys.argv[1])
