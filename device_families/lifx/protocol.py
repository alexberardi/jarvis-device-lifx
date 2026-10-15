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


def _entity_id(label: str, mac: str, ip: str) -> str:
    """Stable id for a light: slug of its label, else of its MAC, else its IP."""
    return _slugify(label) if label else (_slugify(mac) if mac else ip)


async def _connect(ip: str, mac: str) -> Any:
    """Return a lifxlan Light at ``ip``.

    lifxlan addresses a light by MAC as well as IP. Use ``mac`` when the caller
    has it; otherwise scan the LAN for the light answering at ``ip``.
    """
    from lifxlan import LifxLAN, Light

    if mac:
        return Light(mac, ip)
    lights = await asyncio.to_thread(LifxLAN().get_lights) or []
    for light in lights:
        if light.get_ip_addr() == ip:
            return light
    raise OSError(f"no LIFX light found at {ip}")


class LifxProtocol(IJarvisDeviceProtocol):
    """LIFX LAN protocol adapter."""

    protocol_name: str = "lifx"
    friendly_name: str = "LIFX"
    supported_domains: list[str] = ["light"]
    connection_type: str = "lan"

    @property
    def supported_actions(self) -> list[IJarvisButton]:
        return [
            IJarvisButton("Turn On", "turn_on", "primary", "lightbulb-on"),
            IJarvisButton("Turn Off", "turn_off", "secondary", "lightbulb-off"),
            IJarvisButton("Toggle", "toggle", "secondary", "lightbulb-outline"),
        ]

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredDevice]:
        # lifxlan's get_lights() takes no timeout: its broadcast window is fixed.
        try:
            from lifxlan import LifxLAN
        except ImportError:
            logger.error("lifxlan is not installed. Run: pip install lifxlan")
            return []

        devices: list[DiscoveredDevice] = []
        try:
            lan = LifxLAN()
            lights = await asyncio.to_thread(lan.get_lights)
        except Exception as e:
            logger.error(f"LIFX discovery failed: {e}")
            return []

        if not lights:
            logger.info("LIFX discovery found 0 device(s)")
            return []

        for light in lights:
            try:
                label: str = await asyncio.to_thread(light.get_label) or ""
                mac_addr: str = _mac_to_str(light.get_mac_addr())
                ip_addr: str = light.get_ip_addr() or ""
                product: str = await asyncio.to_thread(light.get_product_name) or "LIFX Light"

                devices.append(
                    DiscoveredDevice(
                        name=label or "LIFX Light",
                        domain="light",
                        manufacturer="LIFX",
                        model=product,
                        protocol=self.protocol_name,
                        entity_id=_entity_id(label, mac_addr, ip_addr),
                        local_ip=ip_addr or None,
                        mac_address=mac_addr or None,
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to read LIFX device: {e}")
                continue

        logger.info(f"LIFX discovery found {len(devices)} device(s)")
        return devices

    async def control(
        self,
        ip: str,
        action: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> DeviceControlResult:
        def fail(error: str, entity_id: str = ip) -> DeviceControlResult:
            return DeviceControlResult(
                success=False, entity_id=entity_id, action=action, error=error
            )

        try:
            import lifxlan  # noqa: F401
        except ImportError:
            return fail("lifxlan is not installed. Run: pip install lifxlan")

        data = data or {}
        if not ip:
            return fail("No IP address for device")

        mac: str = kwargs.get("mac_address") or ""
        try:
            light = await _connect(ip, mac)
            label: str = await asyncio.to_thread(light.get_label) or ""
        except Exception as e:
            return fail(f"Failed to connect to LIFX device at {ip}: {e}")

        entity_id: str = _entity_id(label, _mac_to_str(light.get_mac_addr()), ip)
        ok = DeviceControlResult(success=True, entity_id=entity_id, action=action)

        try:
            if action == "turn_on":
                await asyncio.to_thread(light.set_power, 65535)
                return ok

            elif action == "turn_off":
                await asyncio.to_thread(light.set_power, 0)
                return ok

            elif action == "toggle":
                power = await asyncio.to_thread(light.get_power)
                await asyncio.to_thread(light.set_power, 0 if power > 0 else 65535)
                return ok

            elif action == "set_brightness":
                brightness_pct: int = int(data.get("brightness", 100))
                brightness_pct = max(0, min(100, brightness_pct))
                brightness_val: int = int(brightness_pct / 100.0 * 65535)
                await asyncio.to_thread(light.set_brightness, brightness_val)
                return ok

            elif action == "set_color":
                if "rgb" in data:
                    rgb = data["rgb"]
                    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
                    hue, saturation, brightness = _rgb_to_hsbk_components(r, g, b)
                    kelvin: int = int(data.get("color_temp", 3500))
                    color: list[int] = [hue, saturation, brightness, kelvin]
                    await asyncio.to_thread(light.set_color, color)
                    return ok
                elif "color_temp" in data:
                    kelvin = int(data["color_temp"])
                    current_color = await asyncio.to_thread(light.get_color)
                    color = [current_color[0], 0, current_color[2], kelvin]
                    await asyncio.to_thread(light.set_color, color)
                    return ok
                else:
                    return fail("set_color requires 'rgb' or 'color_temp' param", entity_id)

            else:
                return fail(f"Unsupported action: {action}", entity_id)

        except Exception as e:
            return fail(f"Control failed: {e}", entity_id)

    async def get_state(self, ip: str, **kwargs: Any) -> dict[str, Any] | None:
        try:
            import lifxlan  # noqa: F401
        except ImportError:
            return {"error": "lifxlan is not installed"}

        if not ip:
            return {"error": "No IP address for device"}

        mac: str = kwargs.get("mac_address") or ""
        try:
            light = await _connect(ip, mac)
            power: int = await asyncio.to_thread(light.get_power)
            color: tuple[int, ...] = await asyncio.to_thread(light.get_color)
        except Exception as e:
            return {"error": f"Failed to get state from {ip}: {e}"}

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
