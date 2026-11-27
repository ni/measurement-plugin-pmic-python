"""NI-DCPower operations for efficiency and load regulation with channel ganging.

Series ganging divides voltage settings by the number of channels, sums the measured voltages
and averages the measured currents. Parallel ganging divides current settings, averages the
measured voltages and sums the measured currents. Manual ganging applies every setting unchanged
and reports NaN measurements.

Verify device-specific wiring, sensing, trigger routing and safe load sharing before
energizing a DUT. Physical synchronization has not been validated.
"""

import logging
import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import NamedTuple

import nidcpower
from ni_measurement_plugin_sdk_service.session_management import SessionInformation
from nidcpower import (
    Event,
    MeasurementTypes,
    MeasureWhen,
    OutputFunction,
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


class SystemConfiguration(Enum):
    """Supported ways to connect a group of channels."""

    Series = 0
    Parallel = 1
    Manual = 2


class PowerOnOutputFunction(Enum):
    """Output function used to power on the DUT."""

    DC_Voltage = 0
    DC_Current = 1


class SenseMode(Enum):
    """Local or remote sensing."""

    Local = 0
    Remote = 1


class LimitSymmetry(Enum):
    """Whether a compliance limit is symmetric or has separate low and high values."""

    Symmetric = 0
    Asymmetric = 1


class TransientResponse(Enum):
    """Transient response of the output channel."""

    Normal = 0
    Fast = 1
    Slow = 2
    Custom = 3


_SENSE = {SenseMode.Local: nidcpower.Sense.LOCAL, SenseMode.Remote: nidcpower.Sense.REMOTE}
_SYMMETRY = {
    LimitSymmetry.Symmetric: nidcpower.ComplianceLimitSymmetry.SYMMETRIC,
    LimitSymmetry.Asymmetric: nidcpower.ComplianceLimitSymmetry.ASYMMETRIC,
}
_TRANSIENT_RESPONSE = {
    TransientResponse.Normal: nidcpower.TransientResponse.NORMAL,
    TransientResponse.Fast: nidcpower.TransientResponse.FAST,
    TransientResponse.Slow: nidcpower.TransientResponse.SLOW,
    TransientResponse.Custom: nidcpower.TransientResponse.CUSTOM,
}


@dataclass(frozen=True)
class CustomTransientResponse:
    """Loop compensation used when the transient response is custom."""

    gain_bandwidth: float
    compensation_frequency: float
    pole_zero_ratio: float


@dataclass(frozen=True)
class ChannelGroupSettings:
    """Ranges, compliance limit, sense and transient response of a source or load group.

    The source outputs DC voltage, so its level is a voltage and its limit is a current. The load
    outputs DC current, so its level is a current and its limit is a voltage.
    """

    sense: SenseMode
    level_range: float
    limit_range: float
    limit_low: float
    limit_high: float
    limit_symmetry: LimitSymmetry
    transient_response: TransientResponse
    custom_response: CustomTransientResponse


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


def validate_loads(
    resources: Sequence[str],
    peak_current: float,
    maximum_current_per_load: float,
    topology: SystemConfiguration,
    current_level_range: float,
) -> None:
    """Validate the sweep and range against all selected loads without dropping slaves."""
    count = len(resources) if topology == SystemConfiguration.Parallel else 1
    capacity = count * maximum_current_per_load
    if peak_current > capacity:
        raise ValueError(
            f"The load sweep requires {peak_current:g} A, but the selected loads support "
            f"{capacity:g} A with the configured maximum current per load."
        )
    if current_level_range > capacity:
        raise ValueError(
            f"The load current level range is {current_level_range:g} A, but the selected "
            f"loads support {capacity:g} A. Reduce the group range or select more parallel "
            "loads; set MAX CURRENT PER LOAD to the actual channel capability."
        )


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


def configure_channel_settings(
    channel: Session, settings: ChannelGroupSettings, is_source: bool
) -> None:
    """Apply the output function, ranges, compliance limit, sense and transient response."""
    channel.sense = _SENSE[settings.sense]
    channel.output_function = OutputFunction.DC_VOLTAGE if is_source else OutputFunction.DC_CURRENT
    if is_source:
        channel.voltage_level_range = settings.level_range
        channel.current_limit_range = settings.limit_range
    else:
        channel.current_level_range = settings.level_range
        channel.voltage_limit_range = settings.limit_range
    channel.compliance_limit_symmetry = _SYMMETRY[settings.limit_symmetry]
    limit = "current_limit" if is_source else "voltage_limit"
    if settings.limit_symmetry == LimitSymmetry.Symmetric:
        setattr(channel, limit, settings.limit_high)
    else:
        setattr(channel, f"{limit}_high", settings.limit_high)
        setattr(channel, f"{limit}_low", settings.limit_low)
    channel.transient_response = _TRANSIENT_RESPONSE[settings.transient_response]
    if settings.transient_response == TransientResponse.Custom:
        loop = "voltage" if is_source else "current"
        custom = settings.custom_response
        setattr(channel, f"{loop}_gain_bandwidth", custom.gain_bandwidth)
        setattr(channel, f"{loop}_compensation_frequency", custom.compensation_frequency)
        setattr(channel, f"{loop}_pole_zero_ratio", custom.pole_zero_ratio)


def configure_initial_channel(
    pin: ResolvedPin,
    settings: ChannelGroupSettings,
    level: float,
    delay: float,
    is_source: bool,
    source_terminal: str | None,
) -> None:
    """Configure one channel for DUT power-on or load setup."""
    channel = pin.session.channels[pin.channel]
    channel.source_mode = SourceMode.SINGLE_POINT
    configure_channel_settings(channel, settings, is_source)
    if is_source:
        channel.voltage_level = level
    else:
        channel.current_level = level
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


def wait_for_channel(pin: ResolvedPin, event: Event, timeout: float) -> None:
    """Wait for a channel event."""
    pin.session.channels[pin.channel].wait_for_event(event_id=event, timeout=timeout)


def measure_channel(pin: ResolvedPin) -> tuple[float, float]:
    """Measure one channel's voltage and current on demand."""
    channel = pin.session.channels[pin.channel]
    return (
        channel.measure(measurement_type=MeasurementTypes.VOLTAGE),
        channel.measure(measurement_type=MeasurementTypes.CURRENT),
    )


def fetch_channel(pin: ResolvedPin, timeout: float) -> tuple[float, float]:
    """Fetch one channel's voltage and current."""
    reading = pin.session.channels[pin.channel].fetch_multiple(count=1, timeout=timeout)[0]
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
    pin: ResolvedPin,
    settings: ChannelGroupSettings,
    voltage_levels: list[float],
    source_delay: float,
    aperture_time: float,
) -> None:
    """Configure a sequenced voltage sweep on a source channel."""
    channel = pin.session.channels[pin.channel]
    channel.source_mode = SourceMode.SEQUENCE
    configure_channel_settings(channel, settings, True)
    channel.source_delay = source_delay
    channel.set_sequence(voltage_levels, [source_delay] * len(voltage_levels))
    channel.measure_when = MeasureWhen.AUTOMATICALLY_AFTER_SOURCE_COMPLETE
    channel.aperture_time = aperture_time
    channel.commit()


