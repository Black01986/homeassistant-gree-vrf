# GREE GMV / VRF for Home Assistant

Experimental support for GREE GMV/VRF systems with multiple indoor units connected through a shared GREE gateway/controller.

This project extends existing GREE Home Assistant and Python implementations to support the parent/child architecture used by some GREE VRF systems.

## Project status

The implementation has been tested on a real GREE GMV VRF installation with five indoor units connected through a single **GREE MC31-00/F Wi-Fi/cloud controller**.

Other GREE GMV/VRF controllers may also work, but have not yet been verified.

Confirmed working:

- Multiple indoor units exposed as separate Home Assistant climate entities
- Power ON/OFF
- Target temperature
- HVAC mode
- Fan speed
- Horizontal swing
- Vertical full swing
- Cloud/MQTT status updates

This is currently experimental and needs testing on additional GREE GMV/VRF systems.

## Credits and upstream projects

This project would not exist without the work of the following projects and contributors:

### HomeAssistant-GreeClimateComponent

https://github.com/RobHofmann/HomeAssistant-GreeClimateComponent

The Home Assistant custom integration that provides the main integration structure and GREE climate implementation.

### Cloud Support – PR #499

https://github.com/RobHofmann/HomeAssistant-GreeClimateComponent/pull/499

The Cloud Support work provides the architecture used for GREE Cloud devices, including cloud discovery, MQTT transport and the separation between local UDP and cloud/MQTT communication.

The VRF work in this repository builds on top of that implementation.

### davo22/greeclimate

https://github.com/davo22/greeclimate

This implementation was used as an important reference for understanding and validating GREE Cloud/MQTT communication, device discovery, encryption/binding behavior and command routing.

## VRF-specific work

Testing showed that GREE VRF systems may use a parent/child device architecture.

Several indoor units can share one gateway/controller while each indoor unit has its own device identifier.

For the tested system, correct MQTT command routing requires:

```text
MQTT topic  -> request/<controller MAC>
outer tcid  -> indoor-unit/child MAC
pack.sub    -> indoor-unit/child MAC
cp /homeassistant/gree-vrf-github/custom_components/gree_custom/LICENSE \
   /homeassistant/gree-vrf-github/LICENSE
