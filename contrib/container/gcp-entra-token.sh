#!/bin/sh
# Executable credential source for GCP Workload Identity Federation on Azure
# Container Apps (attachment-RAG R3, spec decision #4: keyless in cloud).
#
# ACA has no IMDS (169.254.169.254), so the stock `--azure` credential config
# cannot work: managed-identity tokens come from the per-replica
# $IDENTITY_ENDPOINT with the $IDENTITY_HEADER secret. google-auth invokes
# this executable (GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES=1) and reads one
# JSON object on stdout:
#   {"version":1,"success":true,
#    "token_type":"urn:ietf:params:oauth:token-type:jwt",
#    "id_token":"<Entra JWT>","expiration_time":<unix>}
#
# The audience below must match BOTH the Entra app registration's Application
# ID URI and the WIF provider's --allowed-audiences. Override via
# GCP_WIF_AUDIENCE. No secret material lives in this file.
set -eu

AUDIENCE="${GCP_WIF_AUDIENCE:-api://aimms-media-rag}"

if [ -z "${IDENTITY_ENDPOINT:-}" ] || [ -z "${IDENTITY_HEADER:-}" ]; then
    printf '{"version":1,"success":false,"code":"401","message":"IDENTITY_ENDPOINT/IDENTITY_HEADER not set (not running on Azure Container Apps?)"}\n'
    exit 0
fi

RESPONSE=$(curl -sf -H "x-identity-header: ${IDENTITY_HEADER}" \
    "${IDENTITY_ENDPOINT}?resource=${AUDIENCE}&api-version=2019-08-01") || {
    printf '{"version":1,"success":false,"code":"401","message":"managed identity token request failed"}\n'
    exit 0
}

printf '%s' "$RESPONSE" | python3 -c '
import json
import sys
import time

try:
    payload = json.load(sys.stdin)
    token = payload["access_token"]
    expires_on = int(payload.get("expires_on") or (time.time() + 300))
except Exception:
    print(json.dumps({
        "version": 1,
        "success": False,
        "code": "401",
        "message": "managed identity response was not a token",
    }))
    raise SystemExit(0)
print(json.dumps({
    "version": 1,
    "success": True,
    "token_type": "urn:ietf:params:oauth:token-type:jwt",
    "id_token": token,
    "expiration_time": expires_on,
}))
'