def configure_load(
    pin: ResolvedPin,
    settings: ChannelGroupSettings,
    current_levels: list[float],
    aperture_time: float,
    source_terminal_name: str,
    measure_terminal_name: str,
) -> None:
    """Configure a sequenced current sweep on a load channel."""
    channel = pin.session.channels[pin.channel]
    channel.source_mode = SourceMode.SEQUENCE
    configure_channel_settings(channel, settings, False)
    channel.set_sequence(current_levels, [0.0] * len(current_levels))
    channel.source_trigger_type = TriggerType.DIGITAL_EDGE
    channel.digital_edge_source_trigger_input_terminal = source_terminal_name
    channel.measure_when = MeasureWhen.ON_MEASURE_TRIGGER
    channel.measure_trigger_type = TriggerType.DIGITAL_EDGE
    channel.digital_edge_measure_trigger_input_terminal = measure_terminal_name
    channel.aperture_time = aperture_time
    channel.commit()


def _split_voltage(value: float, count: int, topology: SystemConfiguration) -> float:
    if count < 1:
        raise ValueError("At least one channel is required.")
    return value / count if topology == SystemConfiguration.Series else value


def _split_current(value: float, count: int, topology: SystemConfiguration) -> float:
    if count < 1:
        raise ValueError("At least one channel is required.")
    return value / count if topology == SystemConfiguration.Parallel else value


