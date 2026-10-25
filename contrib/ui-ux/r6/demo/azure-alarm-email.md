# Azure alarm-email configuration

## Status — 25 October 2026

The requested sender is
`DoNotReply@90084ec8-1d0c-432a-bcf4-44548ba22f68.azurecomm.net`.
The companion `azure-alarm-email.env.example` prepares the native configuration;
it is not applied to a running service by its presence in this directory.
The current local backend still uses Mailpit on1025 with its existing local
sender. Its SMTP username and password are absent. External Azure authentication
and delivery are therefore not verified or enabled by this change.

## Native configuration, not a second mail implementation

Use `INVENTREE_EMAIL_SENDER` for the default system-mail sender and the existing
SMTP backend for transport. This preserves InvenTree's logging and notification
pipeline; it is not an alarm-specific recipient or sender override.
Alarm, ended-test and verified-recovery notifications inherit the configured
sender; it is not hardcoded into the notification services. Recipients remain
the authorized machine/client-scoped users and their notification preferences,
not the no-reply address. [InvenTree email settings](https://docs.inventree.org/en/stable/start/config/#email-settings).

Azure SMTP uses `smtp.azurecomm.net`, port587, STARTTLS enabled and implicit SSL
disabled. Its SMTP username comes from the Communication Services resource and
its password is a Microsoft Entra application client-secret value. Neither can
be inferred from the sender domain. The domain must be provisioned and linked to
the Communication Services resource. Do not substitute the client-secret ID,
an account password or an Azure connection string. [Microsoft SMTP setup](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/send-email-smtp/smtp-authentication).

## Activate only after credentials and recipients are reviewed

1. Add the example's non-secret settings and the actual SMTP credentials to the
   deployment's secret environment, preserving other existing settings. For the
   normal development Compose stack, its existing ignored
   `contrib/container/docker.dev.secrets.env` is loaded by both server and worker.
   Do not commit credentials or paste them into chat. Blank example credentials
   are intentionally unusable; do not activate them as a complete configuration.
2. Apply the same configuration to backend and worker. Environment values take
   precedence over `config.yaml`; a restart alone does not replace an existing
   container's environment. The standalone localhost viewer is not managed by
   that development Compose file, so editing it does not reconfigure the viewer.
3. Verify the effective sender, SMTP host/port, TLS/SSL and credential-presence
   booleans without printing the password. No database migration is required.
4. Review queued tasks before starting any general worker. Queued emails may
   already contain their old sender and recipients; do not drain an unrelated
   backlog or rewrite queued messages to send them externally.
5. Send one explicit verification message to an owner-approved inbox. Check the
   actual provider result and received From header. Local Mailpit capture, a
   successful TLS handshake or a queued task is not proof of external delivery.
6. Verify one explicitly authorized alarm and its verified closeout-recovery
   notification through the same backend. Ending a simulation is not verified
   repair completion. Preserve independent approval and all native safety gates.

No Azure resource/deployment change, worker start, new live alarm, recipient
expansion, external message, commit or push is authorized or performed here.

## Verification

Native configured-sender regression assertions pass in both existing focused
tests: critical-alarm mail and synthetic trigger/verified-recovery mail. The
isolated SQLite run reports2 tests in5.133s, OK; configured hooks and the exact
profile/blank-credential contract pass. These are local captures, not Azure mail.

An anonymous connection to `smtp.azurecomm.net:587` successfully negotiates
certificate-verified TLS1.3. No SMTP authentication, MAIL/RCPT command or message
is sent. This verifies network/TLS reachability only; real credentials, approved
recipient and actual provider delivery remain outstanding. Localhost's active
Mailpit configuration, worker queue, alarms, work orders and Azure are unchanged.
