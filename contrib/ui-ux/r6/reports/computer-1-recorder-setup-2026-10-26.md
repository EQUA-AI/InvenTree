# Computer 1 recorder setup — 26 October 2026

## Status: BLOCKED

Software installation and local controller setup are complete. This is **not**
READY TO RECORD: screen permission, the dedicated AIMMS login and commercial
license confirmation remain outstanding. No capture consent was provided, so
no smoke clip or actual demo was recorded.

## Installed and verified

| Component | Actual version / location | Evidence |
| --- | --- | --- |
| macOS / architecture | 26.6.2 build25G83; arm64 / Apple M5 Pro | Host inspection |
| Existing Node / npm | v26.3.1 /11.17.0, reused rather than upgraded | Executed version commands |
| Existing Codex |0.164.0 | Executed version command |
| Cap Desktop |0.6.1, `~/Applications/Cap.app` | Vendor installer; codesign verification exit0 |
| Bundled Cap CLI |0.1.0, `~/.local/bin/cap` | version/guide/help/doctor commands |
| Workspace Playwright |1.64.1, isolated package/lockfile | Installed only in recorder workspace |
| Actual headed Chrome |155.0.8059.85 | Real persistent browser launched |
| FFmpeg / FFprobe |9.0.2 | Homebrew support installation and actual binaries |
| Cap Codex skills | `~/.codex/skills/cap/SKILL.md`, vendor `cap-demo` companion | Official all-component installer |
| Cap MCP | `~/.codex/config.toml`, absolute CLI command + `mcp serve` | Local stdio initialize and76tool schemas verified |

Workspace: `/Users/Aniketkumar/EQUA-AIMMS-Recorder`.

The integration dry run was inspected before applying Codex-only changes.
A private600-mode original config backup exists. Parsed before/after config
comparison confirms all unrelated settings are preserved. The Cap command is
absolute, avoiding PATH dependence in non-interactive Codex. The vendor added
its PATH entry to the shell profile. Restart this already-running Codex session
when convenient to load the new skills/MCP; CLI works without restarting it.
The76-tool stdio probe is not a claim that this conversation hot-loaded Cap.

Cap creator-profile authentication is now server-verified. A narrow
`caps list --limit 1` read succeeds; library content is not displayed or handed
off. No password, token or license key was requested or exported. The MCP
schema inspection found no exposed secret-input properties. The vendor's
cap-demo companion files were installed by its all-component integration,
but no Remotion dependencies, BlackHole, ElevenLabs, Azure Speech or 3D/demo
pipeline were installed or executed.

## Application and privacy boundaries

- Actual existing app `http://localhost:5173/`; backend8000 API returns200.
- Repository branch `inventTree-aniket`, HEAD
  `721c72f08869d52f52fdd3f126d64fbe9e4e72c2`, with pre-existing uncommitted work.
  This is source provenance, not a claim that a dirty runtime is an identical
  fresh build of HEAD. Existing backend image is89981388aff5… .
- Primary container environment declares database `aimms_azure_copy`; no
  disposable-isolation/business-write approval is assumed. No database was
  migrated, reset or modified. The prior isolated5180/8007 runtime remains
  available but is not silently selected or granted new business permissions.
- No application dependencies, package locks, working build, deployment, Azure,
  equipment, alarms, work orders, stock, purchases, approvals or closeout
  records were changed.
- One headed Chrome process owns `chrome-profile/` and remains stable across
  controller calls. The profile/root/private directories are700, IPC socket600.
  No personal Chrome profile or exported cookies/tokens are used.
- The new browser is on native AIMMS login, awaiting manual demo-account sign-in.
  No login screenshot or authenticated navigation is claimed.
- Read-only browser requests are real. Business writes are blocked; narrowly
  scoped manual allauth authentication is the only POST exception.

## Controls implemented and exercised

These are new workspace commands, not claimed vendor commands:

```sh
cd ~/EQUA-AIMMS-Recorder
npm run doctor
npm run inspect
npm run smoke
npm run shoot -- --scene F01
npm run export
npm run handoff
npm test
```

- Doctor: live Cap permission/targets/auth/active-recording checks, FFmpeg,
  FFprobe, local HTTP, private browser, disk and license-attestation status.
- Inspect: starts/reuses the private headed browser. Actual unauthenticated
  state is BLOCKED. Authenticated source-inspected routes/semantic locators
  are implemented but not claimed verified before manual login.
- Smoke: proposal only by default. Actual capture requires an additional
  `--record` and exact operator consent in a real terminal. It discovers live
  Chrome windows, never guesses IDs or automatically falls back to the desktop.
