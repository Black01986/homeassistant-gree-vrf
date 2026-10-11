# Testing a Tadiran / Gree commercial unit (cloud gateway)

This guide is for units such as the Tadiran *Slim Inverter SQ-240/1P*, which show up in the
Gree app as a gateway (for example `Commercial AC`) with one indoor unit behind it
(for example `Commercial ACf700`). Home Assistant reaches them only through the Gree
cloud (MQTT). The gateway MAC is the 12-character one, the indoor unit MAC is the same
plus two characters (for example `…00`).

## How these units behave

Seen on a Tadiran SQ-240/1P, and handled by the integration:

- **Status answers can be incomplete.** A request for several properties can come back with
  fewer values than names (`cols=10 dat=9`). The values can't be matched to names safely,
  so the request is split and retried. Properties that still have no value are treated as
  unsupported.
- **Big status requests get no answer at all.** On a timeout, the request is split and the
  size that worked is remembered for later polls. The first setup after a restart can take
  10–30 s because of this.
- **Every answer is sent twice.** MQTT answers are matched to the request they belong to, so
  a late copy is not taken as the answer to the next request.
- **The unit pushes its full state every few seconds** and after every change. HA applies
  these pushes, so changes from the Gree app or the wall remote show up without polling.
- **Device information is partly missing** (for example `MidType`). It is optional and
  doesn't block setup.

After a command, HA reads the state back and polls a few more times if the gateway still
reports old values. It only shows what the unit reports.

## Install

Install through HACS as usual, then restart Home Assistant.

The domain stays `gree_custom`, so existing config entries keep working.

## Turn on debug logging (temporary)

Add this to `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.gree_custom: debug
```

Or go to **Settings → Devices & services → GREE GMV / VRF → ⋮ → Enable debug logging**.
When you turn it off again, HA offers the log as a download.

The debug logs leave out the cloud token, cloud password and device keys. They still contain
MAC addresses, device names and the Gree account e-mail. Remove those before sharing a log
in public.

## What to collect

1. **Setup log.** Add the device, then:

   ```bash
   ha core logs | grep -E "gree_custom" | tail -n 300
   ```

   Lines to look for:
   - `returned an ambiguous response (Malformed response: cols=… dat=…) … Querying them one by one`.
     This is expected for this unit.
   - `Malformed status response: {...}`. Shows the requested props, returned `cols`, `dat`,
     `requested_not_returned`, and the `mac`/`sub` fields.
   - `Single prop queries for '…': returned=[…] missing=[…]`.
   - `Device info is unavailable, continuing without it`, if it appears.

2. **Diagnostics file.** Go to **Settings → Devices & services → GREE GMV / VRF →** the indoor
   unit → **Download diagnostics**. It includes `polled_properties`, the raw `state`, and
   `unconfirmed` (values sent but not yet reported back).

3. **Raw property values.** Under **Developer tools → Actions**, run:

   ```yaml
   action: gree_custom.get_prop_values
   data:
     device_id: <the indoor unit device>
     prop_list: [Pow, Mod, SetTem, TemRec, WdSpd, SwUpDn, SwingLfRig, TemUn, InTem, InTemEn, TemSen, SetDeciTem]
   ```

   This queries one property at a time. To dump everything, use `gree_custom.get_prop_values_all`.
   It's slow, about one request per property.

4. **Pushed updates.** With debug logging on, change something in the Gree app and look for
   `Pushed status on status/…`. A line saying `Ignoring pushed status for '…'` means the
   unit's pushes carry an id that doesn't match the indoor unit. Please send that line.

## Acceptance checklist

| # | Test | How | Pass when |
|---|------|-----|-----------|
| 1 | Setup | Add the indoor unit (cloud only, *Prefer cloud* on, local fields empty) | Setup finishes with no "Unknown error" |
| 2 | Real state | Compare the climate card with the Gree app | Power, mode, setpoint, fan and room temperature match |
| 3 | On/off from HA | Toggle in HA | Unit switches, the app shows it, and HA still shows it after about 10 s |
| 4 | Setpoint / mode from HA | Change both in HA | The app shows the new values. If it doesn't, HA must go back to the reported value within about 10 s |
| 5 | App → HA | Change power/mode/setpoint in the Gree app | HA updates without touching HA (push, or within one scan interval) |
| 6 | Wall controller → HA | Change on the unit's own controller | Same as 5 |
| 7 | Restart | Change something in the app, then restart HA | After the restart HA shows the unit's current state. Turn off *Restore Entities* first on existing entries |
| 8 | Cloud failure | Block internet for HA for a few minutes | Entities become *unavailable* (not stale). After unblocking they recover without a restart |

For 7, turn off **Restore Entities** in the integration options of an existing entry. Older
entries keep the old default (on).

## Known limits

- Only properties the unit actually reports are offered. Swing, turbo, quiet and so on show up
  only if the unit returns a value for them.
- Half-degree setpoints are shown when the unit reports them (`SetDeciTem`), but HA only
  sends whole degrees. It's not known yet which value the unit expects for a half degree.
- How the gateway caches state is inferred from upstream VRF reports. It's handled by polling
  again, never by showing a value the unit hasn't reported.
