ARG ODOO_BASE_IMAGE=ghcr.io/adomi-io/odoo-community-base:latest

FROM ${ODOO_BASE_IMAGE} AS configuration_layer

USER root

# The platform-management addon talks to the Kubernetes API and signs GitHub App
# JWTs (PyJWT needs cryptography for RS256).
RUN pip install kubernetes "pyjwt[crypto]"

FROM configuration_layer

USER ubuntu
WORKDIR /volumes

COPY config/odoo.conf /volumes/config/odoo.conf
COPY extra_addons /volumes/extra_addons
COPY addons /volumes/addons
