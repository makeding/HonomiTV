# TLV playback notification contract

DPlayer and tlvdemux own TLV demuxing, playback recovery, and the complete
`tlv_playback_damage` / `tlv_error` event payloads. They do not own HonomiTV's
human-facing error UI.

`PlayerController` is the presentation boundary for those events. It generates
HonomiTV's copy and displays it through the current DPlayer instance's public
`notice()` API, which is the lower-left notice inside the player:

- every severe `tlv_playback_damage` is shown through the current DPlayer
  instance's lower-left `notice()` UI;
- the message identifies the damaged recording or live stream, explains
  whether playback will skip, wait, or stop, gives the next action, and keeps
  the stable `TLV_SOURCE_DAMAGE` code;
- every `tlv_error` keeps the concrete failure reason and is shown through the
  same lower-left in-player notice;
- repeated callbacks for one damaged interval are deduplicated within one
  `PlayerController.init()` generation, and a later initialization may notify
  again;
- DPlayer's TLV core must not invent copy or automatically display TLV failures;
  the HonomiTV adapter is the sole caller of `notice()` for these events;
- HonomiTV's global `Message` Snackbar/Toast must never be used for TLV
  failures, so TLV failures stay inside the player and cannot stack with global
  application notifications.
- applying the initial recorded-playback position must call DPlayer's silent
  seek (`seek(position, true)`) and must never call `hideNotice()` as cleanup;
  cleanup for the seek operation must not erase a TLV notice that arrived
  concurrently during startup.
- TLV playback must never enter HLS / Native HLS fallback presentation. The
  iOS / iPadOS Native HLS compatibility warning is valid only when the active
  DPlayer quality itself has `type: 'hls'` and no hls.js plugin was created.

## MH-AIT broadband applications

TLV application collection readiness only describes broadcast VFS files. An
MH-AIT HTTP(S) transport plus entry path is a broadband launch candidate even
when `entryReady` is false and no DDMT/DAMT files are transmitted. HonomiTV owns
URL resolution and launch through its existing same-origin ARIB VFS proxy;
tlvdemux must not download web applications or pretend a network file was
received over broadcast. Prefer a ready broadcast entry when available.

Broadband launch preserves MH-AIT identity, priority, AUTOSTART/PRESENT/KILL,
service reset, and pending D-button delivery. Internet-disabled settings must
prevent all broadband loads and explain how to enable access when D is pressed.
Only explicitly supported public broadcaster hosts may use the proxy; existing
redirect validation, public-IP checks, size limits, and credential stripping
remain in force. A failed launch leaves ordinary playback and D-button retry
available with a concrete reason and stable error code. Switching services
invalidates pending launches. Broadcast carousel applications keep their VFS
readiness barrier. Network pages use the same receiver APIs and stationary
DPlayer video plane as carousel pages.

Acceptance includes the real MH-AIT from BS TV Tokyo 4K service 1100171:
organization 0x102e, application 1, AUTOSTART, transport
`http://hc.bsjhc.jp/hc/`, entry `entry/html_mm/startup.min.html`, with no broadcast
data transmission. Verify D-button portal, resource/navigation requests,
network-disabled behavior, retry and channel switch separately from signalling
and unit-test success.
