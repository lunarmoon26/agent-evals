"""No real sockets, DNS, TLS services, containers, or external network calls."""

import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_evals import egress_proxy as proxy

HOST = "auth.openai.com"
HOSTS = (HOST,)


def vector(data, width=2):
    return len(data).to_bytes(width, "big") + data


def hello(host=HOST, extensions=None):
    if extensions is None:
        extensions = b"\x00\x00" + vector(vector(b"\x00" + vector(host.encode())))
    body = (b"\x03\x03" + bytes(32) + b"\x00" + vector(b"\x13\x01")
            + b"\x01\x00" + vector(extensions))
    return b"\x01" + len(body).to_bytes(3, "big") + body


def record(data, kind=22):
    return bytes([kind]) + b"\x03\x01" + vector(data)


def stream(data=b"", eof=True, limit=proxy.HEADER_LIMIT):
    reader = asyncio.StreamReader(limit=limit)
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.closed = False
        self.eof = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

    def can_write_eof(self):
        return True

    def write_eof(self):
        self.eof = True


def answer(ip, family=socket.AF_INET, port=443):
    address = (ip, port) if family == socket.AF_INET else (ip, port, 0, 0)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)


class ParsingTests(unittest.TestCase):
    def test_valid_connect_host_optional(self):
        for suffix in (b"", b"Host: auth.openai.com:443\r\n"):
            self.assertEqual(proxy.parse_connect(
                b"CONNECT auth.openai.com:443 HTTP/1.1\r\n" + suffix + b"\r\n", HOSTS
            ), HOST)

    def test_invalid_names(self):
        for name in ("Auth.openai.com", "*.openai.com", "127.0.0.1", "[::1]",
                     "foo", "foo..com", "foo.com.", "-foo.com", "foo-.com",
                     "a@foo.com", "https://foo.com", "é.com", "a" * 64 + ".com",
                     "127.1", "0177.0.0.1", "0x7f.0.0.1"):
            with self.subTest(name=name), self.assertRaises(proxy.Rejected):
                proxy.validate_host(name)

    def test_malformed_connect(self):
        requests = [
            b"GET auth.openai.com:443 HTTP/1.1\r\n\r\n",
            b"CONNECT https://auth.openai.com:443 HTTP/1.1\r\n\r\n",
            b"CONNECT user@auth.openai.com:443 HTTP/1.1\r\n\r\n",
            b"CONNECT auth.openai.com:80 HTTP/1.1\r\n\r\n",
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n\r\n",
            b"CONNECT  auth.openai.com:443 HTTP/1.1\r\n\r\n",
            b"CONNECT auth.openai.com:443 HTTP/2\r\n\r\n",
            b"CONNECT auth.openai.com:443 HTTP/1.1\n\n",
        ]
        base = b"CONNECT auth.openai.com:443 HTTP/1.1\r\n"
        for header in (b"Host: chatgpt.com:443", b"Host: auth.openai.com",
                       b"Host: auth.openai.com:443\r\nHost: auth.openai.com:443",
                       b"Content-Length: 0", b"Transfer-Encoding: chunked",
                       b" folded: value", b"Bad Header: value", b"X: a\x00b",
                       b"X: \xff", b"X: " + b"a" * proxy.HEADER_LIMIT):
            requests.append(base + header + b"\r\n\r\n")
        for request in requests:
            with self.subTest(request=request[:80]), self.assertRaises(proxy.Rejected):
                proxy.parse_connect(request, HOSTS)

    def test_hello_exact_sni(self):
        proxy.parse_client_hello(hello(), HOST)
        for name in ("chatgpt.com", "AUTH.openai.com", "127.0.0.1", "auth.openai.com."):
            with self.subTest(name=name), self.assertRaises(proxy.Rejected):
                proxy.parse_client_hello(hello(name), HOST)

    def test_missing_duplicate_malformed_sni(self):
        sni = b"\x00\x00" + vector(vector(b"\x00" + vector(HOST.encode())))
        bad_extensions = (b"", sni + sni, b"\x00\x00" + vector(b""),
                          b"\x00\x00" + vector(vector(b"\x01" + vector(HOST.encode()))),
                          b"\x00\x00" + vector(vector(
                              b"\x00" + vector(HOST.encode()) + b"\x00" + vector(HOST.encode()))),
                           sni + b"\x00", sni[:-1], sni + b"\xfe\x0d" + vector(b"encrypted"))
        for extensions in bad_extensions:
            with self.subTest(extensions=extensions), self.assertRaises(proxy.Rejected):
                proxy.parse_client_hello(hello(extensions=extensions), HOST)
        for data in (b"", b"\x02" + hello()[1:], hello()[:-1], hello() + b"x",
                     hello()[:4] + b"\x02\x00" + hello()[6:]):
            with self.assertRaises(proxy.Rejected):
                proxy.parse_client_hello(data, HOST)

    def test_dns_all_answers_checked(self):
        public = [answer("8.8.8.8"), answer("2606:4700:4700::1111", socket.AF_INET6)]
        self.assertEqual(len(proxy.validate_addresses(public, set())), 2)
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
                   "169.254.169.254", "100.64.0.1", "192.0.2.1", "0.0.0.0",
                   "240.0.0.1", "224.0.0.1"):
            with self.subTest(ip=ip), self.assertRaises(proxy.Rejected):
                proxy.validate_addresses(public + [answer(ip)], set())
        for ip in ("::1", "::", "fe80::1", "fc00::1", "2001:db8::1",
                    "::ffff:8.8.8.8", "::ffff:127.0.0.1", "ff02::1", "64:ff9b::a00:1",
                    "64:ff9b:1::a00:1", "2002:a00:1::1", "2001:0000:4136:e378:8000:63bf:3fff:fdd2"):
            with self.subTest(ip=ip), self.assertRaises(proxy.Rejected):
                proxy.validate_addresses(public + [answer(ip, socket.AF_INET6)], set())
        for answers, local in (([], set()), (public, {"8.8.8.8"}),
                               ([answer("8.8.8.8", port=80)], set())):
            with self.assertRaises(proxy.Rejected):
                proxy.validate_addresses(answers, local)

    def test_all_local_interface_addresses_including_secondary(self):
        ipv4 = """Main:
           |-- 127.0.0.1
              /32 host LOCAL
           |-- 8.8.8.8
              /32 host LOCAL
           |-- 8.8.4.4
              /32 host LOCAL
           |-- 8.8.4.255
              /32 link BROADCAST
"""
        ipv6 = "26064700470000000000000000001111 02 40 00 80 eth0\n"
        with patch.object(proxy.Path, "read_text", side_effect=[ipv4, ipv6]):
            local = proxy.local_addresses()
        self.assertEqual(local, {"127.0.0.1", "8.8.8.8", "8.8.4.4", "2606:4700:4700::1111"})
        with self.assertRaises(proxy.Rejected):
            proxy.validate_addresses([answer("8.8.4.4")], local)
        with patch.object(proxy.Path, "read_text", return_value="unknown format"):
            with self.assertRaises(OSError):
                proxy.local_addresses()


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_hello_and_preserved_following_data(self):
        data = hello()
        wire = b"".join(record(data[a:b]) for a, b in ((0, 1), (1, 3), (3, 30), (30, len(data))))
        reader = stream(eof=False)

        async def feed():
            for byte in wire + b"next":
                reader.feed_data(bytes([byte]))
                await asyncio.sleep(0)
            reader.feed_eof()

        task = asyncio.create_task(feed())
        self.assertEqual(await proxy.read_client_hello(reader, HOST), wire)
        await task
        self.assertEqual(await reader.read(), b"next")

    async def test_invalid_tls_records(self):
        for data in (record(hello(), kind=23), record(hello(), kind=21),
                     record(b""), record(hello()[:-1]), record(b"\x02"),
                     record(b"\x01\xff\xff\xff"), record(hello() + b"x"),
                     b"\x16\x03\x03\x40\x01", b"not TLS"):
            with self.subTest(data=data[:10]), self.assertRaises(proxy.Rejected):
                await proxy.read_client_hello(stream(data), HOST)

    async def test_tls_wire_bound(self):
        # Record overhead counts too, preventing tiny-record amplification.
        body = b"\x01" + (65532).to_bytes(3, "big") + bytes(65532)
        wire = b"".join(record(body[i:i + 1000]) for i in range(0, len(body), 1000))
        with self.assertRaises(proxy.Rejected):
            await proxy.read_client_hello(stream(wire), HOST)

    async def test_header_limit_and_truncation(self):
        for data in (b"x" * (proxy.HEADER_LIMIT + 1), b"CONNECT", b"x\r\n\r\n"):
            with self.assertRaises(proxy.Rejected):
                await proxy.read_connect(stream(data), HOSTS)

    async def test_local_client_no_dial_for_bad_sni(self):
        request = b"CONNECT auth.openai.com:443 HTTP/1.1\r\n\r\n"
        for data in (record(hello("chatgpt.com")), record(hello(extensions=b"")), b"plaintext"):
            writer = Writer()
            with patch.object(proxy, "dial_public", new_callable=AsyncMock) as dial:
                await proxy.handle_connect(stream(request + data), writer, HOSTS)
                dial.assert_not_awaited()
            self.assertTrue(writer.closed)
            self.assertEqual(bytes(writer.data), b"HTTP/1.1 200 Connection Established\r\n\r\n")

    async def test_local_client_success_opaque_payload_halfclose(self):
        request = b"CONNECT auth.openai.com:443 HTTP/1.1\r\n\r\n"
        wire = record(hello())
        client, upstream = Writer(), Writer()
        with patch.object(proxy, "dial_public", AsyncMock(return_value=(stream(b"response"), upstream))) as dial:
            await proxy.handle_connect(stream(request + wire + b"opaque"), client, HOSTS)
            dial.assert_awaited_once_with(HOST)
        self.assertEqual(bytes(upstream.data), wire + b"opaque")
        self.assertTrue(bytes(client.data).endswith(b"response"))
        self.assertTrue(client.eof and upstream.eof and client.closed and upstream.closed)

    async def test_halfclose_does_not_cancel_other_direction(self):
        incoming, outgoing = stream(), stream(eof=False)
        left, right = Writer(), Writer()
        task = asyncio.create_task(proxy.relay_streams(incoming, left, outgoing, right))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(right.eof)
        self.assertFalse(task.done())
        outgoing.feed_data(b"late response")
        outgoing.feed_eof()
        await task
        self.assertEqual(bytes(left.data), b"late response")

    async def test_cancellation_closes_both_writers(self):
        request = b"CONNECT auth.openai.com:443 HTTP/1.1\r\n\r\n" + record(hello())
        client, upstream = Writer(), Writer()
        ready = asyncio.Event()

        async def waiting(*args):
            ready.set()
            await asyncio.Future()

        with patch.object(proxy, "dial_public", AsyncMock(return_value=(stream(eof=False), upstream))), \
                patch.object(proxy, "relay_streams", waiting):
            task = asyncio.create_task(proxy.handle_connect(stream(request, eof=False), client, HOSTS))
            await ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(client.closed and upstream.closed)

    async def test_wall_timeout(self):
        client = Writer()
        with patch.object(proxy, "WALL_SECONDS", 0.01), \
                patch.object(proxy, "dial_public", new_callable=AsyncMock) as dial:
            await proxy.handle_connect(stream(eof=False), client, HOSTS)
            dial.assert_not_awaited()
        self.assertTrue(client.closed)

    async def test_resolve_single_query_and_numeric_dial_rebinding(self):
        loop = asyncio.get_running_loop()
        raw = MagicMock()
        raw.getpeername.return_value = ("8.8.8.8", 443)
        writer = Writer()
        dns = AsyncMock(side_effect=[[answer("8.8.8.8")], [answer("127.0.0.1")]])
        with patch.object(proxy, "local_addresses", return_value=set()), \
                patch.object(loop, "getaddrinfo", dns), \
                patch.object(loop, "sock_connect", new_callable=AsyncMock) as connect, \
                patch.object(proxy.socket, "socket", return_value=raw), \
                patch.object(proxy.asyncio, "open_connection", AsyncMock(return_value=(stream(), writer))) as opened:
            self.assertEqual((await proxy.dial_public(HOST))[1], writer)
            dns.assert_awaited_once()
            connect.assert_awaited_once_with(raw, ("8.8.8.8", 443))
            opened.assert_awaited_once_with(sock=raw, limit=proxy.BUFFER_LIMIT)

    async def test_numeric_ipv6_and_peer_mismatch(self):
        loop = asyncio.get_running_loop()
        ip = "2606:4700:4700::1111"
        for peer, fails in (((ip, 443, 0, 0), False), (("::1", 443, 0, 0), True)):
            raw = MagicMock()
            raw.getpeername.return_value = peer
            with patch.object(proxy, "resolve_public", AsyncMock(return_value=(
                (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, ip),))), \
                    patch.object(proxy.socket, "socket", return_value=raw), \
                    patch.object(loop, "sock_connect", new_callable=AsyncMock) as connect, \
                    patch.object(proxy.asyncio, "open_connection", AsyncMock(return_value=(stream(), Writer()))) as opened:
                if fails:
                    with self.assertRaises(proxy.Rejected):
                        await proxy.dial_public(HOST)
                    raw.close.assert_called_once()
                    opened.assert_not_awaited()
                else:
                    await proxy.dial_public(HOST)
                connect.assert_awaited_once_with(raw, (ip, 443, 0, 0))

    async def test_mixed_dns_prevents_socket_creation(self):
        loop = asyncio.get_running_loop()
        with patch.object(proxy, "local_addresses", return_value=set()), \
                patch.object(loop, "getaddrinfo", AsyncMock(return_value=[
                    answer("8.8.8.8"), answer("::1", socket.AF_INET6)])), \
                patch.object(proxy.socket, "socket") as create:
            with self.assertRaises(proxy.Rejected):
                await proxy.dial_public(HOST)
            create.assert_not_called()

    async def test_dial_cancel_closes_raw_socket(self):
        loop = asyncio.get_running_loop()
        raw = MagicMock()
        with patch.object(proxy, "resolve_public", AsyncMock(return_value=(
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "8.8.8.8"),))), \
                patch.object(proxy.socket, "socket", return_value=raw), \
                patch.object(loop, "sock_connect", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await proxy.dial_public(HOST)
            raw.close.assert_called_once()

    async def test_connect_timeout_closes_raw_socket(self):
        loop = asyncio.get_running_loop()
        raw = MagicMock()

        async def stalled(*args):
            await asyncio.Future()

        with patch.object(proxy, "CONNECT_SECONDS", 0.01), \
                patch.object(proxy, "resolve_public", AsyncMock(return_value=(
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "8.8.8.8"),))), \
                patch.object(proxy.socket, "socket", return_value=raw), \
                patch.object(loop, "sock_connect", stalled):
            with self.assertRaises(TimeoutError):
                await proxy.dial_public(HOST)
            raw.close.assert_called_once()

    async def test_relay_cancel_and_failure_cleanup(self):
        ready = asyncio.Event()

        async def stalled(*args):
            ready.set()
            await asyncio.Future()

        client, remote = Writer(), Writer()
        with patch.object(proxy.asyncio, "open_unix_connection", AsyncMock(return_value=(stream(), remote))), \
                patch.object(proxy, "relay_streams", stalled):
            task = asyncio.create_task(proxy.handle_relay(stream(), client, "protected/proxy.sock"))
            await ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(client.closed and remote.closed)
        client = Writer()
        with patch.object(proxy.asyncio, "open_unix_connection", AsyncMock(side_effect=FileNotFoundError)):
            await proxy.handle_relay(stream(), client, "protected/proxy.sock")
        self.assertTrue(client.closed)

    async def test_relay_buffers_and_failure_cancels_other_pump(self):
        class LimitedWriter(Writer):
            def write(self, data):
                self.assert_bounded(data)
                super().write(data)

            @staticmethod
            def assert_bounded(data):
                if len(data) > proxy.BUFFER_LIMIT:
                    raise AssertionError("unbounded relay")

        left, right = LimitedWriter(), LimitedWriter()
        payload = b"x" * (proxy.BUFFER_LIMIT * 4 + 1)
        await proxy.relay_streams(stream(payload), left, stream(payload), right)
        self.assertEqual(bytes(left.data), payload)
        self.assertEqual(bytes(right.data), payload)
        right.drain = AsyncMock(side_effect=ConnectionError)
        with self.assertRaises(ExceptionGroup):
            await proxy.relay_streams(stream(b"x"), left, stream(eof=False), right)

    async def test_local_enumeration_failure_prevents_dns(self):
        loop = asyncio.get_running_loop()
        with patch.object(proxy, "local_addresses", side_effect=OSError), \
                patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as dns:
            with self.assertRaises(OSError):
                await proxy.resolve_public(HOST)
            dns.assert_not_awaited()

    async def test_listener_never_unlinks_existing_path(self):
        listener = MagicMock()
        listener.__enter__.return_value = listener
        listener.bind.side_effect = FileExistsError()
        with patch.object(proxy.socket, "socket", return_value=listener), \
                patch.object(proxy.os, "chmod") as chmod, \
                patch.object(proxy.os, "unlink") as unlink:
            with self.assertRaises(FileExistsError):
                await proxy.serve("protected/proxy.sock", HOSTS)
            chmod.assert_not_called()
            unlink.assert_not_called()

    async def test_listener_mode_and_relay_destination(self):
        listener = MagicMock()
        listener.__enter__.return_value = listener
        with patch.object(proxy.socket, "socket", return_value=listener), \
                patch.object(proxy.os, "chmod") as chmod, \
                patch.object(proxy, "_serve", new_callable=AsyncMock):
            await proxy.serve("protected/proxy.sock", HOSTS)
            chmod.assert_called_once_with("protected/proxy.sock", 0o666)
            listener.bind.assert_called_once_with("protected/proxy.sock")
            listener.bind.reset_mock()
            await proxy.serve_relay("protected/proxy.sock")
            listener.bind.assert_called_once_with(("127.0.0.1", 8080))
        client, remote = Writer(), Writer()
        with patch.object(proxy.asyncio, "open_unix_connection", AsyncMock(return_value=(stream(b"reply"), remote))) as opened:
            await proxy.handle_relay(stream(b"request"), client, "protected/proxy.sock")
            opened.assert_awaited_once_with("protected/proxy.sock", limit=proxy.BUFFER_LIMIT)
        self.assertEqual(bytes(remote.data), b"request")
        self.assertTrue(client.closed and remote.closed)

    async def test_concurrency_cap_before_handler_and_shutdown_cleanup(self):
        listener = MagicMock(family=socket.AF_UNIX)
        server = MagicMock()
        server.__aenter__ = AsyncMock(return_value=server)
        server.__aexit__ = AsyncMock(return_value=False)
        server.serve_forever = AsyncMock(side_effect=asyncio.CancelledError)
        clients = [Writer() for _ in range(proxy.CONNECTION_LIMIT + 4)]
        calls = []

        async def factory(callback, **kwargs):
            for client in clients:
                callback(stream(), client)
            self.assertTrue(all(client.closed for client in clients[proxy.CONNECTION_LIMIT:]))
            await asyncio.sleep(0)
            return server

        async def handler(reader, writer):
            calls.append(writer)
            try:
                await asyncio.Future()
            finally:
                writer.close()

        with patch.object(proxy.asyncio, "start_unix_server", factory):
            with self.assertRaises(asyncio.CancelledError):
                await proxy._serve(listener, handler)
        self.assertEqual(len(calls), proxy.CONNECTION_LIMIT)
        self.assertTrue(all(client.closed for client in clients))

    async def test_shutdown_before_handler_first_step(self):
        listener = MagicMock(family=socket.AF_UNIX)
        clients = [Writer() for _ in range(proxy.CONNECTION_LIMIT)]

        async def factory(callback, **kwargs):
            for client in clients:
                callback(stream(), client)
            raise asyncio.CancelledError

        handler = AsyncMock()
        with patch.object(proxy.asyncio, "start_unix_server", factory):
            with self.assertRaises(asyncio.CancelledError):
                await proxy._serve(listener, handler)
        handler.assert_not_awaited()
        self.assertTrue(all(client.closed for client in clients))


if __name__ == "__main__":
    unittest.main()
