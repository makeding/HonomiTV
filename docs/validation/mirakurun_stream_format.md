# Mirakurun Service Stream format negotiation

HonomiTV requests `GET /api/docs` before selecting a Service Stream container.  The
response must be Swagger 2.0 and expose both `ts` and `tlv` in the string `format` query
parameter of `/services/{id}/stream`; the parameter may be at path level or inside
the GET operation.  The request has a two-second timeout, and the result is cached by backend docs URL for 60 seconds.  A
network or timeout failure is not cached, so a temporarily unavailable tuner can
recover without restarting HonomiTV.

When that contract is present, Raw MMTS and BS4K FFmpeg/libaribtlv inputs request
`decode=1&format=tlv`.  This keeps the TLV container while allowing Mirakurun's
independent B61 decoder to run.  Regular TS live inputs request
`decode=1&format=ts`, so decoder configuration cannot accidentally change the
container consumed by tsreadex.

Older Mirakurun and mirakc instances retain the previous requests: BS4K TLV paths
use `decode=0`, and normal TS paths omit the query.  HonomiTV does not retry a
stream request without `format` after a 406 response: OpenAPI is the compatibility
contract, and a 406 indicates a server-side conversion/configuration problem that
must remain visible.

`service.streamId`, when present as a non-boolean unsigned 16-bit value, becomes
the channel `transport_stream_id`.  Missing or malformed values retain any existing
stored TSID; only a previously empty GR channel falls back to its NID.
