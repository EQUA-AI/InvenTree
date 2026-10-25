# Pump08 motor-winding exercise — operator runbook

This is a controlled software exercise, not a physical maintenance instruction,
OEM limit, operating permission or claim that a motor has been repaired.
The October25 final work-order and verification specification takes precedence
over the two October24 presentation packs. All numeric record identities below
are local observations, not application constants.

## Deployment prerequisites

- Use the authorized localhost installation, not Azure or a plant SCADA source.
- Back up the local database; inspect and apply `tasks.0061_procedure_client_scope`.
  This adds explicit client ownership to governed procedures. It does not infer
  ownership of any legacy customer-only or unowned procedure.
- Work orders, test alarms and governed procedures must be enabled for this
  deployment. Work-intake AI is separately default-off:
  `AIMMS_WORK_INTAKE_AI_ENABLED`. A properly configured general Azure OpenAI
  endpoint, authentication and standard deployment are also required.
- No AI configuration means **AI drafting unavailable**, not a fake AI answer.
  Automatic creation uses the prepared Pump08 specification for its exact
  configured target. It does not require or pretend to call an AI provider.
- Use actual authorized accounts. Do not set a status directly, create fake
  signatures, borrow credentials or impersonate a named reviewer.

## 1. Trigger a single labelled source event

Open Cedar Creek Final Effluent Pump Station, Pump08 (observed local machine102),
then **Trigger test alarm**. Select **Electric Motor · PS-MOTOR:1**, then
**Motor Winding Temperature 6**. The picker lists the pump's actual mapped
parameters, measured reference, units, quality, configured limits and eligibility.
Select a finite synthetic value above the configured critical limit; the prepared
example is159.5°C against145°C. Leave **Create work order automatically** enabled.
Review the work preview, actual assigned owner and existing-work warning before
confirming the TEST notice. Nothing is triggered while this preview is unavailable.

Use the returned anomaly identity, never a scripted or guessed alarm number.
The indication must be labelled TEST and critical in the affected mimic, chart,
KPI and board; the underlying measured/historian values must remain unchanged.
Winding5 is comparison context only, not a fabricated second fault or vote.
Email/notification requests use configured recipients and channels; queued or
locally captured mail does not prove external SMTP delivery.

## 2. Review the prepared work before triggering

The preview appears inside the trigger dialog, before creating any records.
It uses the document scope for Pump08 / Electric Motor / Winding Temperature6,
and a generic investigation scope for other mapped targets. Other parameters
must not receive the Pump08 winding-six diagnosis. Review against the documents:

| Field | Reviewed value |
| --- | --- |
| Title | `Pump 08 \| High Motor Winding Temperature` |
| Type / priority / criticality | Corrective / High / Critical |
| Asset / component / primary channel | Pump08 / Electric Motor / Winding Temperature6 |
| Scope | Investigate and verify source, sensor path, load and cooling; inspect before any conditional cleaning |
| Potential impact | Pump08 availability interruption; actual process impact not established |
| Owner | Actual active, authorized Avery Patel, resolved by `cc.avery.patel` |
| Due date / effort / downtime | Blank until established |
| Parts | None; do not fabricate stock demand or reservations |
| Packet | Create one linked draft Repair Packet and primary card |

Assignment proposes a native handover. It does not mean the owner accepted it,
has a particular trade qualification or performed previous work on this fault.
Daniel/Priya remain support/reviewer suggestions subject to actual identity,
permissions and independent review. No personnel history is rewritten.

Existing bearing-vibration **WO-003826 must remain unchanged**. Inspect the
duplicate warning. A separate thermal investigation needs a deliberate reason,
such as “Separate motor-winding thermal investigation; bearing-vibration
WO-003826 remains unchanged and does not own this source event.” An override
never transfers an already-owned anomaly.

