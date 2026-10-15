"""Tests for LifxProtocol against the jarvis-command-sdk device protocol interface.

lifxlan is replaced by a fake ``lifxlan`` module so the tests need no network
and no real lights.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest
from jarvis_command_sdk import DeviceControlResult, DiscoveredDevice, IJarvisButton

from device_families.lifx.protocol import LifxProtocol


class FakeLight:
    def __init__(
        self,
        mac: str = "d0:73:d5:00:00:01",
        ip: str = "192.168.1.20",
        label: str = "Kitchen Bulb",
        power: int = 0,
        color: tuple[int, int, int, int] = (0, 0, 65535, 3500),
        product_name: str = "LIFX A19",
    ) -> None:
        self.mac = mac
        self.ip = ip
        self.label = label
        self.power = power
        self.color = list(color)
        self.product_name = product_name
        self.calls: list[Any] = []

    def get_mac_addr(self) -> str:
        return self.mac

    def get_ip_addr(self) -> str:
        return self.ip

    def get_label(self) -> str:
        return self.label

    def get_product_name(self) -> str:
        return self.product_name

    def get_power(self) -> int:
        return self.power

    def set_power(self, power: int) -> None:
        self.calls.append(("set_power", power))
        self.power = power

    def get_color(self) -> list[int]:
        return list(self.color)

    def set_color(self, color: list[int]) -> None:
        self.calls.append(("set_color", list(color)))
        self.color = list(color)

    def set_brightness(self, brightness: int) -> None:
        self.calls.append(("set_brightness", brightness))
        self.color[2] = brightness


@pytest.fixture
def fake_lifx(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeLight]:
    """Install a fake ``lifxlan`` module; returns the mac -> light map it serves."""
    lights: dict[str, FakeLight] = {}

    class LifxLAN:
        def get_lights(self) -> list[FakeLight]:  # lifxlan's real signature: no args
            return list(lights.values())

    def Light(mac_addr: str, ip_addr: str) -> FakeLight:
        found = lights.get(mac_addr)
        if found is None or found.ip != ip_addr:
            raise OSError(f"no LIFX light {mac_addr} at {ip_addr}")
        return found

    module = types.ModuleType("lifxlan")
    module.LifxLAN = LifxLAN  # type: ignore[attr-defined]
    module.Light = Light  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lifxlan", module)
    return lights


def add(lights: dict[str, FakeLight], light: FakeLight) -> FakeLight:
    lights[light.mac] = light
    return light


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_instantiates_and_builds_supported_actions() -> None:
    protocol = LifxProtocol()
    assert protocol.protocol_name == "lifx"
    assert protocol.supported_domains == ["light"]
    actions = protocol.supported_actions
    assert actions and all(isinstance(a, IJarvisButton) for a in actions)
    assert [a.button_action for a in actions] == ["turn_on", "turn_off", "toggle"]
    for a in actions:
        assert a.button_type in ("primary", "secondary", "destructive")
        assert a.to_dict()["button_text"]


def test_discover_builds_devices(fake_lifx: dict[str, FakeLight]) -> None:
    add(fake_lifx, FakeLight())

    (device,) = run(LifxProtocol().discover(timeout=1))

    assert isinstance(device, DiscoveredDevice)
    assert device.entity_id == "kitchen_bulb"
    assert device.name == "Kitchen Bulb"
    assert device.domain == "light"
    assert device.protocol == "lifx"
    assert device.manufacturer == "LIFX"
    assert device.model == "LIFX A19"
    assert device.local_ip == "192.168.1.20"
    assert device.mac_address == "d0:73:d5:00:00:01"


def test_discover_without_label_uses_mac_slug(fake_lifx: dict[str, FakeLight]) -> None:
    add(fake_lifx, FakeLight(label=""))
    (device,) = run(LifxProtocol().discover())
    assert device.entity_id == "d0_73_d5_00_00_01"
    assert device.name == "LIFX Light"


def test_control_turn_on_and_off_with_mac(fake_lifx: dict[str, FakeLight]) -> None:
    light = add(fake_lifx, FakeLight(power=0))
    protocol = LifxProtocol()

    result = run(protocol.control("192.168.1.20", "turn_on", mac_address=light.mac))
    assert isinstance(result, DeviceControlResult)
    assert result.success and result.error is None
    assert result.action == "turn_on"
    assert result.entity_id == "kitchen_bulb"
    assert light.power == 65535

    result = run(protocol.control("192.168.1.20", "turn_off", mac_address=light.mac))
    assert result.success and light.power == 0


def test_control_without_mac_resolves_by_ip(fake_lifx: dict[str, FakeLight]) -> None:
    add(fake_lifx, FakeLight(mac="d0:73:d5:00:00:02", ip="192.168.1.21", label="Other"))
    light = add(fake_lifx, FakeLight(power=0))

    result = run(LifxProtocol().control("192.168.1.20", "turn_on"))
    assert result.success and result.entity_id == "kitchen_bulb"
    assert light.power == 65535


def test_control_toggle(fake_lifx: dict[str, FakeLight]) -> None:
    light = add(fake_lifx, FakeLight(power=65535))
    result = run(LifxProtocol().control("192.168.1.20", "toggle"))
    assert result.success and result.action == "toggle"
    assert light.power == 0


def test_control_set_brightness_clamps(fake_lifx: dict[str, FakeLight]) -> None:
    light = add(fake_lifx, FakeLight())
    result = run(LifxProtocol().control("192.168.1.20", "set_brightness", {"brightness": 150}))
    assert result.success
    assert ("set_brightness", 65535) in light.calls


def test_control_set_color_rgb_and_temp(fake_lifx: dict[str, FakeLight]) -> None:
    light = add(fake_lifx, FakeLight())
    protocol = LifxProtocol()

    result = run(protocol.control("192.168.1.20", "set_color", {"rgb": [255, 0, 0]}))
    assert result.success
    assert light.calls[-1] == ("set_color", [0, 65535, 65535, 3500])

    result = run(protocol.control("192.168.1.20", "set_color", {"color_temp": 2700}))
    assert result.success
    assert light.calls[-1] == ("set_color", [0, 0, 65535, 2700])

    result = run(protocol.control("192.168.1.20", "set_color", {}))
    assert not result.success and result.error


def test_control_unknown_action(fake_lifx: dict[str, FakeLight]) -> None:
    add(fake_lifx, FakeLight())
    result = run(LifxProtocol().control("192.168.1.20", "explode"))
    assert not result.success
    assert result.action == "explode"
    assert result.error and "explode" in result.error


def test_control_unreachable_device(fake_lifx: dict[str, FakeLight]) -> None:
    result = run(LifxProtocol().control("192.168.1.99", "turn_on"))
    assert not result.success
    assert result.entity_id == "192.168.1.99"
    assert result.error and "192.168.1.99" in result.error


def test_get_state(fake_lifx: dict[str, FakeLight]) -> None:
    add(fake_lifx, FakeLight(power=65535, color=(0, 65535, 65535, 3500)))
    state = run(LifxProtocol().get_state("192.168.1.20"))
    assert state == {
        "state": "on",
        "brightness": 100,
        "hue": 0,
        "saturation": 65535,
        "color_temp": 3500,
        "rgb": [255, 0, 0],
    }


def test_get_state_unreachable(fake_lifx: dict[str, FakeLight]) -> None:
    state = run(LifxProtocol().get_state("192.168.1.99"))
    assert state is not None and "error" in state
