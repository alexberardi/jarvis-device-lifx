"""LIFX smart light protocol adapter."""

from __future__ import annotations

import asyncio
import colorsys
import re
from typing import Any

from jarvis_command_sdk import (
    IJarvisDeviceProtocol,
    DiscoveredDevice,
    DeviceControlResult,
    IJarvisButton,
)

try:
    from jarvis_log_client import JarvisLogger
except ImportError:
    import logging

    class JarvisLogger:
        def __init__(self, **kw: Any) -> None:
            self._log = logging.getLogger(kw.get("service", __name__))

        def info(self, msg: str, **kw: Any) -> None:
            self._log.info(msg)

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg)

        def error(self, msg: str, **kw: Any) -> None:
            self._log.error(msg)

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg)


logger = JarvisLogger(service="device.lifx")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _mac_to_str(mac_bytes: Any) -> str:
    if isinstance(mac_bytes, str):
        return mac_bytes
    if isinstance(mac_bytes, (bytes, bytearray)):
        return ":".join(f"{b:02x}" for b in mac_bytes)
    raw: str = str(mac_bytes)
    if len(raw) == 12 and all(c in "0123456789abcdefABCDEF" for c in raw):
        return ":".join(raw[i : i + 2] for i in range(0, 12, 2))
    return raw


def _rgb_to_hsbk_components(r: int, g: int, b: int) -> tuple[int, int, int]:
    h_float, s_float, v_float = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hue: int = int(h_float * 65535)
    saturation: int = int(s_float * 65535)
    brightness: int = int(v_float * 65535)
    return hue, saturation, brightness


def _hsbk_to_rgb(h: int, s: int, b: int) -> tuple[int, int, int]:
    h_float: float = h / 65535.0
    s_float: float = s / 65535.0
    v_float: float = b / 65535.0
    r_float, g_float, b_float = colorsys.hsv_to_rgb(h_float, s_float, v_float)
    return int(r_float * 255), int(g_float * 255), int(b_float * 255)


