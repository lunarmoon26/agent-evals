"""Restricted, credential-blind CONNECT proxy. Run directly; no package imports.

The socket must live in a protected trial-private directory/volume. Its 0666
mode permits a read-only volume client to connect, not to replace the listener.
This is an egress boundary, not a claim about requests inside end-to-end TLS.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import socket
import signal
import sys
from pathlib import Path

HEADER_LIMIT = 8192
HELLO_LIMIT = 65536
BUFFER_LIMIT = 16384
CONNECTION_LIMIT = 16
WALL_SECONDS = 60
CONNECT_SECONDS = 10


class Rejected(ValueError):
    """Untrusted protocol or destination rejected (never include raw input)."""


def validate_host(host: str) -> str:
    if not isinstance(host, str) or not host.isascii() or len(host) > 253:
        raise Rejected("invalid host")
    labels = host.split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        raise Rejected("invalid host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # inet_aton also recognizes legacy IPv4 forms (octal/hex/short).
        try:
            socket.inet_aton(host)
        except OSError:
            return host
    raise Rejected("IP literal")


def parse_connect(headers: bytes, hosts: tuple[str, ...]) -> str:
    if len(headers) > HEADER_LIMIT or not headers.endswith(b"\r\n\r\n"):
        raise Rejected("invalid headers")
    try:
        lines = headers[:-4].decode("ascii").split("\r\n")
    except UnicodeDecodeError:
        raise Rejected("invalid headers") from None
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] not in (
        "HTTP/1.0", "HTTP/1.1"
    ):
        raise Rejected("invalid method")
    authority = parts[1]
    if not authority.endswith(":443"):
        raise Rejected("invalid port")
    host = validate_host(authority[:-4])
    if host not in hosts:
        raise Rejected("unapproved host")
    seen_host = False
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise Rejected("invalid header")
        if any(ord(c) < 32 and c != "\t" or ord(c) == 127 for c in value):
            raise Rejected("invalid header")
        if name.lower() == "host":
            if seen_host or value.strip(" \t") != authority:
                raise Rejected("conflicting host")
            seen_host = True
        # CONNECT has no request body. Reject framing ambiguities outright.
        if name.lower() in ("content-length", "transfer-encoding"):
            raise Rejected("request body")
    return host


async def read_connect(reader: asyncio.StreamReader, hosts: tuple[str, ...]) -> str:
    try:
        headers = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
        raise Rejected("invalid headers") from None
    return parse_connect(headers, hosts)


class _Fields:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise Rejected("malformed hello")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def vector(self, width: int) -> bytes:
        return self.take(int.from_bytes(self.take(width), "big"))

    def done(self) -> None:
        if self.pos != len(self.data):
            raise Rejected("malformed hello")


def parse_client_hello(data: bytes, host: str) -> None:
    """Validate a complete handshake (including its four-byte header)."""
    validate_host(host)
    if len(data) > HELLO_LIMIT or len(data) < 4 or data[0] != 1:
        raise Rejected("not client hello")
    if int.from_bytes(data[1:4], "big") != len(data) - 4:
        raise Rejected("malformed hello")
    fields = _Fields(data[4:])
    if fields.take(2) not in (b"\x03\x01", b"\x03\x02", b"\x03\x03"):
        raise Rejected("invalid TLS version")
    fields.take(32)
    if len(fields.vector(1)) > 32:
        raise Rejected("invalid session")
    ciphers = fields.vector(2)
    if not ciphers or len(ciphers) % 2:
        raise Rejected("invalid ciphers")
    compression = fields.vector(1)
    if not compression or 0 not in compression:
        raise Rejected("invalid compression")
    extensions = _Fields(fields.vector(2))
    fields.done()
    seen: set[int] = set()
    sni = None
    while extensions.pos < len(extensions.data):
        kind = int.from_bytes(extensions.take(2), "big")
        value = extensions.vector(2)
        if kind in seen:
            raise Rejected("duplicate extension")
        seen.add(kind)
        if kind == 0xFE0D:
            raise Rejected("encrypted client hello")
        if kind == 0:
            names = _Fields(value)
            entries = _Fields(names.vector(2))
            names.done()
            # Exactly one name, of host_name type; no alternative identities.
            if entries.take(1) != b"\x00":
                raise Rejected("invalid SNI")
            try:
                sni = entries.vector(2).decode("ascii")
            except UnicodeDecodeError:
                raise Rejected("invalid SNI") from None
            entries.done()
            validate_host(sni)
    if sni != host:
        raise Rejected("missing or mismatched SNI")


async def read_client_hello(reader: asyncio.StreamReader, host: str) -> bytes:
    """Consume bounded fragmented handshake records, retaining their exact bytes."""
    wire = bytearray()
    handshake = bytearray()
    expected = None
    try:
        while expected is None or len(handshake) < expected:
            if len(wire) + 5 > HELLO_LIMIT:
                raise Rejected("hello too large")
            header = await reader.readexactly(5)
            size = int.from_bytes(header[3:5], "big")
            if header[0] != 22 or header[1:3] not in (
                b"\x03\x01", b"\x03\x02", b"\x03\x03"
            ) or not 0 < size <= 16384 or len(wire) + 5 + size > HELLO_LIMIT:
                raise Rejected("invalid TLS record")
            body = await reader.readexactly(size)
            wire.extend(header + body)
            handshake.extend(body)
            if handshake[0] != 1:
                raise Rejected("not client hello")
            if len(handshake) >= 4:
                expected = 4 + int.from_bytes(handshake[1:4], "big")
                if expected > HELLO_LIMIT or len(handshake) > expected:
                    raise Rejected("invalid hello size")
        parse_client_hello(bytes(handshake), host)
    except asyncio.IncompleteReadError:
        raise Rejected("truncated hello") from None
    return bytes(wire)


def local_addresses() -> set[str]:
    """Linux interface addresses, including globally routed local interfaces.

    Failure to enumerate is an infrastructure failure, not permission to dial.
    """
    addresses = set()
    # SIOCGIFADDR returns only the primary IPv4 address, missing secondary
    # public addresses. The kernel's local host routes include all addresses.
    previous = ""
    for line in Path("/proc/net/fib_trie").read_text().splitlines():
        if line.strip() == "/32 host LOCAL":
            fields = previous.split()
            if len(fields) != 2 or fields[0] != "|--":
                raise OSError("unrecognized local address table")
            addresses.add(str(ipaddress.IPv4Address(fields[1])))
        previous = line
    if not addresses:
        raise OSError("unavailable local address table")
    for line in Path("/proc/net/if_inet6").read_text().splitlines():
        addresses.add(str(ipaddress.IPv6Address(int(line.split()[0], 16))))
    return addresses


def validate_addresses(answers: list[tuple], local: set[str]) -> tuple[tuple, ...]:
    if not answers:
        raise Rejected("empty DNS")
    result = []
    local_ips = {ipaddress.ip_address(address) for address in local}
    for family, kind, protocol, _, address in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            raise Rejected("invalid DNS")
        ip = ipaddress.ip_address(address[0])
        transition = (isinstance(ip, ipaddress.IPv6Address) and
                      (ip.sixtofour is not None or ip.teredo is not None
                       or ip in ipaddress.IPv6Network("64:ff9b::/96")
                       or ip in ipaddress.IPv6Network("64:ff9b:1::/48")))
        if (ip.version != (4 if family == socket.AF_INET else 6)
                or not ip.is_global or ip.is_reserved or ip.is_multicast
                or ip.is_loopback or ip.is_link_local or ip.is_unspecified
                 or isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None
                 or transition
                or ip in local_ips or address[1] != 443
                or family == socket.AF_INET6 and address[3] != 0):
            raise Rejected("unsafe DNS")
        result.append((family, kind, protocol, str(ip)))
    return tuple(dict.fromkeys(result))


async def resolve_public(host: str) -> tuple[tuple, ...]:
    validate_host(host)
    local = local_addresses()
    answers = await asyncio.get_running_loop().getaddrinfo(
        host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return validate_addresses(answers, local)


async def dial_public(host: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    async with asyncio.timeout(CONNECT_SECONDS):
        answers = await resolve_public(host)
        # One validated numeric destination; failures do not trigger re-resolution.
        family, kind, protocol, address = answers[0]
        raw = socket.socket(family, kind, protocol)
        raw.setblocking(False)
        try:
            destination = (address, 443) if family == socket.AF_INET else (address, 443, 0, 0)
            await asyncio.get_running_loop().sock_connect(raw, destination)
            peer = raw.getpeername()
            if ipaddress.ip_address(peer[0]) != ipaddress.ip_address(address) or peer[1] != 443:
                raise Rejected("peer mismatch")
            return await asyncio.open_connection(sock=raw, limit=BUFFER_LIMIT)
        except BaseException:
            raw.close()
            raise


async def close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        async with asyncio.timeout(1):
            await writer.wait_closed()


async def relay_streams(left_reader, left_writer, right_reader, right_writer) -> None:
    async def pump(reader, writer):
        while data := await reader.read(BUFFER_LIMIT):
            writer.write(data)
            await writer.drain()
        if not writer.can_write_eof():
            raise Rejected("half-close unavailable")
        writer.write_eof()
        await writer.drain()

    async with asyncio.TaskGroup() as group:
        group.create_task(pump(left_reader, right_writer))
        group.create_task(pump(right_reader, left_writer))


async def handle_connect(reader, writer, hosts: tuple[str, ...]) -> None:
    upstream = None
    established = False
    try:
        async with asyncio.timeout(WALL_SECONDS):
            host = await read_connect(reader, hosts)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            established = True
            await writer.drain()
            hello = await read_client_hello(reader, host)
            remote_reader, upstream = await dial_public(host)
            upstream.write(hello)
            await upstream.drain()
            await relay_streams(reader, writer, remote_reader, upstream)
    except Exception:
        if not established:
            with contextlib.suppress(Exception):
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
    finally:
        try:
            if upstream is not None:
                await close_writer(upstream)
        finally:
            await close_writer(writer)


async def handle_relay(reader, writer, socket_path: str) -> None:
    upstream = None
    try:
        async with asyncio.timeout(WALL_SECONDS):
            async with asyncio.timeout(CONNECT_SECONDS):
                remote_reader, upstream = await asyncio.open_unix_connection(
                    socket_path, limit=BUFFER_LIMIT
                )
            await relay_streams(reader, writer, remote_reader, upstream)
    except Exception:
        pass
    finally:
        try:
            if upstream is not None:
                await close_writer(upstream)
        finally:
            await close_writer(writer)


async def _serve(listener: socket.socket, handler) -> None:
    # Admission happens synchronously BEFORE creating a handler task. There is
    # no unlimited semaphore-waiter queue, even under a connection flood.
    slots = asyncio.Semaphore(CONNECTION_LIMIT)
    tasks: set[asyncio.Task] = set()

    def accepted(reader, writer):
        if len(tasks) >= CONNECTION_LIMIT:
            writer.close()
            return
        async def run():
            try:
                async with slots:
                    await handler(reader, writer)
            finally:
                writer.close()

        def finished(task):
            # A task cancelled before its first step never enters its finally.
            writer.close()
            tasks.discard(task)
            if not task.cancelled():
                task.exception()  # consume failure without logging private data

        task = asyncio.create_task(run())
        tasks.add(task)
        task.add_done_callback(finished)

    factory = (asyncio.start_unix_server if listener.family == socket.AF_UNIX
               else asyncio.start_server)
    try:
        server = await factory(accepted, sock=listener, limit=HEADER_LIMIT,
                               backlog=CONNECTION_LIMIT)
        async with server:
            await server.serve_forever()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def serve(socket_path: str, hosts: tuple[str, ...]) -> None:
    if not hosts or len(set(hosts)) != len(hosts):
        raise Rejected("invalid allowlist")
    for host in hosts:
        validate_host(host)
    # Bind ourselves: asyncio's pathname API may unlink existing socket files.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(socket_path)
        os.chmod(socket_path, 0o666)
        listener.listen(CONNECTION_LIMIT)
        listener.setblocking(False)
        await _serve(listener, lambda reader, writer: handle_connect(reader, writer, hosts))


async def serve_relay(socket_path: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 8080))
        listener.listen(CONNECTION_LIMIT)
        listener.setblocking(False)
        await _serve(listener, lambda reader, writer: handle_relay(reader, writer, socket_path))


async def serve_bounded(socket_path: str, hosts: tuple[str, ...], seconds: int) -> None:
    async with asyncio.timeout(seconds):
        await serve(socket_path, hosts)


def main() -> int:
    args = sys.argv[1:]
    try:
        if len(args) == 2 and args[0] == "--relay":
            asyncio.run(serve_relay(args[1]))
        elif len(args) >= 4 and args[0] == "--lifetime" and args[1].isdigit() and 1 <= int(args[1]) <= 900:
            # Independent process deadline, even if DNS/executor/writer teardown stalls.
            signal.alarm(int(args[1]))
            try:
                asyncio.run(serve_bounded(args[2], tuple(args[3:]), max(1, int(args[1]) - 5)))
            finally:
                signal.alarm(0)
        elif len(args) >= 2 and not args[0].startswith("--"):
            asyncio.run(serve(args[0], tuple(args[1:])))
        else:
            return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        # No exception text, paths, addresses, headers or payload logging.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
