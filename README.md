# GREE GMV / VRF

Custom Home Assistant integration for GREE GMV / VRF systems.

## Installation with HACS

Add this repository as a custom integration repository in HACS, install **GREE GMV / VRF**, restart Home Assistant, and then add the integration from **Settings → Devices & services → Add integration**.

## v0.1.5

- Fixes the config-flow import failure (`Invalid handler specified`) caused by an invalid indentation block in `cloud_api.py`.
- Preserves the verified working VRF/MQTT logic.
- Includes the Python exception-handling compatibility fixes from the previous test builds.

## Repository layout

The integration is located at:

`custom_components/gree_custom/`

The Home Assistant integration domain remains `gree_custom`.

## Project status

This is a custom integration and is not part of Home Assistant Core.
