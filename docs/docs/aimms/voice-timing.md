# Voice validation timing contract (Phase V)

Collection is optional and default-off (`FEATURE_VOICE_VALIDATION_METRICS`,
requiring foreground voice). Timing never grants action or playback authority.
The existing exporter remains dark without an operator-configured exporter.

| Field | Definition / clock | Range / provenance |
| --- | --- | --- |
| `speech_to_final_ms` | Final transcript received minus speech-end event, browser monotonic | 0–300000 ms; provider event observation |
| `final_to_submit_ms` | HTTP submission minus final transcript received, browser monotonic | 0–300000 ms; includes local queue/hold |
| `ack_schedule_ms` | Earcon scheduled minus speech-end event, browser monotonic | 0–300000 ms; scheduling proxy, not audible acknowledgment |
| `submit_to_observed_playback_ms` | First correlated inbound audio-energy increase minus submission, browser monotonic | 0–300000 ms; RTP-energy proxy, not acoustic onset |
| `local_stop_ms` | Synchronous local pause completion minus screen/keyboard stop invocation, browser monotonic | 0–300000 ms; local control latency, not acoustic tail |
| `first_playback_at` | Client-reported UTC time of the correlated energy observation | Nullable; bounded against server UTC for usability only; never cross-clock latency |
| `timing_reported_at` | Server UTC receipt of first valid timing report | Nullable; distinct from client observation |
| `ms_server_receipt` | Origin of the voice HTTP handler's monotonic timing window | 0 ms by definition, not network travel time |
| `ms_route` | Routing-stage duration, server monotonic | Optional 0–300000 ms |
| `ms_tool` | Sum of measured canonical decision adapter / authorized tool invocation durations, server monotonic | Optional 0–300000 ms; absent if none executed; parallel work may overlap, so this is not end-to-end latency |
| `ms_tts_request` | Exact TTS dispatch offset from server receipt | Optional 0–300000 ms; request, not delivery |
| `ms_result` | HTTP result assembled offset from server receipt | Optional 0–300000 ms; not a fabricated business success |

Missing is null/absent, never zero. Numeric fields reject booleans, strings,
nonfinite values, negatives and values above five minutes. Objects reject unknown
keys. Existing language, item-ID and confidence metadata are unchanged. Timing
does not participate in a business idempotency fingerprint.

Client observations bind to an authenticated, active owner/scope session, a
server-issued timing epoch and an exact utterance/hash. Epoch rotation and
suspension invalidate older observations. Reports are rate-limited, first-valid
write wins, and do not update playback state, review state, receipts or activity.
All additive database fields are nullable; historical observations stay unknown.

RTP energy, provider completion and UI Speaking are **not proof of audible
output**. Audible acknowledgment/useful-audio gates require separately measured
evidence. No transcript, speech, preview, consent, recipient, supplier, exception
message, SDP, credential, recording or device fingerprint belongs in timing.
Opaque IDs belong in traces/events, not high-cardinality metric labels.