class LifxProtocol(IJarvisDeviceProtocol):
    """LIFX LAN protocol adapter."""

    protocol_name: str = "lifx"
    friendly_name: str = "LIFX"
    supported_domains: list[str] = ["light"]
    connection_type: str = "lan"

    @property
    def supported_actions(self) -> list[IJarvisButton]:
        return [
            IJarvisButton(
                action="turn_on",
                label="Turn On",
                icon="lightbulb-on",
            ),
            IJarvisButton(
                action="turn_off",
                label="Turn Off",
                icon="lightbulb-off",
            ),
            IJarvisButton(
                action="toggle",
                label="Toggle",
                icon="lightbulb-outline",
            ),
        ]

    async def discover_devices(self, timeout: int = 5) -> list[DiscoveredDevice]:
        try:
            from lifxlan import LifxLAN
        except ImportError:
            logger.error("lifxlan is not installed. Run: pip install lifxlan")
            return []

        devices: list[DiscoveredDevice] = []
        try:
            lan = LifxLAN()
            lights = await asyncio.to_thread(lan.get_lights, timeout=timeout)
        except Exception as e:
            logger.error(f"LIFX discovery failed: {e}")
            return []

        if not lights:
            logger.info("LIFX discovery found 0 device(s)")
            return []

        for light in lights:
            try:
                label: str = light.get_label() or ""
                mac_addr: str = _mac_to_str(light.get_mac_addr())
                ip_addr: str = light.get_ip_addr() or ""
                product: str = str(getattr(light, "product", "")) or "LIFX Light"

                device_id: str = _slugify(label) if label else _slugify(mac_addr)

                devices.append(
                    DiscoveredDevice(
                        id=device_id,
                        name=label or "LIFX Light",
                        domain="light",
                        protocol=self.protocol_name,
                        ip=ip_addr,
                        mac=mac_addr,
                        model=product,
                        manufacturer="LIFX",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to read LIFX device: {e}")
                continue

        logger.info(f"LIFX discovery found {len(devices)} device(s)")
        return devices

    async def control(
        self, device: DiscoveredDevice, action: str, params: dict[str, Any] | None = None
    ) -> DeviceControlResult:
        try:
            from lifxlan import Light
        except ImportError:
            return DeviceControlResult(
                success=False,
                message="lifxlan is not installed. Run: pip install lifxlan",
            )

        params = params or {}
        ip: str = device.ip or ""
        mac: str = device.mac or ""
        if not ip or not mac:
            return DeviceControlResult(success=False, message="No IP or MAC address for device")

        try:
            light = Light(mac, ip)
        except Exception as e:
            return DeviceControlResult(success=False, message=f"Failed to connect to LIFX device: {e}")

        try:
            if action == "turn_on":
                await asyncio.to_thread(light.set_power, 65535)
                return DeviceControlResult(success=True, message=f"{device.name} turned on")

            elif action == "turn_off":
                await asyncio.to_thread(light.set_power, 0)
                return DeviceControlResult(success=True, message=f"{device.name} turned off")

            elif action == "toggle":
                power = await asyncio.to_thread(light.get_power)
                if power > 0:
                    await asyncio.to_thread(light.set_power, 0)
                    new_state = "off"
                else:
                    await asyncio.to_thread(light.set_power, 65535)
                    new_state = "on"
                return DeviceControlResult(success=True, message=f"{device.name} toggled {new_state}")

            elif action == "set_brightness":
                brightness_pct: int = int(params.get("brightness", 100))
                brightness_pct = max(0, min(100, brightness_pct))
                brightness_val: int = int(brightness_pct / 100.0 * 65535)
                await asyncio.to_thread(light.set_brightness, brightness_val)
                return DeviceControlResult(
                    success=True, message=f"{device.name} brightness set to {brightness_pct}%"
                )

            elif action == "set_color":
                if "rgb" in params:
                    rgb = params["rgb"]
                    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
                    hue, saturation, brightness = _rgb_to_hsbk_components(r, g, b)
                    kelvin: int = int(params.get("color_temp", 3500))
                    color: list[int] = [hue, saturation, brightness, kelvin]
                    await asyncio.to_thread(light.set_color, color)
                    return DeviceControlResult(
                        success=True, message=f"{device.name} color set to RGB({r},{g},{b})"
                    )
                elif "color_temp" in params:
                    kelvin = int(params["color_temp"])
                    current_color = await asyncio.to_thread(light.get_color)
                    color = [current_color[0], 0, current_color[2], kelvin]
                    await asyncio.to_thread(light.set_color, color)
                    return DeviceControlResult(
                        success=True, message=f"{device.name} color temp set to {kelvin}K"
                    )
                else:
                    return DeviceControlResult(
                        success=False, message="set_color requires 'rgb' or 'color_temp' param"
                    )

            else:
                return DeviceControlResult(success=False, message=f"Unsupported action: {action}")

        except Exception as e:
            return DeviceControlResult(success=False, message=f"Control failed: {e}")

    async def get_state(self, device: DiscoveredDevice) -> dict[str, Any]:
        try:
            from lifxlan import Light
        except ImportError:
            return {"error": "lifxlan is not installed"}

        ip: str = device.ip or ""
        mac: str = device.mac or ""
        if not ip or not mac:
            return {"error": "No IP or MAC address for device"}

        try:
            light = Light(mac, ip)
            power: int = await asyncio.to_thread(light.get_power)
            color: tuple[int, ...] = await asyncio.to_thread(light.get_color)
        except Exception as e:
            return {"error": f"Failed to get state: {e}"}

        h, s, b, k = color[0], color[1], color[2], color[3]
        brightness_pct: int = int(b / 65535.0 * 100)
        r, g, b_rgb = _hsbk_to_rgb(h, s, b)

        state: dict[str, Any] = {
            "state": "on" if power > 0 else "off",
            "brightness": brightness_pct,
            "hue": h,
            "saturation": s,
            "color_temp": k,
            "rgb": [r, g, b_rgb],
        }

        return state
