#!/bin/bash

# exit when any command fails
set -e

# Required to suppress some git errors further down the line
if command -v git &> /dev/null; then
    git config --global --add safe.directory /home/***
fi

# Create required directory structure (if it does not already exist)
if [[ ! -d "$INVENTREE_STATIC_ROOT" ]]; then
    echo "Creating directory $INVENTREE_STATIC_ROOT"
    mkdir -p $INVENTREE_STATIC_ROOT
fi

if [[ ! -d "$INVENTREE_MEDIA_ROOT" ]]; then
    echo "Creating directory $INVENTREE_MEDIA_ROOT"
    mkdir -p $INVENTREE_MEDIA_ROOT
fi

if [[ ! -d "$INVENTREE_BACKUP_DIR" ]]; then
    echo "Creating directory $INVENTREE_BACKUP_DIR"
    mkdir -p $INVENTREE_BACKUP_DIR
fi

# Check if "config.yaml" has been copied into the correct location
if test -f "$INVENTREE_CONFIG_FILE"; then
    echo "Loading config file : $INVENTREE_CONFIG_FILE"
else
    echo "Copying config file from $INVENTREE_BACKEND_DIR/InvenTree/config_template.yaml to $INVENTREE_CONFIG_FILE"
    cp $INVENTREE_BACKEND_DIR/InvenTree/config_template.yaml $INVENTREE_CONFIG_FILE
fi

# Setup a python virtual environment
# This should be done on the *mounted* filesystem,
# so that the installed modules persist!
if [[ -n "$INVENTREE_PY_ENV" ]]; then

    if test -d "$INVENTREE_PY_ENV"; then
        # venv already exists
        echo "Using Python virtual environment: ${INVENTREE_PY_ENV}"
        source ${INVENTREE_PY_ENV}/bin/activate
    else
        # Setup a virtual environment (within the provided directory)
        echo "Running first time setup for python environment"
        python3 -m venv ${INVENTREE_PY_ENV} --system-site-packages --upgrade-deps

        # Ensure invoke tool is installed locally
        source ${INVENTREE_PY_ENV}/bin/activate
        python3 -m pip install --ignore-installed --upgrade invoke
    fi

fi

cd ${INVENTREE_HOME}

sync_spa_bundle() {
    local src_dir="${INVENTREE_BACKEND_DIR}/InvenTree/web/static/web"
    local dest_dir="${INVENTREE_STATIC_ROOT}/web"

    if [[ ! -d "${src_dir}" ]]; then
        echo "SPA bundle source directory not found: ${src_dir}"
        return
    fi

    mkdir -p "${dest_dir}"
    # Remove stale compiled artefacts so hashed filenames always match the baked manifest
    rm -rf "${dest_dir}/assets" "${dest_dir}/.vite"
    cp -a "${src_dir}/." "${dest_dir}/"
    echo "Synchronized SPA bundle into ${dest_dir}"
}

# Collect static assets once for production builds so WhiteNoise can serve them
if [[ "${INVENTREE_DEBUG,,}" != "true" ]]; then
    STATIC_SENTINEL="${INVENTREE_STATIC_ROOT}/.static_collected"

    if [[ "${INVENTREE_FORCE_COLLECTSTATIC,,}" == "true" && -f "${STATIC_SENTINEL}" ]]; then
        echo "INVENTREE_FORCE_COLLECTSTATIC is enabled - forcing static asset refresh"
        rm -f "${STATIC_SENTINEL}"
    fi

    if [[ ! -f "${STATIC_SENTINEL}" ]]; then
        echo "Collecting static files into ${INVENTREE_STATIC_ROOT}"
        invoke static --skip-plugins
        touch "${STATIC_SENTINEL}"
    else
        echo "Static files already collected, skipping"
    fi

    sync_spa_bundle
fi

# Launch the CMD *after* the ENTRYPOINT completes
exec "$@"