def _aggregate(
    readings: Sequence[tuple[float, float]], topology: SystemConfiguration
) -> tuple[float, float]:
    if not readings:
        raise ValueError("At least one reading is required.")
    if topology == SystemConfiguration.Manual:
        return math.nan, math.nan
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
        settings: ChannelGroupSettings,
        is_source: bool,
        timeout: float,
    ) -> None:
        """Resolve the requested resources into a validated channel group."""
        self._channels = self.validate_channel_group(session_infos, resources)
        self._topology = topology
        self._is_source = is_source
        self._timeout = timeout
        self._settings = self._share_settings(settings)

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

    def _split_level(self, value: float) -> float:
        split = _split_voltage if self._is_source else _split_current
        return split(value, len(self._channels), self._topology)

    def _split_limit(self, value: float) -> float:
        split = _split_current if self._is_source else _split_voltage
        return split(value, len(self._channels), self._topology)

    def _share_settings(self, settings: ChannelGroupSettings) -> ChannelGroupSettings:
        """Return the settings of one channel of the group."""
        return replace(
            settings,
            level_range=self._split_level(settings.level_range),
            limit_range=self._split_limit(settings.limit_range),
            limit_low=self._split_limit(settings.limit_low),
            limit_high=self._split_limit(settings.limit_high),
        )

    def validate_distinct_group(self, other: "GangedSession") -> None:
        """Reject physical channels shared by two groups."""
        if {(pin.device_name, pin.channel) for pin in self._channels} & {
            (pin.device_name, pin.channel) for pin in other._channels
        }:
            raise ValueError("Source and load groups must not share a channel.")

    def configure_channel_group(self, level: float, delay: float) -> None:
        """Configure every channel for DUT power-on or load setup."""
        channels = self._channels
        shared_level = self._split_level(level)
        for index, pin in enumerate(channels):
            source_terminal = trigger_terminal(channels[0], "SourceTrigger") if index else None
            configure_initial_channel(
                pin, self._settings, shared_level, delay, self._is_source, source_terminal
            )

    def initiate_channel_group(self) -> None:
        """Arm slave channels before initiating the master."""
        channels = self._channels
        for pin in reversed(channels[1:]):
            initiate_channel(pin)
        initiate_channel(channels[0])

    def power_on_channel_group(self, level: float, delay: float) -> tuple[float, float]:
        """Apply initial group settings and return the combined readings."""
        self.configure_channel_group(level, delay)
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
        return _aggregate(
            [fetch_channel(pin, self._timeout) for pin in self._channels], self._topology
        )

    def configure_sweep(
        self,
        load: "GangedSession",
        voltages: list[float],
        currents: list[float],
        source_delay: float,
        aperture_time: float,
    ) -> None:
        """Configure source and load sweeps to follow the source master."""
        source_channels = self._channels
        master = source_channels[0]
        voltage_values = [self._split_level(value) for value in voltages for _ in currents]
        current_values = len(voltages) * [load._split_level(value) for value in currents]
        for index, pin in enumerate(source_channels):
            configure_source(pin, self._settings, voltage_values, source_delay, aperture_time)
            if index:
                configure_source_slave(
                    pin,
                    trigger_terminal(master, "SourceTrigger"),
                    trigger_terminal(master, "SourceCompleteEvent"),
                )
        for pin in load._channels:
            configure_load(
                pin,
                load._settings,
                current_values,
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
            wait_for_channel(pin, event, self._timeout)

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
