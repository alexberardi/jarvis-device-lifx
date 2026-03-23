# jarvis-device-lifx

LIFX smart light protocol adapter with zero-cloud LAN control for [Jarvis](https://github.com/alexberardi/jarvis-node-setup).

## Install

```bash
python scripts/command_store.py install --url https://github.com/alexberardi/jarvis-device-lifx
```

## Supported Devices

- LIFX smart bulbs (white and color)
- LIFX light strips
- LIFX beam
- LIFX candle

## Secrets

No secrets required — works over LAN.

## Structure

```
device_families/lifx/protocol.py   # Device protocol adapter
```
