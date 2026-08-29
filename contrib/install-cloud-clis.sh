#!/usr/bin/env bash

set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
    echo "Unable to determine the operating system: /etc/os-release is missing." >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release

if [[ "${ID:-}" != "debian" && "${ID:-}" != "ubuntu" ]]; then
    echo "This installer supports Debian and Ubuntu only; detected ${PRETTY_NAME:-unknown OS}." >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "This installer requires apt-get." >&2
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
else
    if ! command -v sudo >/dev/null 2>&1; then
        echo "Run this script as root or install sudo first." >&2
        exit 1
    fi

    SUDO=(sudo)
fi

missing_packages=()

if ! command -v az >/dev/null 2>&1; then
    missing_packages+=(azure-cli)
fi

if ! command -v gcloud >/dev/null 2>&1; then
    missing_packages+=(google-cloud-cli)
fi

if [[ "${#missing_packages[@]}" -gt 0 ]]; then
    if [[ "${EUID}" -ne 0 ]]; then
        sudo -v
    fi

    "${SUDO[@]}" apt-get install --yes ca-certificates curl gnupg

    if [[ " ${missing_packages[*]} " == *" azure-cli "* ]]; then
        "${SUDO[@]}" install -d -m 0755 /etc/apt/keyrings
        curl -fsSL https://packages.microsoft.com/keys/microsoft.asc |
            gpg --dearmor |
            "${SUDO[@]}" tee /etc/apt/keyrings/microsoft.gpg >/dev/null
        "${SUDO[@]}" chmod a+r /etc/apt/keyrings/microsoft.gpg

        azure_codename="${VERSION_CODENAME:-}"
        if [[ -z "${azure_codename}" ]]; then
            echo "Unable to determine the Debian/Ubuntu release codename." >&2
            exit 1
        fi

        if ! curl --fail --silent --location --output /dev/null \
            "https://packages.microsoft.com/repos/azure-cli/dists/${azure_codename}/Release"; then
            if [[ "${ID}" == "debian" ]]; then
                azure_codename="bookworm"
                echo "Azure CLI has no ${VERSION_CODENAME} repository; using bookworm."
            else
                echo "Azure CLI has no repository for ${azure_codename}." >&2
                exit 1
            fi
        fi

        cat <<EOF | "${SUDO[@]}" tee /etc/apt/sources.list.d/azure-cli.sources >/dev/null
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: ${azure_codename}
Components: main
Architectures: $(dpkg --print-architecture)
Signed-by: /etc/apt/keyrings/microsoft.gpg
EOF
    fi

    if [[ " ${missing_packages[*]} " == *" google-cloud-cli "* ]]; then
        curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg |
            "${SUDO[@]}" gpg --dearmor --yes -o /usr/share/keyrings/cloud.google.gpg
        echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" |
            "${SUDO[@]}" tee /etc/apt/sources.list.d/google-cloud-sdk.list >/dev/null
    fi

    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" apt-get install --yes "${missing_packages[@]}"
fi

echo "Azure CLI:"
az version
echo "Google Cloud CLI:"
gcloud version
