#!/usr/bin/env python3
"""cam_bridge — pull MJPEG from upstream web_video_server, cache the latest
JPEG per camera, re-serve to browsers as either MJPEG (drop-in <img>) or
H.264 fragmented MP4 (low-bandwidth <video>).

Server: hypercorn (HTTP/2 + HTTP/1.1 over TLS). Browsers require TLS for
HTTP/2; multiplexing all camera streams over one TCP connection removes
the 6-per-origin HTTP/1.1 cap that otherwise limits how many tiles can be
live at once.

H.264 transcoding is shared per camera (one ffmpeg subprocess per cam,
fanned out to N clients via per-client asyncio.Queue). This holds NVENC
session count to at most ONE per camera regardless of viewer count.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import ssl
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

import aiohttp
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger("cam_bridge")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@dataclass
class Source:
    name: str
    base_url: str
    cams: list[int]
    insecure: bool = False
    ca_cert: Optional[str] = None


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 9998
    framerate: int = 30
    h264_encoder: str = "h264_nvenc"
    h264_bitrate: str = "2M"
    tls: bool = True
    cert_path: Optional[str] = None
    key_path: Optional[str] = None
    sources: list[Source] = field(default_factory=list)


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    sources = [Source(**s) for s in raw.get("sources", [])]
    return Config(
        host=raw.get("host", "0.0.0.0"),
        port=raw.get("port", 9998),
        framerate=raw.get("framerate", 30),
        h264_encoder=raw.get("h264_encoder", "h264_nvenc"),
        h264_bitrate=raw.get("h264_bitrate", "2M"),
        tls=raw.get("tls", True),
        cert_path=raw.get("cert_path"),
        key_path=raw.get("key_path"),
        sources=sources,
    )


# ---------------------------------------------------------------------------
# frame cache — latest JPEG per (source, cam_id), async notify on update
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    data: bytes
    seq: int
    ts: float


class FrameCache:
    def __init__(self) -> None:
        self._frames: dict[str, Frame] = {}
        self._conds: dict[str, asyncio.Condition] = {}
        self._seq: int = 0

    @staticmethod
    def key(source: str, cam_id: int) -> str:
        return f"{source}/{cam_id}"

    def _cond(self, key: str) -> asyncio.Condition:
        c = self._conds.get(key)
        if c is None:
            c = asyncio.Condition()
            self._conds[key] = c
        return c

    async def put(self, source: str, cam_id: int, data: bytes) -> None:
        key = self.key(source, cam_id)
        cond = self._cond(key)
        async with cond:
            self._seq += 1
            self._frames[key] = Frame(data=data, seq=self._seq, ts=time.time())
            cond.notify_all()

    def get(self, source: str, cam_id: int) -> Optional[Frame]:
        return self._frames.get(self.key(source, cam_id))

    async def wait_next(
        self, source: str, cam_id: int, last_seq: int, timeout: float = 10.0
    ) -> Optional[Frame]:
        key = self.key(source, cam_id)
        cond = self._cond(key)
        async with cond:
            try:
                await asyncio.wait_for(
                    cond.wait_for(
                        lambda: (f := self._frames.get(key)) is not None
                        and f.seq > last_seq
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return None
            return self._frames.get(key)

    def status(self) -> dict[str, dict]:
        now = time.time()
        return {
            key: {
                "seq": f.seq,
                "age_s": round(now - f.ts, 3),
                "bytes": len(f.data),
            }
            for key, f in self._frames.items()
        }


# ---------------------------------------------------------------------------
# upstream MJPEG puller
# ---------------------------------------------------------------------------


def _extract_boundary(content_type: str) -> Optional[str]:
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            return part.split("=", 1)[1].strip().strip('"')
    return None


async def _parse_multipart(
    body: aiohttp.StreamReader, boundary: str
) -> AsyncIterator[bytes]:
    delim = f"--{boundary}".encode()
    buf = bytearray()
    while True:
        chunk = await body.read(64 * 1024)
        if not chunk:
            return
        buf.extend(chunk)
        while True:
            i1 = buf.find(delim)
            if i1 < 0:
                if len(buf) > 4 * 1024 * 1024:
                    del buf[: len(buf) - 1024]
                break
            i2 = buf.find(delim, i1 + len(delim))
            if i2 < 0:
                if i1 > 0:
                    del buf[:i1]
                break
            part = bytes(buf[i1 + len(delim) : i2])
            del buf[:i2]
            jpeg = _extract_jpeg_payload(part)
            if jpeg is not None:
                yield jpeg


def _extract_jpeg_payload(part: bytes) -> Optional[bytes]:
    while part.startswith(b"\r\n"):
        part = part[2:]
    sep = part.find(b"\r\n\r\n")
    if sep < 0:
        return None
    body = part[sep + 4 :]
    if not body.startswith(b"\xff\xd8"):
        return None
    if body.endswith(b"\r\n"):
        body = body[:-2]
    return body


def _ssl_context(source: Source) -> Optional[ssl.SSLContext]:
    if not source.base_url.startswith("https://"):
        return None
    if source.insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if source.ca_cert:
        return ssl.create_default_context(cafile=source.ca_cert)
    return None


async def pull_loop(cache: FrameCache, source: Source, cam_id: int) -> None:
    topic = f"/cam_{cam_id}/image_raw"
    url = f"{source.base_url}/stream?topic={topic}&type=mjpeg"
    ssl_ctx = _ssl_context(source)
    backoff = 1.0
    while True:
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=15)
            connector = aiohttp.TCPConnector(ssl=ssl_ctx if ssl_ctx else True)
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout
            ) as sess:
                async with sess.get(url) as resp:
                    if resp.status != 200:
                        log.warning(
                            "[%s/%d] upstream HTTP %d, retry in %.1fs",
                            source.name,
                            cam_id,
                            resp.status,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30.0)
                        continue
                    boundary = _extract_boundary(resp.headers.get("Content-Type", ""))
                    if not boundary:
                        log.error(
                            "[%s/%d] no multipart boundary in upstream Content-Type",
                            source.name,
                            cam_id,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30.0)
                        continue
                    log.info("[%s/%d] upstream connected", source.name, cam_id)
                    backoff = 1.0
                    async for jpeg in _parse_multipart(resp.content, boundary):
                        await cache.put(source.name, cam_id, jpeg)
                    log.warning("[%s/%d] upstream EOF, reconnecting", source.name, cam_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[%s/%d] upstream error: %s, retry in %.1fs",
                source.name,
                cam_id,
                e,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


# ---------------------------------------------------------------------------
# shared H.264 transcoder pool — one ffmpeg per cam, fanned out to N clients
# ---------------------------------------------------------------------------


def _build_ffmpeg_cmd(config: Config, encoder: str) -> list[str]:
    # When using NVENC, also do JPEG decode on the GPU (mjpeg_cuvid +
    # CUDA-resident frames). Keeps the entire pipeline on the GPU and
    # avoids saturating CPU cores on 8+ concurrent 1080p MJPEG decodes —
    # CPU decode was the real bottleneck (NVENC averageFps was ~8 not 30).
    base = ["ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-fflags", "+nobuffer", "-flush_packets", "1"]
    if encoder == "h264_nvenc":
        base += [
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
            "-c:v", "mjpeg_cuvid",
        ]
    base += [
        "-f", "mjpeg",
        "-framerate", str(config.framerate),
        "-i", "pipe:0",
        "-c:v", encoder,
        "-profile:v", "baseline",
        "-g", str(config.framerate),
        "-bf", "0",
        "-b:v", config.h264_bitrate,
    ]
    if encoder == "h264_nvenc":
        base += ["-preset", "p1", "-tune", "ull"]
    else:  # libx264 — needs explicit pixel format (NVENC handles CUDA frames natively)
        base += ["-pix_fmt", "yuv420p", "-preset", "ultrafast", "-tune", "zerolatency"]
    base += [
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof+omit_tfhd_offset",
        "-frag_duration", "100000",
        "-f", "mp4", "pipe:1",
    ]
    return base


class SharedTranscoder:
    """One ffmpeg subprocess per camera. Reads JPEGs from FrameCache, emits
    fragmented MP4 H.264 to all attached client queues."""

    def __init__(
        self,
        source: str,
        cam_id: int,
        cache: FrameCache,
        config: Config,
    ) -> None:
        self.source = source
        self.cam_id = cam_id
        self.cache = cache
        self.config = config
        self.proc: Optional[asyncio.subprocess.Process] = None
        # Init segment (ftyp + moov boxes) — parsed once from ffmpeg output,
        # replayed to every joining client before live media chunks.
        self.init_segment: Optional[bytes] = None
        # Clients that joined before init was ready — promoted to active
        # once init lands.
        self._pending: set[asyncio.Queue] = set()
        self._active: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    async def start(self) -> None:
        cmd = _build_ffmpeg_cmd(self.config, self.config.h264_encoder)
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info(
            "[%s/%d] transcoder started (%s, pid=%d)",
            self.source,
            self.cam_id,
            self.config.h264_encoder,
            self.proc.pid,
        )
        self._tasks.append(asyncio.create_task(self._feed_jpegs()))
        self._tasks.append(asyncio.create_task(self._read_output()))
        self._tasks.append(asyncio.create_task(self._drain_stderr()))

    async def add_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=128)
        async with self._lock:
            if self.init_segment is not None:
                q.put_nowait(self.init_segment)
                self._active.add(q)
            else:
                self._pending.add(q)
        return q

    async def remove_client(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._pending.discard(q)
            self._active.discard(q)

    def client_count(self) -> int:
        return len(self._pending) + len(self._active)

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for t in self._tasks:
            t.cancel()
        if self.proc is not None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        # unblock any clients still reading
        async with self._lock:
            for q in list(self._pending | self._active):
                try:
                    q.put_nowait(b"")  # sentinel: empty bytes = EOF
                except asyncio.QueueFull:
                    pass
            self._pending.clear()
            self._active.clear()
        log.info("[%s/%d] transcoder shut down", self.source, self.cam_id)

    async def _feed_jpegs(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdin is not None
        last_seq = 0
        try:
            first = self.cache.get(self.source, self.cam_id)
            if first is not None:
                proc.stdin.write(first.data)
                await proc.stdin.drain()
                last_seq = first.seq
            while not proc.stdin.is_closing():
                frame = await self.cache.wait_next(
                    self.source, self.cam_id, last_seq, timeout=10.0
                )
                if frame is None:
                    continue
                proc.stdin.write(frame.data)
                await proc.stdin.drain()
                last_seq = frame.seq
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if proc.stdin is not None and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass

    async def _read_output(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        buf = bytearray()
        init_parsed = False
        try:
            while True:
                chunk = await proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                buf.extend(chunk)
                if not init_parsed:
                    # init segment ends 4 bytes before the first 'moof' box
                    idx = buf.find(b"moof")
                    if idx >= 4:
                        init = bytes(buf[: idx - 4])
                        del buf[: idx - 4]
                        await self._on_init_parsed(init)
                        init_parsed = True
                if init_parsed and buf:
                    await self._broadcast(bytes(buf))
                    buf.clear()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("[%s/%d] read_output: %s", self.source, self.cam_id, e)

    async def _on_init_parsed(self, init: bytes) -> None:
        async with self._lock:
            self.init_segment = init
            for q in list(self._pending):
                self._pending.discard(q)
                try:
                    q.put_nowait(init)
                    self._active.add(q)
                except asyncio.QueueFull:
                    # client never even got init — drop it
                    pass
        log.info(
            "[%s/%d] init segment ready (%d bytes)",
            self.source,
            self.cam_id,
            len(init),
        )

    async def _broadcast(self, chunk: bytes) -> None:
        async with self._lock:
            for q in self._active:
                try:
                    q.put_nowait(chunk)
                except asyncio.QueueFull:
                    # client is too slow — drop oldest and push new, keep up
                    try:
                        q.get_nowait()
                        q.put_nowait(chunk)
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass

    async def _drain_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                log.debug(
                    "[%s/%d ffmpeg] %s",
                    self.source,
                    self.cam_id,
                    line.decode(errors="replace").rstrip(),
                )
        except asyncio.CancelledError:
            pass


class TranscoderRegistry:
    def __init__(self, cache: FrameCache, config: Config) -> None:
        self.cache = cache
        self.config = config
        self._lock = asyncio.Lock()
        self._transcoders: dict[str, SharedTranscoder] = {}

    @staticmethod
    def _key(source: str, cam_id: int) -> str:
        return f"{source}/{cam_id}"

    async def acquire(
        self, source: str, cam_id: int
    ) -> tuple[SharedTranscoder, asyncio.Queue]:
        key = self._key(source, cam_id)
        async with self._lock:
            t = self._transcoders.get(key)
            if t is None:
                t = SharedTranscoder(source, cam_id, self.cache, self.config)
                await t.start()
                self._transcoders[key] = t
        q = await t.add_client()
        return t, q

    async def release(
        self,
        source: str,
        cam_id: int,
        transcoder: SharedTranscoder,
        q: asyncio.Queue,
    ) -> None:
        await transcoder.remove_client(q)
        async with self._lock:
            if transcoder.client_count() == 0:
                key = self._key(source, cam_id)
                if self._transcoders.get(key) is transcoder:
                    del self._transcoders[key]
                    await transcoder.shutdown()

    async def shutdown_all(self) -> None:
        async with self._lock:
            for t in list(self._transcoders.values()):
                await t.shutdown()
            self._transcoders.clear()


# ---------------------------------------------------------------------------
# Starlette handlers
# ---------------------------------------------------------------------------


_COMMON_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "no-store",
}


async def handle_index(request: Request) -> Response:
    config: Config = request.app.state.config
    tiles = []
    for src in config.sources:
        for cam_id in src.cams:
            label = f"{src.name}_cam_{cam_id}"
            tiles.append(
                f"<figure style='margin:0'>"
                f"<video src='/cam/{src.name}/{cam_id}/h264' "
                f"autoplay muted playsinline "
                f"style='width:100%;display:block;background:#000'></video>"
                f"<figcaption style='font:12px monospace;padding:2px 4px;"
                f"background:#222;color:#bbb'>{label}</figcaption>"
                f"</figure>"
            )
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>cam_bridge</title></head>"
        "<body style='margin:0;background:#111;color:#eee;"
        "font-family:monospace'>"
        "<header style='padding:8px 12px;border-bottom:1px solid #333'>"
        "cam_bridge — H.264 mosaic. "
        "MJPEG fallback: /cam/&lt;source&gt;/&lt;cam_id&gt;/mjpeg. "
        "Snapshot: /snap/&lt;source&gt;/&lt;cam_id&gt;.</header>"
        "<main style='display:grid;grid-template-columns:repeat(4,1fr);"
        "gap:4px;padding:4px'>" + "".join(tiles) + "</main></body></html>"
    )
    return Response(content=html, media_type="text/html", headers=_COMMON_HEADERS)


async def handle_healthz(request: Request) -> Response:
    cache: FrameCache = request.app.state.cache
    config: Config = request.app.state.config
    expected = {
        FrameCache.key(s.name, c) for s in config.sources for c in s.cams
    }
    have = cache.status()
    missing = sorted(expected - set(have.keys()))
    body = {"ok": not missing, "missing": missing, "cams": have}
    return JSONResponse(body, headers=_COMMON_HEADERS)


async def handle_cams(request: Request) -> Response:
    config: Config = request.app.state.config
    out = [
        {"source": s.name, "cams": list(s.cams), "base_url": s.base_url}
        for s in config.sources
    ]
    return JSONResponse(out, headers=_COMMON_HEADERS)


async def handle_snap(request: Request) -> Response:
    cache: FrameCache = request.app.state.cache
    source = request.path_params["source"]
    cam_id = int(request.path_params["cam_id"])
    frame = cache.get(source, cam_id)
    if frame is None:
        return PlainTextResponse(
            "no frame yet", status_code=503, headers=_COMMON_HEADERS
        )
    return Response(
        content=frame.data,
        media_type="image/jpeg",
        headers={**_COMMON_HEADERS, "Content-Length": str(len(frame.data))},
    )


async def handle_mjpeg(request: Request) -> Response:
    cache: FrameCache = request.app.state.cache
    source = request.path_params["source"]
    cam_id = int(request.path_params["cam_id"])
    boundary = "frame"

    async def gen() -> AsyncIterator[bytes]:
        last_seq = 0
        try:
            while True:
                frame = await cache.wait_next(source, cam_id, last_seq, timeout=10.0)
                if frame is None:
                    continue
                last_seq = frame.seq
                yield (
                    f"--{boundary}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame.data)}\r\n\r\n"
                ).encode()
                yield frame.data
                yield b"\r\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        gen(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers=_COMMON_HEADERS,
    )


async def handle_h264(request: Request) -> Response:
    cache: FrameCache = request.app.state.cache
    registry: TranscoderRegistry = request.app.state.registry
    source = request.path_params["source"]
    cam_id = int(request.path_params["cam_id"])

    if cache.get(source, cam_id) is None:
        return PlainTextResponse(
            "no frame yet", status_code=503, headers=_COMMON_HEADERS
        )

    transcoder, q = await registry.acquire(source, cam_id)
    log.info(
        "[%s/%d] h264 client connected (%d total)",
        source,
        cam_id,
        transcoder.client_count(),
    )

    async def gen() -> AsyncIterator[bytes]:
        try:
            while True:
                chunk = await q.get()
                if not chunk:  # EOF sentinel from shutdown
                    return
                yield chunk
        except asyncio.CancelledError:
            pass
        finally:
            await registry.release(source, cam_id, transcoder, q)
            log.info("[%s/%d] h264 client disconnected", source, cam_id)

    return StreamingResponse(
        gen(),
        media_type="video/mp4",
        headers={**_COMMON_HEADERS, "Connection": "close"},
    )


# ---------------------------------------------------------------------------
# app bootstrap
# ---------------------------------------------------------------------------


def build_app(config: Config) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        cache = FrameCache()
        registry = TranscoderRegistry(cache, config)
        app.state.config = config
        app.state.cache = cache
        app.state.registry = registry
        pullers: list[asyncio.Task] = []
        for src in config.sources:
            for cam_id in src.cams:
                pullers.append(asyncio.create_task(pull_loop(cache, src, cam_id)))
        app.state.pullers = pullers
        try:
            yield
        finally:
            for t in pullers:
                t.cancel()
            await asyncio.gather(*pullers, return_exceptions=True)
            await registry.shutdown_all()

    routes = [
        Route("/", handle_index),
        Route("/healthz", handle_healthz),
        Route("/cams", handle_cams),
        Route("/snap/{source}/{cam_id:int}", handle_snap),
        Route("/cam/{source}/{cam_id:int}/mjpeg", handle_mjpeg),
        Route("/cam/{source}/{cam_id:int}/h264", handle_h264),
    ]
    return Starlette(debug=False, routes=routes, lifespan=lifespan)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.yaml"),
        help="Path to config.yaml",
    )
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = p.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = load_config(Path(args.config))
    log.info(
        "cam_bridge starting on %s:%d (%d source(s), tls=%s)",
        config.host,
        config.port,
        len(config.sources),
        config.tls,
    )
    for s in config.sources:
        log.info("  source=%s url=%s cams=%s", s.name, s.base_url, s.cams)

    app = build_app(config)

    # hypercorn — HTTP/2 over TLS, HTTP/1.1 fallback. Browsers require TLS
    # for HTTP/2 (no h2c). For tunnel passthrough, set tls=false in config
    # and let the tunnel terminate TLS.
    import hypercorn.asyncio
    import hypercorn.config

    h_cfg = hypercorn.config.Config()
    h_cfg.bind = [f"{config.host}:{config.port}"]
    h_cfg.accesslog = None
    if config.tls:
        if not config.cert_path or not config.key_path:
            raise SystemExit(
                "TLS enabled but cert_path/key_path not set in config. "
                "Either disable TLS or run run.sh which generates a self-signed cert."
            )
        h_cfg.certfile = config.cert_path
        h_cfg.keyfile = config.key_path
        h_cfg.alpn_protocols = ["h2", "http/1.1"]
    asyncio.run(hypercorn.asyncio.serve(app, h_cfg))


if __name__ == "__main__":
    main()
