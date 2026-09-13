# Agent correspondence mailboxes

Agent correspondence is separate from InvenTree notification email. The new path is disabled by default. No provider is inferred from notification credentials or from another connected account.

## Current implementation and release gates

The backend provides account grants, encrypted credentials, immutable approval drafts, single-claim dispatch, receipts, private message and attachment storage, bounded polling, and SMTP/IMAP, Microsoft Graph and Google adapters. The Mail tab supports composing, reviewing, reading and replying. The admin email panel contains mailbox connection and pause controls.

This is **not a completed production rollout**. Live provider/account compatibility, recipient canaries, full browser acceptance, operational thresholds and legacy cutover require the deployment acceptance record. Plain SMTP password authentication, Graph delegated/application OAuth and Google delegated OAuth are the implemented authentication modes. SMTP XOAUTH2, provider webhooks and IMAP IDLE are not implemented.

Chat tools create approval drafts and return account-scoped message/attachment references. They do not copy private received message bodies into shared chat history or memory. Automated analysis of incoming mail needs a future derivative-access integration before it can safely be enabled. Generated business documents must be produced through their authorized business workflow and uploaded/scanned before attaching them; this path does not synthesize sample business records.

## Configuration

Set configuration through the deployment secret manager, not chat, source control or approval payloads.

| Environment variable | Purpose |
| --- | --- |
| `INVENTREE_AGENT_EMAIL_ENABLED` | Enable the connected mailbox service; default false |
| `INVENTREE_AGENT_EMAIL_SEND_PAUSED` | Stop new mailbox dispatch claims and legacy direct sends; default false |
| `INVENTREE_AGENT_EMAIL_CREDENTIAL_KEYS` | Comma-separated Fernet keys; first key encrypts, all keys may decrypt |
| `INVENTREE_AGENT_EMAIL_MESSAGE_ID_DOMAIN` | Operator-controlled domain for stable RFC Message-IDs |
| `INVENTREE_AGENT_EMAIL_OAUTH_REDIRECT_URI` | Exact registered HTTPS redirect URI for consent |
| `INVENTREE_AGENT_EMAIL_MICROSOFT_CLIENT_ID` | Shared Microsoft application ID for customer mailbox connections |
| `INVENTREE_AGENT_EMAIL_MICROSOFT_CLIENT_SECRET` | Shared application credential, supplied through a deployment secret reference |
| `INVENTREE_AGENT_EMAIL_PRIVATE_NETWORKS` | Explicit administrator-approved CIDRs for private SMTP/IMAP endpoints |
| `INVENTREE_AGENT_EMAIL_CLAMAV_SOCKET` | ClamAV Unix socket; defaults to `/var/run/clamav/clamd.ctl` |
| `AIMMS_EMAIL_RECIPIENT_ALLOWLIST` | Existing deployment-wide recipient restriction, applied in addition to account policy |

Apply database migrations before running the web application and workers. New tables are in `aichat` migrations 0036 and 0037. Keep the credential key ring outside database backups; retain it securely so restored credentials remain decryptable.

Each mailbox starts disabled, with sending and receiving paused and an empty recipient allowlist. An empty account allowlist blocks every recipient; `null` removes only the account restriction. Address and `@domain` entries are supported. The policy covers To, CC and BCC.

Global Email view/send permissions and per-account grants are both required. Sending also requires `approvals.review`. Administration uses the connected-mailbox model permissions and account administration grants. Superusers retain administrative access. HTTP mailbox endpoints accept authenticated, CSRF-protected sessions; API OAuth-token access is not enabled. AI tools call the same authorized services using the authenticated principal.

## Connect and verify

1. Configure the key ring, Message-ID domain, scanning service and pilot recipient restrictions. Enable the feature with sending globally paused while configuring accounts.
2. Open **Admin → Email → Agent mailboxes** and add an account. SMTP requires explicit SMTP and IMAP endpoints and credentials. Ports support mandatory STARTTLS or implicit TLS for SMTP, and implicit TLS for IMAP. Certificate validation cannot be disabled. DNS addresses are checked and pinned before connecting; metadata/link-local endpoints remain blocked.
3. For Graph or Google, register an OAuth application, enter the client configuration, and start consent. Open the returned authorization URL, then paste the resulting callback URL into the connection form. State is short-lived, one-use, bound to the initiating administrator and account version, and protected with PKCE. Graph application credentials are configured through the account API with `options.auth = "application"`; restrict the application's mailbox access in the tenant.

   When the shared Microsoft client ID, secret, redirect URI and encryption keys are configured, select **Microsoft Graph → Use AIMMS Microsoft connection**. Enter the mailbox name and email address, save it, and select **Connect with OAuth → Open provider consent**. No application secret is entered in the browser. The shared application uses the `common` Microsoft authority with delegated permissions, supporting work and personal accounts when the app registration permits them. Its secret stays in deployment configuration; only account tokens are stored encrypted in the mailbox record. Existing custom registrations remain available by clearing the shared-connection checkbox. Changing the shared client ID requires reconnecting existing shared mailboxes; secret rotation under the same client ID does not.

4. Grant read/draft/send/admin capabilities to the intended users or groups. Configure aliases, signature, allowlist and retention through the account PATCH API. User grant controls are available in the panel; group grants are also supported by the API.
5. Enable the account and receiving, then request a receive test. A completed authorized collection sync verifies the read connection. It does **not** prove end-to-end delivery of a canary reply.
6. Once live test recipients and permission are recorded, unpause global sending and create an administrator verification message to the allowlisted recipient. Review its entire content and approve it. This uses the normal durable dispatch ledger. Successful submission verifies the send connection; it does not assert recipient delivery.
7. Confirm actual canary arrival, send a real reply and verify ingestion, threading and attachments. Enable normal per-account sending only after both connection checks and the pilot acceptance gate pass.

