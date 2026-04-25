# Netlea Aquarium for Home Assistant

Home Assistant custom integration for Netlea aquarium lights.

## Features

- SMS-code login through the Netlea V3 cloud API.
- Discovers cloud device groups, including shared device groups.
- Creates one `light` entity for the aquarium light group.
- `light.turn_on` sends the verified temporary-on command.
- `light.turn_off` sends the verified temporary-off command.
- Adds action buttons for temporary on, temporary off, and resume schedule.
- Adds sensors for run mode, current trip, reply count, online count, and last command.
- Adds binary sensors for schedule existence and schedule-running state.

## Install

Copy `custom_components/netlea_aquarium` into your Home Assistant `custom_components` directory and restart Home Assistant.

For HACS custom repository use, add this repository as an integration repository.

## Configuration

1. In Home Assistant, go to Settings -> Devices & services -> Add integration.
2. Search for `Netlea Aquarium` / `尼特利鱼缸灯`.
3. Enter your Netlea account phone number.
4. Enter the SMS code.
5. Select the device group to add.

## Control semantics

This integration intentionally treats the aquarium light as a group device:

- `turn_on` always means temporary viewing light on.
- `turn_off` always means temporary light off.
- Resume schedule is exposed as a separate button.

This is important for 24-hour schedules where the night schedule may already be moonlight mode, but `turn_on` should still temporarily brighten the tank.

## Notes

Netlea may rate-limit SMS verification requests. If the cloud returns a daily rate-limit error, wait for the limit to reset or reauthenticate later.
