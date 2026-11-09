"""Untrusted-side TCP-to-UDS bridge and command supervisor. Contains no real credentials."""
import argparse
import asyncio
import os
import signal


async def serve(args):
    async def connect(reader, writer, socket_path):
        upstream = None
        try:
            remote, upstream = await asyncio.open_unix_connection(socket_path)
            async def copy(source, target):
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
                if target.can_write_eof():
                    target.write_eof()
            await asyncio.gather(copy(reader, upstream), copy(remote, writer))
        except (OSError, ConnectionError):
            pass
        finally:
            writer.close()
            if upstream:
                upstream.close()

    server = await asyncio.start_server(lambda r, w: connect(r, w, args.socket), "127.0.0.1", args.port)
    site = None
    if args.site_socket:
        site = await asyncio.start_server(lambda r, w: connect(r, w, args.site_socket), "127.0.0.1", 31080)
    child = await asyncio.create_subprocess_exec(*args.command, start_new_session=True)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, lambda s=sig: os.killpg(child.pid, s) if child.returncode is None else None)
    try:
        return await child.wait()
    finally:
        server.close()
        await server.wait_closed()
        if site:
            site.close()
            await site.wait_closed()
        if child.returncode is None:
            os.killpg(child.pid, signal.SIGTERM)
            await child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/eipi-broker.sock")
    parser.add_argument("--port", type=int, default=30014)
    parser.add_argument("--site-socket")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command.pop(0)
    if not args.command:
        parser.error("A command is required")
    raise SystemExit(asyncio.run(serve(args)))
