"""NI-DCPower operations for efficiency and load regulation with channel ganging.

Verify device-specific wiring, sensing, trigger routing and safe load sharing before
energizing a DUT. Physical synchronization has not been validated.
"""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from enum import Enum
from typing import NamedTuple

from ni_measurement_plugin_sdk_service.session_management import SessionInformation
from nidcpower import (
    Event,
    MeasurementTypes,
    MeasureWhen,
    OutputFunction,
    Sense,
    Session,
    SourceMode,
    TriggerType,
)


class ModeOfOperation(Enum):
    """Supported measurement actions."""

    Power_On_DUT = 0
    PerformMeasurement = 1
    Power_Off_DUT = 2


class SweepType(Enum):
    """Supported sweep interpolation modes."""

    Linear = 0
    Logarithmic = 1

    def __eq__(self, other: object) -> bool:
        """Compare sweep modes by their enum values."""
        if not isinstance(other, SweepType):
            return NotImplemented
        return self.value == other.value

    def __hash__(self) -> int:
        """Hash by enum value, consistent with equality."""
        return hash(self.value)


def format_power_on_result(voltage_level: float, current_level: float) -> str:
    """Format DUT readings to six significant digits, including small values."""
    return (
        "The DUT is powered ON\n"
        f"Voltage Level: {voltage_level:.6g}\n"
        f"Current Level: {current_level:.6g}"
    )


def generate_sequence(
    sweep_type: SweepType, start: float, stop: float, steps: int, with_end_points: bool = True
) -> list[float]:
    """Generate linear or logarithmic sweep points."""
    res = []
    if start > stop or steps <= 0:
        return res

    if sweep_type == SweepType.Linear:
        if with_end_points:
            if steps - 1 == 0:
                d = 0
            else:
                d = (stop - start) / (steps - 1)
            for i in range(steps):
                res.append((start + (i * d)))
            return res
        else:
            d = (stop - start) / (steps + 1)
            for i in range(steps + 2):
                res.append((start + (i * d)))
            return res[1:-1]
    else:
        if with_end_points:
            r = 10 ** (1 / (steps))
            while True:
                res.append(start * (r ** len(res)))
                if res[-1] >= stop:
                    res[-1] = stop
                    return res
        else:
            r = 10 ** (1 / (steps + 1))
            while True:
                res.append(start * (r ** len(res)))
                if res[-1] >= stop:
                    res[-1] = stop
                    return res[1:-1]


def get_current_limit(voltage_level: float, current_limit: float, power_limit: float) -> float:
    """Limit current so the requested voltage does not exceed the power limit."""
    if voltage_level * current_limit > power_limit:
        value = str(float(power_limit / voltage_level))
        return float(value[: min(len(value), value.index(".") + 4)])
    return current_limit


def build_trigger_terminal(resource_name: str, channel_name: str, event_name: str) -> str:
    """Build the niDCPower engine event terminal path."""
    return f"/{resource_name}/Engine{channel_name}/{event_name}"


class ResolvedPin(NamedTuple):
    """The NI-DCPower session, device name and channel that a pin maps to."""

    session: Session
    device_name: str
    channel: str


def resolve_pin(session_infos: Sequence[SessionInformation], pin: str) -> ResolvedPin:
    """Resolve a pin to its single NI-DCPower session, device and channel."""
    matches = [
        (info, mapping.channel)
        for info in session_infos
        for mapping in info.channel_mappings
        if mapping.pin_or_relay_name == pin
    ]
    if len(matches) != 1:
        raise ValueError(f"Pin {pin!r} must map to exactly one NI-DCPower channel.")
    info, mapped_channel = matches[0]
    device_name, separator, channel = mapped_channel.rpartition("/")
    if not separator:
        device_name, channel = info.resource_name, mapped_channel
    return ResolvedPin(info.session, device_name, channel)


def trigger_terminal(pin: ResolvedPin, signal: str) -> str:
    """Return the device-specific trigger terminal for a channel."""
    if pin.session.instrument_model == "NI PXI-4132":
        return f"/{pin.device_name}/{signal}"
    return build_trigger_terminal(pin.device_name, pin.channel, signal)