- Shoot: all11runbook scene titles are present; execution is fail-closed until
  the exact future script, bindings, authorities and separate authorizations
  arrive. Even `--record` cannot run an unprovided F01 story.
- Export: proposal-only until an actual owned stopped take and explicit local
  export approval exist. Uses Cap project validation and Cap export; no
  alternate recorder. FFprobe/decode/frame checks are implemented but no
  successful media export is claimed.
- Handoff: an honest zero-take template, scene manifest, readme and checksums
  were generated; metadata checksum verification passes. Profiles, private
  configs, logs, credentials, databases and raw customer documents are excluded.
  No cloud upload or transfer occurred.

## Safety validation

**10/10 controller tests pass**, including loopback restrictions, pretty JSON
and NDJSON, actual-ID/stop-metadata requirements, business-write refusal,
path boundaries, exact-ID cleanup after operation/start failures, and denial
of an unprovided story even with a record flag. These are fixture/unit safety
tests, not actual Cap capture acceptance.

Actual native negative check: Cap rejects an intentionally empty
`private/invalid-validation-fixture.cap` with valid=false and exit1. This is
explicitly an invalid fixture, not a recording or fake success.

Cap reports zero active detached recordings, disabled automations, and
captureReady=false because Screen Recording permission is denied. There are
no source-cap projects or MP4s in recorder sessions. No actual recording ID,
stopped-event metadata, valid recorded project, MP4 pixel measurement, media
playback or source-media copy/restore is claimed.

Start/stop controls preserve actual returned IDs/paths/raw events and local
observed timestamps. Cleanup uses the same ID in finally; signal cancellation
drives stop; a native15-second guard bounds an interrupted12-second smoke
capture. Recording/stop/metadata failure is BLOCKED, not success. Unrelated
recordings are not stopped. Recovery receipts remain local/private.

## Remaining human gates / deferred acceptance

1. Enable **Cap only** in macOS Privacy & Security → Screen & System Audio
   Recording. Its settings pane is open. No TCC bypass, Accessibility, Full
   Disk Access, camera or microphone grant was made.
2. Sign into the demo account manually in the already-open dedicated Chrome.
3. Confirm an **activated commercial license** privately in Cap; no purchase
   or billing change is performed. Cap cloud sign-in alone does not prove a
   desktop commercial license. Do not send its key.
4. Authorize a specific12-second window-only Studio smoke test later. Current
   setup instructions do not constitute consent for that capture.
5. After that test: require actual stopped metadata, native project validation,
   Cap export, FFprobe source/export pixels/FPS/audio, actual visual/playback,
   menus/popovers framing and real Cap-source/MP4 copy/restore verification.

The display is3024×1964 Retina, not16:9. The private browser content viewport is
1280×720CSS at2× backing scale; this does not establish actual Cap source pixels.
Export is configured1920×1080/30fps, silent, maximum quality. Actual bounds and
legibility are deliberately unverified until the authorized capture. Connect
power and set Focus/notifications appropriately before a take; no persistent
system power/privacy workaround was made.

The bundle version probe lists a standalone cap-exporter binary as absent.
The installed CLI exposes native export; whether this build needs that helper
remains unverified until a real authorized export. Do not assume it is broken,
claim export success, fabricate a source project, or substitute FFmpeg capture.

The separate Simple Demo Flow and executive blueprint named inside the master
prompt were not supplied/found at the referenced adjacent locations. The owner
explicitly says exact tools/script/instructions will arrive later. F01–F11
titles therefore remain provisional, with no invented Pump02 IDs, sensor
binding, diagnosis, ROI, parts, future-date delivery or approvals. Business-flow
readiness/reset rehearsal remains separate and BLOCKED.

## First approved test command

After the human setup gates and explicit window/audio proposal are approved:

```sh
cd ~/EQUA-AIMMS-Recorder && npm run smoke -- --record
```

The wrapper asks for the exact current window consent phrase; it does not start
silently. The first full-story shoot remains blocked until the later approved
script is provided. No Git commit, push or deployment is part of this setup.

## References and evidence

- `~/EQUA-AIMMS-Recorder/logs/readiness.json`
- `~/EQUA-AIMMS-Recorder/logs/controller-tests.txt`
- `~/EQUA-AIMMS-Recorder/logs/cap-mcp-probe.json`
- `~/EQUA-AIMMS-Recorder/docs/source-manifest.json`
- `~/EQUA-AIMMS-Recorder/README.md`
- [Official Cap setup](https://cap.so/docs/agents/setup)
- [Cap consent and verification safety](https://cap.so/docs/agents/safety)
- [Cap CLI lifecycle](https://cap.so/docs/agents/workflows)
- [Cap commercial license requirements](https://cap.so/docs/commercial-license)
