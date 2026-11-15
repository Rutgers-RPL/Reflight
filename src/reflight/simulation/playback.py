"""Bind existing SPI sensor instances to their scheduled input samples."""

from random import Random

from reflight.buses import SpiBus
from reflight.config.scenario import ScenarioConfig
from .clock import VirtualClock


def build_simulation(scenario: ScenarioConfig, bus: SpiBus | None) -> VirtualClock:
    end_s, reason = scenario.endpoint
    clock = VirtualClock(scenario.instructions_per_second, end_s, reason)
    rng = Random(scenario.random_seed)
    for spec in sorted(scenario.sensors.values(), key=lambda spec: spec.endpoint):
        endpoint = spec.endpoint
        bus_name, cs = endpoint.split('.', 1)
        if bus is None or bus.name != bus_name or cs not in bus.devices:
            raise ValueError(f"scenario endpoint {endpoint} is not attached to the runtime bus")
        sensor = bus.devices[cs]
        data = scenario.csv_data.get(spec.source)

        def update(time_s, *, sensor=sensor, data=data, spec=spec):
            sensor.update_measurements(data.values_at(time_s) if data else {},
                                       bias=spec.bias, noise=spec.noise, rng=rng)

        clock.schedule(endpoint, sensor.sample_rate_hz, update)
    return clock
