# S2 / S3 delegated review — Equipment view (P05–P08) and Performance (P11)

**19 October 2026. Status: agent review under owner delegation (O12), complete.** The user, the sole frontend/backend owner, said "do review on behalf of me" and then "wherever human review needed do on behalf of me as of now". This record is that review. It is not an independent human audit, accessibility certification, physical/domain acceptance or deployment acceptance, and it does not waive prerequisites (P02) or the rule that a pull request needs manual human review.

## Method

1. **Independent code review.** A separate reviewer agent read the S2 Equipment view (adapter, panel, inspector, positions endpoint, MachineDetail/trail/Health changes) and the S3 P11 changes with no access to the authoring context, and reported defects with scenarios. Every finding was checked against the code before acting; real defects got a failing test first where practical.
2. **Design review of real captures** against designprompt1's checklist (desktop density and hierarchy, phone containment, keyboard/focus, long/missing values, loading, stale, denied, empty, failure recovery) and R6 A03, A04, A05.7, A19.1/7.
3. **Replays.** All S0–S3 browser suites, units, types and the backend set after the fixes (see the checkpoint in `s2.md`).

## Code review findings and disposition

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | High | Following the station crumb, or "Open station mimic", from a pump's schematic bounced back to the pump: the legacy-link redirect ran while the previous machine was still loaded. | **Fixed** — redirect only when the loaded machine is the URL's. Regression test fails before (`review-fixes/red-before-station-crumb.log`), passes after, desktop and phone. |
| 2 | Medium | Inspector readings were a one-time snapshot with no age shown. | **Fixed** — each reading shows its observation time and stale state (native `ObservedAt`); the records are re-read every 30 s while visible and show when they were read. |
| 3 | Medium (possible) | Retrying while WebGL is still unavailable might leave the scene's own fallback mounted under the panel's message. | **Not reproduced** — a phase/canvas probe across three retries shows failed → loading → failed with the scene torn down each time. A synchronous-failure teardown was added anyway, and the test now retries twice with WebGL still blocked. The original test's flake (a retry still in flight when WebGL was restored) is fixed. |
| 4 | Medium (possible) | A user Part sharing a catalogue IPN could hide a slot and produce an untrue "no slot" message. | **Fixed** — slots match by IPN; family names come only from loader-owned catalogue Parts. Duplicate-IPN test fails on the old code (`review-fixes/red-before-duplicate-ipn/`). |
| 5 | Medium | "Nothing outside configured limits" could show while a signal with limits went unjudged (a fresh text value). | **Fixed** — calm also requires every limited signal judged; unit test added. |
| 6 | Low | The parent-work line ("2 open on the whole machine") read as contradicting the machine's own count (3). | **Fixed** — "2 other open on this machine" beside a position's own work; unbound positions keep "N open on the whole machine". |
| 7 | Low | Inspector labels sat on unlabelled-role divs, so screen readers dropped them. | **Fixed** — the inspector is a labelled region (`section`); its loading state is an `output`. Tests now find it by role. |
| 8 | Low | Raw values (long floats) and silent poor quality. | **Fixed** — numbers use the Performance formatter; non-numeric values print as sent; poor quality is named. |
| 9 | Low | Access denials offered a Retry that could never succeed. | **Fixed** — 401/403/404 say the records are not available to the reader, with no retry. |
| 10 | Minor | Props ref written during render; redirect effect dependencies incomplete. | **Fixed** — layout effect; dependencies complete. |
| — | Test gaps | Crumb/station-link click, retry with WebGL still blocked, duplicate IPN, customer-excluded work. | **Added.** S3's inventory JSON is compared by hand in the P11 checkpoint (identical before/after); automating that comparison is deferred. |

The reviewer found nothing significant in: the positions endpoint's scope and authorization, signal/work attribution, the `.d.mts` types, the wheel guard, mount-once/cleanup, the electrical axis rule and the TrendChart change.

## Design review

| Area | Result |
|---|---|
| Desktop density and hierarchy | Model about two thirds, inspector one third from 1200 px; component list below the model in three short columns. Toolbar groups mode, camera, zoom, focus/reset and labels on one line at 1280 px. Inspector order: identity, component slot, readings (value first, then time), work, parent context, links. Reviewed; the reading's time first out-weighed its value and was corrected. |
| Phone | No horizontal scroll; toolbar wraps; model, list and inspector stack. The inspector sits below the nine-row list, so a list choice needs a scroll to its details — acceptable, noted for P13 layout work. |
| Keyboard / focus | List items are buttons (Enter/Space), selection mirrored on the model's hotspots; canvas keyboard orbit/zoom; wheel scrolls the page until the model has focus. |
| Long / missing values | Every absence has its own words (no family, no slot, no signal, no work, work unknown, records unavailable, not available to you). |
| Loading / stale / failure | Same-height scene skeleton; inspector skeleton; stale and age per reading; 3D failure local with retry; records failure with retry; denial without. |
| Theming | Light and dark reviewed. The authored scene keeps its own light background in dark mode — legible, not themed; changing it means editing the reference scene. Accepted for now. |
| Performance P11 | Verdict strip and electrical axes reviewed in light/dark, desktop/phone; native inventory identical before and after. |

## P13 Parts tab, slice 1 (19 October)

Agent review under owner delegation (O12): an independent reviewer pass over the backend context, serializer, table and inspector, and a design review of real captures (`p13-parts/browser/`: before-review and after, desktop and phone, light and dark).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Medium (test gap) | No test exercised the Work scope filter on a slot's open work, the slice's main authorization rule. | **Fixed** - a customer-owned job on the slot is not counted; the test fails with the scope filter removed (`p13-parts/red-before-scope/`). |
| 2 | Medium (design) | The fit basis badge was cut to "DRAFT · FIT N…", hiding "not verified". | **Fixed** - short badge (Draft / Verified) with the fit basis on its own line. |
| 3 | Low | A station's own slots could get a 3D position, though only pumps have the model. | **Fixed** - position only for pump slots; test added. |
| 4 | Low | An older server's rows (no position field) and an unknown position id read as "Not in the model". | **Fixed** - "Not available"; browser case for rows without the new fields. |
| 5 | Low | Zero open work showed a bare dash. | **Fixed** - "None open". |
| 6 | Low | Stock docs said "available" (net of allocations natively) for the native in-stock quantity; the column read as per-slot on a station. | **Fixed** - wording "in stock"; column "Catalogue stock". |
| 7 | Low | A virtual part (logical group) showed "None in stock"; natively it cannot hold stock. | **Fixed** - null on the server, "Not stocked" in the table; test added. |
| 8 | Low (a11y) | Repeated "Open part record" links were indistinguishable to screen readers. | **Fixed** - labelled with the part name. Explanations in `title` tooltips remain secondary to the visible words. |
| — | Accepted | Phone: the eight-column table scrolls inside its frame (native table pattern); the page itself does not. Blueprint column order kept. "Discharge and valve" label (P07) is broader than O11's discharge line only - noted for P07. |

Sound per the reviewer: slot-own-machine work attribution, customer/client scope, no join duplication, constant query count, stock role gating matching the frontend, pagination ordering, no fitted fact invented.

## Outcome

Under O12 the review gate is cleared for P05, P06 and P08: their own criteria are met in isolated evidence and reviewed, and each now waits only on its prerequisite chain (P02 for P05; P05 for P06 and P08), so they are **Blocked**, not Done. P07 stays **In progress** (Identity & Parts depth, with P13). P11 stays **In progress** (S3-05 station view, S3-06/S3-07 audits). Evidence: `data/devscratch/r6-s2/2026-10-19/review-fixes/`.
