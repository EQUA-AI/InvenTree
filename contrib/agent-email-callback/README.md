# AIMMS Agent Email callback landing page

Dedicated static landing page for the existing manual OAuth callback flow.
Register `https://aimms-auth.equa.work/microsoft/callback` as a **Web** redirect
URI on the shared Microsoft application. The initiating AIMMS deployment must
use the identical `INVENTREE_AGENT_EMAIL_OAUTH_REDIRECT_URI` value.

This service does not exchange authorization codes, hold credentials, grant
mailbox access or route callbacks between customers. The administrator copies
the callback URL into the initiating AIMMS session, whose backend validates
state, PKCE, account binding and permissions. Automatic customer onboarding
requires additional backend integration.

The container runs nginx as a non-root user on port 8080, with request and error
logs disabled to avoid recording callback query strings. Responses disallow
caching and referrers. There are no external assets, analytics, redirects or
browser storage. Azure ingress/platform logging must also exclude callback
query strings if additional diagnostics are enabled later.

Build only this directory as the image context. Deploy by immutable ACR digest
to the dedicated `aimms-email-auth` Container App. Give its image-pull identity
only `AcrPull` on the registry. It needs no database, mailbox secrets, volumes,
or application environment configuration. `/healthz` is a readiness endpoint.
Keep Cloudflare's CNAME DNS-only for Azure managed certificate issuance and
renewal, and retain the matching `asuid.aimms-auth` TXT ownership record.

This is a separate small service; existing AIMMS web/worker deployment scripts
continue to govern those applications. A deployed landing page does not certify
Microsoft consent, mailbox access, or the complete customer onboarding flow.
