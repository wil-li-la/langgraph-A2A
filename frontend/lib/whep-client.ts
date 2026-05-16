// Minimal WHEP (WebRTC HTTP Egress Protocol, RFC 9725) client.
//
// Used to attach a MediaMTX WebRTC stream to a <video> element.
// MediaMTX exposes one WHEP endpoint per path at /<path>/whep — POST an
// SDP offer there, get an SDP answer back plus a Location header pointing
// at the session resource (used to DELETE on tear-down).
//
// Latency: ~80 ms end-to-end on LAN once peer connection establishes.
// Resource cost: one PeerConnection + one transceiver per stream. Browsers
// don't cap WebRTC streams the same way as HTTP/1.1 — 16+ concurrent
// streams from one MediaMTX origin works.

export interface WhepSession {
  pc: RTCPeerConnection
  /** server-issued resource URL — DELETE here on cleanup */
  resourceUrl: string | null
}

export async function connectWhep(
  endpoint: string,
  video: HTMLVideoElement,
  signal?: AbortSignal,
): Promise<WhepSession> {
  const pc = new RTCPeerConnection({
    iceServers: [], // LAN host-candidates only; add STUN if you tunnel
    bundlePolicy: "max-bundle",
  })

  // MediaMTX paths from RTSP source are video-only; don't request audio
  // (some browsers reject the answer if the offer has m-lines the answer
  // doesn't bind).
  pc.addTransceiver("video", { direction: "recvonly" })

  pc.ontrack = (ev) => {
    if (video.srcObject !== ev.streams[0]) {
      video.srcObject = ev.streams[0]
    }
  }

  const offer = await pc.createOffer()
  await pc.setLocalDescription(offer)

  // Send the offer immediately without waiting for ICE gathering to
  // complete. MediaMTX handles trickle-ICE via the PATCH endpoint, and
  // for LAN host-candidates the SDP carries enough info to start.
  // (Waiting for gather complete serialized poorly under 8 concurrent
  // peer connections — sometimes only one POST would fire.)
  if (signal?.aborted) {
    pc.close()
    throw new DOMException("aborted", "AbortError")
  }

  const resp = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription?.sdp ?? "",
    signal,
  })
  if (!resp.ok) {
    pc.close()
    throw new Error(`WHEP POST ${endpoint} → ${resp.status} ${resp.statusText}`)
  }
  const resourceUrl = resp.headers.get("Location")
  const answerSdp = await resp.text()
  await pc.setRemoteDescription({ type: "answer", sdp: answerSdp })

  return {
    pc,
    resourceUrl: resourceUrl
      ? new URL(resourceUrl, endpoint).toString()
      : null,
  }
}

export async function disconnectWhep(session: WhepSession): Promise<void> {
  try {
    session.pc.close()
  } catch {
    // ignore
  }
  if (session.resourceUrl) {
    try {
      await fetch(session.resourceUrl, { method: "DELETE" })
    } catch {
      // server may already have torn down — ignore
    }
  }
}