def configure_initial_channel(
    pin: ResolvedPin,
    voltage: float,
    current: float,
    delay: float,
    is_source: bool,
    source_terminal: str | None,
) -> None:
    """Configure one channel for DUT power-on or load setup."""
    channel = pin.session.channels[pin.channel]
    channel.sense = Sense.REMOTE
    channel.source_mode = SourceMode.SINGLE_POINT
    channel.output_function = OutputFunction.DC_VOLTAGE if is_source else OutputFunction.DC_CURRENT
    if is_source:
        channel.voltage_level_autorange = True
        channel.current_limit_autorange = True
        channel.voltage_level = voltage
        channel.current_limit = current
    else:
        channel.current_level_autorange = True
        channel.current_level = current
        channel.voltage_limit_range = voltage
    channel.source_delay = delay
    channel.measure_when = MeasureWhen.ON_DEMAND
    if source_terminal is not None:
        channel.source_trigger_type = TriggerType.DIGITAL_EDGE
        channel.digital_edge_source_trigger_input_terminal = source_terminal
    channel.commit()


def configure_source_slave(pin: ResolvedPin, source_terminal: str, measure_terminal: str) -> None:
    """Synchronize one source slave with the source master."""
    channel = pin.session.channels[pin.channel]
    channel.source_trigger_type = TriggerType.DIGITAL_EDGE
    channel.digital_edge_source_trigger_input_terminal = source_terminal
    channel.measure_when = MeasureWhen.ON_MEASURE_TRIGGER
    channel.measure_trigger_type = TriggerType.DIGITAL_EDGE
    channel.digital_edge_measure_trigger_input_terminal = measure_terminal
    channel.commit()


def initiate_channel(pin: ResolvedPin) -> None:
    """Initiate a channel."""
    pin.session.channels[pin.channel].initiate()


def wait_for_channel(pin: ResolvedPin, event: Event) -> None:
    """Wait for a channel event."""
    pin.session.channels[pin.channel].wait_for_event(event_id=event)


def measure_channel(pin: ResolvedPin) -> tuple[float, float]:
    """Measure one channel's voltage and current on demand."""
    channel = pin.session.channels[pin.channel]
    return (
        channel.measure(measurement_type=MeasurementTypes.VOLTAGE),
        channel.measure(measurement_type=MeasurementTypes.CURRENT),
    )


def fetch_channel(pin: ResolvedPin) -> tuple[float, float]:
    """Fetch one channel's voltage and current."""
    reading = pin.session.channels[pin.channel].fetch_multiple(count=1)[0]
    return reading.voltage, reading.current


def abort_channel(pin: ResolvedPin) -> None:
    """Abort one channel."""
    pin.session.channels[pin.channel].abort()


def power_off_channel(pin: ResolvedPin) -> None:
    """Disable one channel's output."""
    pin.session.channels[pin.channel].output_enabled = False


def reset_channel(pin: ResolvedPin) -> None:
    """Reset one channel."""
    pin.session.channels[pin.channel].reset()


def configure_source(
    session: Session,
    channel_name: str,
    voltage_levels: list[float],
    current_limit: float,
    power_limit: float,
    current_sweep_points: int,
    source_delay: float,
    aperture_time: float,
) -> None:
    """Configure a sequenced voltage sweep on the source."""
    session.channels[channel_name].sense = Sense.REMOTE
    session.channels[channel_name].source_mode = SourceMode.SEQUENCE
    session.channels[channel_name].output_function = OutputFunction.DC_VOLTAGE

    session.channels[channel_name].voltage_level_autorange = True
    session.channels[channel_name].current_limit_autorange = True
    session.channels[channel_name].source_delay = source_delay

    session.channels[channel_name].create_advanced_sequence(
        "SourceVoltages", ["voltage_level", "current_limit"]
    )

    for i in voltage_levels:
        for _ in range(current_sweep_points):
            session.channels[channel_name].create_advanced_sequence_step()
            session.channels[channel_name].voltage_level = i
            session.channels[channel_name].current_limit = get_current_limit(
                i, current_limit, power_limit
            )

    session.channels[channel_name].measure_when = MeasureWhen.AUTOMATICALLY_AFTER_SOURCE_COMPLETE
    session.channels[channel_name].aperture_time = aperture_time

    session.channels[channel_name].commit()


