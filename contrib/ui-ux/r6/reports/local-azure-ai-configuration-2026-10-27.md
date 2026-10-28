# Local Azure AI provider configuration

## Post-grant execution — 28 October 2026

The operator grants secret-read access. A fresh authorized secret listing now
returns the existing OpenAI/Search values. They are written directly into the
private selected-provider.env file, without displaying or committing either
value. All three local AI processes are reapplied with Azure API-key mode;
local-model endpoint/model remain empty. UI is HTTP200 and backend health is
alive/AI-ready. No manual key paste is required.

Post-grant read-only inspection confirms actual serving revision0000119 and
the ready latest IOT revision have identical selected API/model/pin/retrieval
settings and the same app-scoped secret references. Earlier access restrictions
below describe the pre-grant state. Azure configuration is not changed.

Actual native minimal API probes pass for both deployments and their expected
identities: gpt-5.6-luna-2026-07-09 and gpt-4.1-2025-04-14. Native backend boot
also verifies text-embedding-3-large and the controlled-document index. The
automatic chat boot probe remains disabled as configured; the explicit native
chat probes provide this execution's evidence. Model probes do not establish
full script acceptance.

The first Azure UI check reveals HTTP500on thread/focus reads before inference:
provider-file replacement omitted the existing local single-site policy key.
The application correctly refuses an empty server scope. The reapplication
helper is corrected to merge the original local recording settings with only
the selected Azure overrides. The stable policy key, work-intake AI, closeout
extractor and all other original nonprovider environment values are verified
unchanged after reapplication. No permission/scope guard is weakened. A fresh
native UI replay passes; the failed run remains as regression evidence.

Actual native admin38replay on28October completes one station93question:
"Which pumps are running at this station?" HTTP200SSE finishes in11.224seconds
without a turn error. The answer identifies exactly1of14running pumps:
Pump08/machine102, matching the real station mimic. Thread list and existing
focus reads return200; final persisted focus is machine93/version1. No500,
pageerror or blocked business write occurs. Three initial404reads refer to
newly generated unpersisted thread IDs. The answer screenshot is inspected.
Evidence: browser-smoke-azure-20261028-replay1/result.json, native-chat.sse and
station93-native-chat-answer.png under the deployment evidence root. This
proves the scoped station query, not the entire original V5 script or attachment
and media workflows. The owned browser closes; local app services remain up.

Cloud attachment embeddings require a separate Cohere credential path and
media uses GCP WIF. Those local planes remain dark; OpenAI/Search key setup
does not establish complete cloud attachment/media parity. Both local databases
remain at997migrations; no additional database migration is required.

## Earlier blocked setup

Owner directs replacing local Ollama/Qwen calls with Azure API-key models.
The current Azure Container App template is inspected read-only:
`aimms-experimental`, resource group `EpconChat`.

Selected configuration:

- Provider azure; API-key authentication; keyless authentication off.
- Endpoint https://aimms-foundry.openai.azure.com/.
- API version2025-04-01-preview.
- Primary/Luna deployment gpt-5.6-luna; expected gpt-5.6-luna-2026-07-09.
- Fast deployment gpt-4.1; expected gpt-4.1-2025-04-14.
- Embedding deployment text-embedding-3-large.

Private input file, mode0600 in a0700directory:
`data/devscratch/local-deploy-20261027/private/azure-ai/selected-provider.env`.
Nonsecret values are filled. AZURE_OPENAI_API_KEY and AZURE_SEARCH_API_KEY are
blank. The current Azure login cannot retrieve them: direct revision and
Cognitive Services reads return AuthorizationFailed; unfiltered Container App
secret listing returns an empty array despite metadata secret references.
Bounded related-project credential checks find no usable existing key.

Serving-revision equivalence is not established. The template identifies
ready revision iot-7e33232c7d09-1027, while configured ingress traffic points
to0000119; direct revision access is denied. Actual provider model identities
remain unverified until a successful API probe. No cloud permissions, secrets,
traffic or resources are changed.

All three local AI-serving processes are recreated with provider azure and
empty local-model endpoint/model fields. The unconditional private Qwen
overrides are removed. Local database, mail, safety and permission settings
are preserved. Previous container definitions/configuration remain privately
archived. Local UI returns200 and backend liveness is alive; AI readiness is
not_ready because the Azure API key is missing. There is no local-model fallback.

The operator is asked to save the key privately in the selected-provider.env
file. Once supplied, run the task-owned apply_azure_provider.py helper without
--allow-missing-key to recreate the local containers with the saved values,
then verify native model probes and the admin station query. Keys are never
printed, added to frontend configuration or committed to source.

PostgreSQL remains current: both local databases have997migrations matching
the earlier fresh Azure audit; provider configuration adds no new migration.

The completed admin smoke verifies user38 is staff/superuser, dashboard,
station93, Pump102 Health, WO3992 and packet34. All fourteen pump labels and
legend fit the1920x1080viewport. The picker renders; a root-wrapper locator
times out, so it is not recorded as a product failure. An earlier local-model
question begins just before provider-change cancellation; no answer is accepted,
and it is not Azure evidence. The owned browser closes and old local-provider
containers stop. No recording or business-workflow action is performed.
