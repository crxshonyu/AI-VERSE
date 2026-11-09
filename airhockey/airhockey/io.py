"""Optional GPIO + non-blocking operator input. GPIO callbacks only enqueue."""

from __future__ import annotations

import queue
import sys
import threading
import time


class OperatorInput:
    def __init__(self, enabled=True):
        self.commands = queue.SimpleQueue()
        if enabled and sys.stdin and sys.stdin.isatty():
            threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in sys.stdin:
            self.commands.put(line.strip().lower())

    def poll(self):
        result = []
        while not self.commands.empty():
            result.append(self.commands.get())
        return result


class GPIOInterface:
    def __init__(self, config, goals):
        self.config, self.goals = config, goals
        self.events = queue.SimpleQueue()
        self.devices = {}
        self.led = None
        if not config["enabled"]:
            return
        try:
            from gpiozero import RGBLED, DigitalInputDevice
        except ImportError as exc:
            raise RuntimeError("Install python3-gpiozero (Raspberry Pi OS apt)") from exc
        try:
            for name in ("human_goal", "robot_goal"):
                device = DigitalInputDevice(
                    config[name + "_pin"], pull_up=bool(config["pull_up"]), bounce_time=None
                )
                self.devices[name] = device
                # Defaulted args capture each loop's name without shared variables.
                device.when_activated = lambda name=name: self.events.put(
                    (name, True, time.monotonic())
                )
                device.when_deactivated = lambda name=name: self.events.put(
                    (name, False, time.monotonic())
                )
                self.goals.initial(name, device.is_active, time.monotonic())
            if config["rgb_pins"]:
                self.led = RGBLED(
                    *config["rgb_pins"],
                    active_high=bool(config["rgb_active_high"]),
                    pwm=False,
                    initial_value=(0, 0, 0),
                )
        except Exception:
            self.close()
            raise

    def poll(self, now_s):
        while not self.events.empty():
            name, active, stamp = self.events.get()
            self.goals.edge(name, active, stamp)
        return self.goals.poll(now_s)

    def show(self, color):
        if self.led:
            self.led.value = color

    def close(self):
        for device in self.devices.values():
            device.when_activated = None
            device.when_deactivated = None
            device.close()
        self.devices.clear()
        if self.led:
            self.led.off()
            self.led.close()
            self.led = None
