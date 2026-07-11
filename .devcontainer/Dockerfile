# Dockerfile for the InvenTree devcontainer

# In contrast with the "production" image (which is based on an Alpine image)
# we use a Debian-based image for the devcontainer

FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm@sha256:5140e54af7a0399a4932dd4c4653d085fcf451b093d7424867df1828ffbb9b81

# InvenTree paths
ENV INVENTREE_HOME="/home/inventree"
ENV INVENTREE_DATA_DIR="${INVENTREE_HOME}/dev"
ENV INVENTREE_STATIC_ROOT="${INVENTREE_DATA_DIR}/static"
ENV INVENTREE_MEDIA_ROOT="${INVENTREE_DATA_DIR}/media"
ENV INVENTREE_BACKUP_DIR="${INVENTREE_DATA_DIR}/backup"
ENV INVENTREE_PLUGIN_DIR="${INVENTREE_DATA_DIR}/plugins"
ENV INVENTREE_CONFIG_FILE="${INVENTREE_DATA_DIR}/config.yaml"
ENV INVENTREE_SECRET_KEY_FILE="${INVENTREE_DATA_DIR}/secret_key.txt"
ENV INVENTREE_OIDC_PRIVATE_KEY_FILE="${INVENTREE_DATA_DIR}/oidc.pem"

# Required for running playwright within devcontainer
ENV DISPLAY=:0
ENV LIBGL_ALWAYS_INDIRECT=1

COPY contrib/container/init.sh ./
RUN chmod +x init.sh

# Remove yarn apt source to avoid GPG key errors
RUN rm -f /etc/apt/sources.list.d/yarn.list

# Install required base packages
RUN apt-get update && apt-get install -y \
   python3.11-dev python3.11-venv \
   postgresql-client \
   libldap2-dev libsasl2-dev \
   libpango1.0-0 libcairo2 \
   poppler-utils weasyprint

# Install packages required for frontend development
RUN apt-get install -y \
   nodejs npm

# Update to the latest stable node version
RUN npm install -g n --ignore-scripts && n lts

ENTRYPOINT ["/bin/bash", "./init.sh"]