def configure_load(
    session: Session,
    channel_name: str,
    current_levels: list[float],
    voltage_limit_range: float,
    aperture_time: float,
    source_terminal_name: str,
    measure_terminal_name: str,
) -> None:
    """Configure a sequenced current sweep on the load."""
    session.channels[channel_name].sense = Sense.REMOTE
    session.channels[channel_name].source_mode = SourceMode.SEQUENCE
    session.channels[channel_name].output_function = OutputFunction.DC_CURRENT

    session.channels[channel_name].current_level_autorange = True
    session.channels[channel_name].voltage_limit_range = voltage_limit_range
    session.channels[channel_name].set_sequence(
        current_levels, [0 for _ in range(len(current_levels))]
    )

    session.channels[channel_name].source_trigger_type = TriggerType.DIGITAL_EDGE
    session.channels[channel_name].measure_trigger_type = TriggerType.DIGITAL_EDGE

    session.channels[channel_name].digital_edge_source_trigger_input_terminal = source_terminal_name
    session.channels[channel_name].measure_when = MeasureWhen.ON_MEASURE_TRIGGER
    session.channels[channel_name].aperture_time = aperture_time
    session.channels[channel_name].digital_edge_measure_trigger_input_terminal = (
        measure_terminal_name
    )

    session.channels[channel_name].commit()


class SystemConfiguration(Enum):
    """Supported ways to connect a group of channels."""

    Series = 0
    Parallel = 1


def _split_voltage(value: float, count: int, topology: SystemConfiguration) -> float:
    if count < 1 or topology not in (SystemConfiguration.Series, SystemConfiguration.Parallel):
        raise ValueError("A valid topology and at least one channel are required.")
    return value / count if topology == SystemConfiguration.Series else value


def _split_current(value: float, count: int, topology: SystemConfiguration) -> float:
    if count < 1 or topology not in (SystemConfiguration.Series, SystemConfiguration.Parallel):
        raise ValueError("A valid topology and at least one channel are required.")
    return value / count if topology == SystemConfiguration.Parallel else value


def _aggregate(
    readings: Sequence[tuple[float, float]], topology: SystemConfiguration
) -> tuple[float, float]:
    if not readings or topology not in (SystemConfiguration.Series, SystemConfiguration.Parallel):
        raise ValueError("A valid topology and at least one reading are required.")
    voltage = sum(reading[0] for reading in readings)
    current = sum(reading[1] for reading in readings)
    if topology == SystemConfiguration.Parallel:
        voltage /= len(readings)
    else:
        current /= len(readings)
    return voltage, current