Enter a deliberate separate-work reason and confirm the existing-work review.
Then confirm the synthetic exercise and select **Trigger and create work order**.
One atomic request creates the synthetic alarm, actual native planned work order,
typed assignment, linked draft Repair Packet and primary card. A refused work
creation also rolls back the alarm and its queued notification. The work order
opens using the actual returned ID; no second creation button is required.

An uncertain outcome retains the exact body, signed preview and idempotency key
through reload. **Retry same test request** retrieves the same aggregate, even if
the preview expired after it committed. Changing the owner/selection under that
key is refused. A definite conflict requires renewed review, never an automatic
override. Existing work is linked in a new tab so reviewing it preserves the form.

Optional alarm-only mode remains available by explicitly turning off automatic
creation. Only that mode uses **Create work for this test** afterwards. Its native
form supports separately configured, tool-free AI drafting or the prepared
specification action, with human review. This is not the default automatic flow.

## 3. Author and independently publish the governed exercise definition

An authorized author can prepare the native draft:

```sh
python manage.py prepare_pump08_procedure --machine 102 --author cc.avery.patel
```

The command returns actual procedure/revision/content-version identities and
does not review, publish, apply or execute anything. Re-running it returns the
existing family without overwriting its content. Review the returned status.

An independent, permitted reviewer requests review through the native procedure
revision API using the returned revision ID and current content-version token.
Use the resulting normal publication approval: open it, review its exact content
and approve its current revision/review hash. The author cannot self-review.
Do not directly update `status`, invent an approval, bypass scope, or claim a
prepared software definition is an OEM-approved physical return procedure.

## 4. Record the three exercise checks

On the new work order, open **Verify & Return**. The controlled exercise panel is
separate from physical verification, technical acceptance, operating permission,
observed return and service state. Apply the actual published current revision
`P08-MOTOR-COOL-RTS-EX` through the native versioned action.

Explicitly attach the three labelled prepared evidence sheets. These are actual
job-owned attachments, not field work or signed physical evidence. Each check
requires its evidence, note, actual actor/time and native completion event.

| Check | Prepared input | Required prepared evidence |
| --- | --- | --- |
| Cooler restored | Yes | E03 + E05 |
| Maximum of Winding5 and6 below warning |96.0°C | E06 (W5=94°C, W6=96°C) |
| Stable prepared trial; source reviewed separately | Yes;15 minutes | E06 + actual source-status review |

The native inclusive bound is124.9°C:96 and124.9 pass that numerical bound;
125 and129 fail. The **prepared exercise verdict also requires agreement with
the supplied E06 values**, so124.9 with an unchanged96°C evidence sheet is not
approved. Do not change the sheet to make a value pass.

An absent/obsolete revision, missing or foreign attachment, changed snapshot,
false result, missing actor/time/event, reopened or N/A step cannot produce3/3.
Record129 as a negative test, verify refusal, then explicitly retest with the
reviewed96°C prepared value. Refresh and inspect retained identities and actor/time.
This case's required3/3 cannot be bypassed by marking native steps N/A.

## 5. Close out only through the owning packet

Use the native Repair Packet service-report and closeout workflow. Review the
report/PDF, submit, obtain an actual independent authorized approval and satisfy
all remaining native readiness, safety, assignment, execution and return gates.
The exercise panel never supplies physical release authority or auto-completes
the job. Do not invent permits, diagnoses, completed cleaning or signatures.

Only an actual successful owning-packet closeout should produce the single
linked maintenance-history record and reconcile its synthetic source. Confirm
the affected mimic/chart/KPI/board return to the unchanged measured state, with
no remaining TEST overlay for that event. Other real or independent conditions
must remain visible. SAP is successful only on a real configured acknowledgement;
otherwise show its unavailable/failed state honestly.

If publication, provider configuration or human release is unavailable, stop at
that boundary and record the missing prerequisite. A prepared3/3 software
verdict is not a substitute for operational permission or end-to-end acceptance.
