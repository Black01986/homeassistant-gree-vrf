# GREE GMV / VRF

Custom Home Assistant integration for **GREE GMV / VRF air-conditioning systems**.

This integration adds support for GREE GMV/VRF systems using the GREE cloud/MQTT communication path and exposes individual indoor units as climate entities in Home Assistant.

> This is a custom integration and is not part of Home Assistant Core.

---

## Features

- GREE GMV / VRF support
- Multiple indoor units connected through a single GREE gateway
- Individual climate entities for each indoor unit
- Power control
- HVAC mode control
- Target temperature
- Current room temperature
- Fan speed control
- Vertical swing control
- Horizontal swing control where supported by the indoor unit
- GREE Cloud / MQTT communication
- Home Assistant Config Flow setup
- HACS installation

---

## Installation with HACS

This integration can be installed through HACS as a **Custom Repository**.

### 1. Add the repository to HACS

In Home Assistant, open:

**HACS → Integrations**

Open the menu in the upper-right corner and select:

**Custom repositories**

Enter the repository URL:

`https://github.com/Black01986/homeassistant-gree-vrf`

For **Type**, select:

**Integration**

Click **Add**.

### 2. Install the integration

In HACS, search for:

**GREE GMV / VRF**

Open the integration and click **Download**.

When the installation is complete, **restart Home Assistant**.

### 3. Configure GREE GMV / VRF

After Home Assistant has restarted, go to:

**Settings → Devices & services → Add integration**

Search for:

**GREE GMV / VRF**

Follow the configuration flow and enter the required GREE account information.

The integration will discover supported GREE devices associated with the account.

---

## Updating

Updates can be installed directly through HACS when a newer version is available.

Restart Home Assistant after updating the integration.

---

## Manual installation

If you do not use HACS:

1. Download this repository.
2. Copy the directory:

   `custom_components/gree_custom`

   to:

   `/config/custom_components/gree_custom`

3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**.
5. Search for **GREE GMV / VRF**.

---

## Supported architecture

The integration supports GREE GMV / VRF installations where multiple indoor units are exposed through a GREE gateway.

A single gateway may provide access to multiple individual indoor units. Each supported indoor unit can be represented separately in Home Assistant.

---

## Current release - v0.1.5

### Fixes

- Fixed the Config Flow import failure:

  `Config flow could not be loaded: {"message":"Invalid handler specified"}`

- Fixed the invalid indentation block in `cloud_api.py`.
- Includes Python exception-handling compatibility fixes from previous test builds.

### VRF support

- Preserves the verified GREE GMV / VRF cloud and MQTT communication logic.
- Supports routing commands to individual indoor units behind a common gateway.
- Supports receiving status updates for individual VRF indoor units.
- Includes VRF temperature/status handling.
- Includes compatibility handling for VRF swing values.

---

## Repository layout

The Home Assistant integration is located at:

`custom_components/gree_custom/`

The Home Assistant integration domain remains:

`gree_custom`

---

## Troubleshooting

If the integration does not appear after installation:

1. Restart Home Assistant.
2. Verify that this directory exists:

   `/config/custom_components/gree_custom`

3. Check **Settings → System → Logs** for messages containing:

   `gree_custom`

When reporting a problem, please include:

- Home Assistant version
- GREE GMV / VRF integration version
- GREE indoor-unit/system model, if known
- Relevant Home Assistant log entries

Please remove passwords, account credentials, device keys, tokens and other sensitive information before posting logs.

---

## Issues

Bug reports and compatibility reports are welcome through GitHub Issues.

When reporting support for a new GREE GMV / VRF system, please include as much information as possible about the gateway and indoor-unit models.

---

## Disclaimer

This project is an unofficial Home Assistant integration and is not affiliated with or endorsed by GREE Electric Appliances Inc.

Use this integration at your own risk.