Changing provider, address, aliases, options or credentials invalidates connection verification and increments the binding version. Previously reviewed drafts cannot dispatch against the new connection. Reconnect with the correct account, verify it, and revise/review affected drafts. Do not rebind an unresolved legacy execution to a new provider.

## Dispatch and recovery

An approval persists `pending_dispatch` before queue publication. Periodic publication recovers a broker outage. A worker atomically changes that state to `submitting` and rechecks the current requester, reviewer, account permissions, connection version, recipient policy, review hash, attachment content and source access before contacting the provider.

Only the claiming worker may submit. Retries of a submitting, partial, unknown or succeeded operation never send again. A lost response or receipt-storage failure must not be treated as proof that nothing happened. SMTP receipt evidence is based on the final DATA response, including each recipient's status; failure to append a Sent copy does not undo successful submission. Graph 202 and Google 200 are submission evidence, even without returned IDs. A local receipt identifies the observation, not recipient delivery.

The real adapters currently declare no authoritative reconciliation capability: Sent-copy existence alone is insufficient to prove acceptance of the complete protected BCC envelope. Unknown outcomes remain unknown for operator investigation. The recording adapter supports deterministic reconciliation scenarios. Legacy Gmail recovery remains available for original unresolved legacy operations.

## Receive, attachments and history

Inbox and Sent collections have independent cursors and leases. Content and cursor progress commit in one transaction. Graph uses immutable message IDs and folder delta links. Google starts full sync from a captured history ID and handles history expiry as an explicit coverage gap. IMAP identities include folder, UIDVALIDITY and UID; bounded snapshots refresh flags and record removed membership. IMAP uses read-only SELECT and BODY.PEEK, with at most 20 entries per page and 10,000 UIDs per scope. Oversized scopes stop with an explicit error rather than silently claiming complete history.

Polling is every two minutes. An incomplete page continues on a subsequent poll. Backfill defaults to 30 days, bounded by the configured recovery window. A gap remains visible after resync; completion of a new snapshot does not prove missing historical events were recovered. Provider read flags never authorize business processing. The processed flag is local, and received content never executes tools or initiates replies. Durable downstream business-workflow intents and their publication are not implemented; polling currently captures correspondence only.

Message size is limited to 20 MiB; Graph submission currently uses a conservative 2 MiB frozen-MIME cap and does not support upload sessions for larger attachments. MIME traversal is limited to 100 parts and attachments to 20 per message. Attachments remain private and quarantined until ClamAV reports clean. Scanner failure does not release content; a periodic rescan handles recovery. Downloads require fresh account access, are served as opaque attachments with `nosniff`, and never return attachment bytes to the model. HTML is reduced to inert text without remote loads.

Normal received content, copied conversation subjects and unprotected attachment filenames expire after the account retention period; minimal identity survives for deduplication. Unattached uploads expire too. Frozen approval artifacts and execution evidence are protected audit data and are **not yet automatically purged**; define and implement their audit-retention policy before enabling deployments with a finite mandatory audit-retention requirement. Unknown operations need their original evidence for investigation.

## Operate, pause and restore

Run `python manage.py agent_email_audit` for counts of connections, operation states, sync gaps and legacy recovery dependencies. It prints no credential values or message bodies. Monitor pending dispatch age, stale submitting/unknown/partial operations, sync lag and gaps, quarantine backlog and token health. Treat any unknown or partial result as a reason to pause expansion and investigate; agree pilot-specific limits and observation duration before enabling normal sends.

For a global stop, set `INVENTREE_AGENT_EMAIL_SEND_PAUSED=true` and restart/drain all dispatch workers. This also blocks legacy direct sends. Already submitted operations may still complete; inspect their existing receipts. Per-account pause stops new work for that account. Disconnect erases its stored credentials and increments its binding version while keeping history and receipts.

For key rotation, prepend the new key, retain old keys, restart all mailbox workers with the updated key ring, then run `python manage.py agent_email_rotate_keys`. This command re-encrypts stored credentials without exposing them. Remove an old key only after stored records and retained backups no longer need it.

Restore with global sending paused and the feature disabled. Restore the database and the required key ring, run the audit, inspect pending and in-flight records, and resolve the original provider/account identity before unpausing. Never reset `submitting`, `partial` or `unknown` to pending. Keep the new global pause set when rolling back feature enablement so the legacy path cannot resume sending inadvertently. Notification email configuration remains independent.

## Implementation references

- Provider contracts and adapters: `ai/core/integrations/email/`.
- Mailbox persistence and services: `aichat/email_models.py`, `aichat/services/email/`.
- HTTP API: `/api/aichat/email/`, defined in `aichat/email_api.py`.
- Approval authority and receipt integration: `approvals/services.py`, `approvals/execution.py`, `approvals/review_evidence.py`.
- UI: `MailboxPanel.tsx`, `MailboxApprovalReview.tsx`.
- Acceptance fixtures: `aichat/tests/test_mailbox_*.py`, `test_email_contracts.py`, and `tests/pages/pui_agent_email.spec.ts`.

Provider behavior was checked against the official [Graph sendMail reference](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0), [Graph delta guidance](https://learn.microsoft.com/en-us/graph/delta-query-overview), and [Gmail synchronization guide](https://developers.google.com/workspace/gmail/api/guides/sync). Live acceptance is still required for each advertised account type.
