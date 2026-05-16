// Low-latency HLS player using hls.js.
//
// Used in production when MediaMTX is exposed via Cloudflare Tunnel —
// pure HTTP transport, so it works through any HTTP-only proxy.
// Native <video src="*.m3u8"> only works in Safari + mobile; hls.js
// covers desktop Chrome / Firefox / Edge.

import Hls from "hls.js"

export interface HlsSession {
  hls: Hls | null
  /** if hls.js is unavailable, we're using native <video>. */
  native: boolean
}

export function attachHls(
  url: string,
  video: HTMLVideoElement,
): HlsSession {
  if (Hls.isSupported()) {
    const hls = new Hls({
      // Standard HLS. LL mode produces silent black screens through a
      // Cloudflare-tunneled MediaMTX HLS-LL endpoint: hls.js v1.6 + the
      // `#EXT-X-TARGETDURATION:0` MediaMTX emits for sub-second segments
      // + blocking-playlist part fetches over the CDN don't combine
      // reliably. Falling back to standard HLS adds ~2-3 s latency for
      // a much more robust pipeline.
      lowLatencyMode: false,
      backBufferLength: 30,
      liveDurationInfinity: true,
      enableWorker: true,
    })
    hls.loadSource(url)
    hls.attachMedia(video)
    return { hls, native: false }
  }
  // Safari & iOS: native HLS in <video>
  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url
    return { hls: null, native: true }
  }
  // Unsupported browser
  throw new Error("HLS not supported in this browser")
}

export function detachHls(session: HlsSession, video: HTMLVideoElement): void {
  if (session.hls) {
    try {
      session.hls.destroy()
    } catch {
      // ignore
    }
  } else if (session.native) {
    video.removeAttribute("src")
    video.load()
  }
}