class GangedSession:
    """Manage a master channel and its slaves using single-channel operations."""

    def __init__(
        self,
        session_infos: Sequence[SessionInformation],
        resources: Sequence[str],
        topology: SystemConfiguration,
    ) -> None:
        """Resolve the requested resources into a validated channel group."""
        self._channels = self.validate_channel_group(session_infos, resources)
        self._topology = topology

    @staticmethod
    def validate_channel_group(
        session_infos: Sequence[SessionInformation], resources: Sequence[str]
    ) -> tuple[ResolvedPin, ...]:
        """Resolve resources and reject missing, duplicate or shared channels."""
        if not resources or any(not resource.strip() for resource in resources):
            raise ValueError("A channel group must contain at least one non-empty resource.")
        if len(set(resources)) != len(resources):
            raise ValueError("A channel group cannot contain duplicate resources.")
        channels = tuple(resolve_pin(session_infos, resource) for resource in resources)
        if len({(pin.device_name, pin.channel) for pin in channels}) != len(channels):
            raise ValueError("Resources in a channel group must map to distinct channels.")
        return channels

    def validate_distinct_group(self, other: "GangedSession") -> None:
        """Reject physical channels shared by two groups."""
        if {(pin.device_name, pin.channel) for pin in self._channels} & {
            (pin.device_name, pin.channel) for pin in other._channels
        }:
            raise ValueError("Source and load groups must not share a channel.")

    def configure_channel_group(
        self, voltage: float, current: float, delay: float, is_source: bool
    ) -> None:
        """Configure every channel for DUT power-on or load setup."""
        channels = self._channels
        shared_voltage = _split_voltage(voltage, len(channels), self._topology)
        shared_current = _split_current(current, len(channels), self._topology)
        for index, pin in enumerate(channels):
            source_terminal = trigger_terminal(channels[0], "SourceTrigger") if index else None
            configure_initial_channel(
                pin, shared_voltage, shared_current, delay, is_source, source_terminal
            )

    def initiate_channel_group(self) -> None:
        """Arm slave channels before initiating the master."""
        channels = self._channels
        for pin in reversed(channels[1:]):
            initiate_channel(pin)
        initiate_channel(channels[0])

    def power_on_channel_group(
        self, voltage: float, current: float, delay: float, is_source: bool
    ) -> tuple[float, float]:
        """Apply initial group settings and return the combined readings."""
        self.configure_channel_group(voltage, current, delay, is_source)
        self.initiate_channel_group()
        self._wait_for_event(Event.SOURCE_COMPLETE)
        measurement = self.measure_channel_group()
        for pin in self._channels:
            abort_channel(pin)
        return measurement

    def measure_channel_group(self) -> tuple[float, float]:
        """Measure each channel and combine the results."""
        return _aggregate([measure_channel(pin) for pin in self._channels], self._topology)

    def fetch_channel_group(self) -> tuple[float, float]:
        """Fetch one reading per channel and combine the results."""
        return _aggregate([fetch_channel(pin) for pin in self._channels], self._topology)

    def configure_sweep(
        self,
        load: "GangedSession",
        voltages: list[float],
        currents: list[float],
        current_limit: float,
        maximum_power: float,
        voltage_limit: float,
        source_delay: float,
        aperture_time: float,
    ) -> None:
        """Configure source and load sweeps to follow the source master."""
        source_channels = self._channels
        load_channels = load._channels
        master = source_channels[0]
        for index, pin in enumerate(source_channels):
            configure_source(
                pin.session,
                pin.channel,
                [_split_voltage(value, len(source_channels), self._topology) for value in voltages],
                _split_current(current_limit, len(source_channels), self._topology),
                maximum_power / len(source_channels),
                len(currents),
                source_delay,
                aperture_time,
            )
            if index:
                configure_source_slave(
                    pin,
                    trigger_terminal(master, "SourceTrigger"),
                    trigger_terminal(master, "SourceCompleteEvent"),
                )
        current_values = len(voltages) * [
            _split_current(value, len(load_channels), load._topology) for value in currents
        ]
        for pin in load_channels:
            configure_load(
                pin.session,
                pin.channel,
                current_values,
                _split_voltage(voltage_limit, len(load_channels), load._topology),
                aperture_time,
                trigger_terminal(master, "SourceTrigger"),
                trigger_terminal(master, "SourceCompleteEvent"),
            )

    def initiate_sweep(self, load: "GangedSession") -> None:
        """Arm load channels before initiating the source group."""
        for pin in reversed(load._channels):
            initiate_channel(pin)
        self.initiate_channel_group()

    def _wait_for_event(self, event: Event) -> None:
        for pin in self._channels:
            wait_for_channel(pin, event)

    def wait_for_sweep(self) -> None:
        """Wait for the group's sequence to finish."""
        self._wait_for_event(Event.SEQUENCE_ENGINE_DONE)

    def power_off_channel_group(self, *others: "GangedSession") -> None:
        """Disable and reset the channels of all supplied groups."""
        self.reset_channel_group(*others, abort=False)

    def reset_channel_group(self, *others: "GangedSession", abort: bool = True) -> None:
        """Attempt each cleanup operation on every channel, reporting all failures."""
        errors: list[Exception] = []
        operations = (
            (
                ("abort", abort_channel),
                ("disable", power_off_channel),
                ("reset", reset_channel),
            )
            if abort
            else (("disable", power_off_channel), ("reset", reset_channel))
        )
        for name, operation in operations:
            for group in (self, *others):
                for pin in group._channels:
                    try:
                        operation(pin)
                    except Exception as exc:
                        logging.exception("Failed to %s %s/%s", name, pin.device_name, pin.channel)
                        errors.append(exc)
        if errors:
            raise ExceptionGroup("Failed to clean up NI-DCPower channels", errors)

    @contextmanager
    def cleanup_on_exit(self, *others: "GangedSession") -> Iterator[None]:
        """Reset groups on exit while retaining the original measurement error."""
        try:
            yield
        except BaseException as original:
            try:
                self.reset_channel_group(*others)
            except Exception as cleanup_error:
                raise BaseExceptionGroup(
                    "Measurement and cleanup failed", [original, cleanup_error]
                )
            raise
        else:
            self.reset_channel_group(*others)
