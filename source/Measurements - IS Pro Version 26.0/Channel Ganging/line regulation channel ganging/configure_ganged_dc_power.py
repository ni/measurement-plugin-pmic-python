"""NI-DCPower channel ganging helpers for the Line Regulation Channel Ganging measurement.

A ganged group is one master and zero or more slave pins, each resolved to one NI-DCPower
channel. Series ganging divides voltage settings by the number of channels and sums the measured
voltages. Parallel ganging divides current settings and averages the measured voltages. Manual
ganging applies every setting unchanged and reports NaN measurements.

The sessions are opened and closed by the Measurement Plug-In SDK; nothing here closes a session.
"""

from __future__ import annotations

import contextlib
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import hightime
import nidcpower
from ni_measurement_plugin_sdk_service.session_management import SessionInformation

_logger = logging.getLogger(__name__)

SEQUENCE_TIMEOUT = hightime.timedelta(seconds=10)
FETCH_TIMEOUT = hightime.timedelta(seconds=1)
PXI_4132_MODEL = "NI PXI-4132"


class SystemConfiguration(Enum):
    """How the channels of a ganged group are combined."""

    SERIES = 0
    PARALLEL = 1
    MANUAL = 2


class LimitSymmetry(Enum):
    """Whether a compliance limit is symmetric or has separate low and high values."""

    SYMMETRIC = 0
    ASYMMETRIC = 1


class SenseMode(Enum):
    """Local or remote sensing."""

    LOCAL = 0
    REMOTE = 1


class TransientResponse(Enum):
    """Transient response of the output channel."""

    NORMAL = 0
    FAST = 1
    SLOW = 2
    CUSTOM = 3


class SweepType(Enum):
    """Spacing of the source voltage sweep."""

    LINEAR = 0
    LOGARITHMIC = 1


_SENSE = {SenseMode.LOCAL: nidcpower.Sense.LOCAL, SenseMode.REMOTE: nidcpower.Sense.REMOTE}
_SYMMETRY = {
    LimitSymmetry.SYMMETRIC: nidcpower.ComplianceLimitSymmetry.SYMMETRIC,
    LimitSymmetry.ASYMMETRIC: nidcpower.ComplianceLimitSymmetry.ASYMMETRIC,
}
_TRANSIENT_RESPONSE = {
    TransientResponse.NORMAL: nidcpower.TransientResponse.NORMAL,
    TransientResponse.FAST: nidcpower.TransientResponse.FAST,
    TransientResponse.SLOW: nidcpower.TransientResponse.SLOW,
    TransientResponse.CUSTOM: nidcpower.TransientResponse.CUSTOM,
}


@dataclass
class CustomTransientResponse:
    """Parameters used when the transient response is custom (voltage or current loop)."""

    gain_bandwidth: float
    compensation_frequency: float
    pole_zero_ratio: float


@dataclass
class GangedChannel:
    """One NI-DCPower channel of a ganged group, resolved from a pin."""

    session: nidcpower.Session
    device: str
    channel: str

    @property
    def channel_number(self) -> str:
        """The channel number without any device prefix, used for trigger engine names."""
        return self.channel.rpartition("/")[2]


def resolve_pin(session_infos: Sequence[SessionInformation], pin: str) -> GangedChannel:
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
    device = mapped_channel.rpartition("/")[0]
    resolved = GangedChannel(info.session, device or info.resource_name, mapped_channel)
    _logger.info("Pin %s resolved to device %s channel %s", pin, resolved.device, resolved.channel)
    return resolved


def resolve_pins(
    session_infos: Sequence[SessionInformation], pins: Sequence[str]
) -> list[GangedChannel]:
    """Resolve the master pin first, then the slave pins, ignoring empty pin names."""
    return [resolve_pin(session_infos, pin) for pin in pins if pin]


class GangedSession:
    """Channels of one master (first) and its slaves."""

    def __init__(self, config: SystemConfiguration, ganged_channels: list[GangedChannel]) -> None:
        """Wrap resolved channels; the first channel is the master."""
        if not ganged_channels:
            raise ValueError("A ganged group needs at least a master pin.")
        self.config = config
        self.ganged_channels = ganged_channels

    @property
    def channels(self) -> list[nidcpower.Session]:
        """Channel scoped view of every session, master first."""
        return [item.session.channels[item.channel] for item in self.ganged_channels]

    @property
    def master(self) -> GangedChannel:
        """The master channel."""
        return self.ganged_channels[0]

    def _voltage_share(self, value: float) -> float:
        if self.config == SystemConfiguration.SERIES:
            return value / len(self.ganged_channels)
        return value

    def _current_share(self, value: float) -> float:
        if self.config == SystemConfiguration.PARALLEL:
            return value / len(self.ganged_channels)
        return value

    def configure_source_mode(self, source_mode: nidcpower.SourceMode) -> None:
        """Set the source mode of every channel."""
        for channel in self.channels:
            channel.source_mode = source_mode

    def configure_sense(self, sense: SenseMode) -> None:
        """Set local or remote sensing of every channel."""
        for channel in self.channels:
            channel.sense = _SENSE[sense]

    def configure_output_function(self, output_function: nidcpower.OutputFunction) -> None:
        """Set the output function of every channel."""
        for channel in self.channels:
            channel.output_function = output_function

    def configure_voltage_level_range(self, voltage_level_range: float) -> None:
        """Set the voltage level range, divided between the channels in series."""
        for channel in self.channels:
            channel.voltage_level_range = self._voltage_share(voltage_level_range)

    def configure_voltage_level(self, voltage_level: float) -> None:
        """Set the voltage level, divided between the channels in series."""
        for channel in self.channels:
            channel.voltage_level = self._voltage_share(voltage_level)

    def configure_current_limit_range(self, current_limit_range: float) -> None:
        """Set the current limit range, divided between the channels in parallel."""
        for channel in self.channels:
            channel.current_limit_range = self._current_share(current_limit_range)

    def configure_current_limit(self, low: float, high: float, symmetry: LimitSymmetry) -> None:
        """Set the current limit, divided between the channels in parallel."""
        for channel in self.channels:
            channel.compliance_limit_symmetry = _SYMMETRY[symmetry]
            if symmetry == LimitSymmetry.SYMMETRIC:
                channel.current_limit = self._current_share(high)
            else:
                channel.current_limit_high = self._current_share(high)
                channel.current_limit_low = self._current_share(low)

    def configure_voltage_limit_range(self, voltage_limit_range: float) -> None:
        """Set the voltage limit range, divided between the channels in series."""
        for channel in self.channels:
            channel.voltage_limit_range = self._voltage_share(voltage_limit_range)

    def configure_voltage_limit(self, low: float, high: float, symmetry: LimitSymmetry) -> None:
        """Set the voltage limit, divided between the channels in series."""
        for channel in self.channels:
            channel.compliance_limit_symmetry = _SYMMETRY[symmetry]
            if symmetry == LimitSymmetry.SYMMETRIC:
                channel.voltage_limit = self._voltage_share(high)
            else:
                channel.voltage_limit_high = self._voltage_share(high)
                channel.voltage_limit_low = self._voltage_share(low)

    def configure_current_level_range(self, current_level_range: float) -> None:
        """Set the current level range, divided between the channels in parallel."""
        for channel in self.channels:
            channel.current_level_range = self._current_share(current_level_range)

    def configure_current_level(self, current_level: float) -> None:
        """Set the current level, divided between the channels in parallel."""
        for channel in self.channels:
            channel.current_level = self._current_share(current_level)

    def configure_source_loop(
        self,
        transient_response: TransientResponse,
        custom: CustomTransientResponse,
        source_delay: float,
        aperture_time: float | None,
        is_current_loop: bool,
    ) -> None:
        """Set transient response, loop compensation, source delay and optionally aperture time."""
        for channel in self.channels:
            channel.transient_response = _TRANSIENT_RESPONSE[transient_response]
            if transient_response == TransientResponse.CUSTOM:
                if is_current_loop:
                    channel.current_gain_bandwidth = custom.gain_bandwidth
                    channel.current_compensation_frequency = custom.compensation_frequency
                    channel.current_pole_zero_ratio = custom.pole_zero_ratio
                else:
                    channel.voltage_gain_bandwidth = custom.gain_bandwidth
                    channel.voltage_compensation_frequency = custom.compensation_frequency
                    channel.voltage_pole_zero_ratio = custom.pole_zero_ratio
            channel.source_delay = hightime.timedelta(seconds=source_delay)
            if aperture_time is not None:
                channel.aperture_time = aperture_time

    def set_output_enabled(self, enabled: bool) -> None:
        """Enable or disable the output of every channel."""
        for channel in self.channels:
            channel.output_enabled = enabled

    def set_output_connected(self, connected: bool) -> None:
        """Connect or disconnect the output relay of every channel."""
        for channel in self.channels:
            channel.output_connected = connected

    def commit(self) -> None:
        """Commit every channel."""
        for channel in self.channels:
            channel.commit()

    def initiate(self) -> None:
        """Initiate the slaves first and the master last."""
        for channel in reversed(self.channels):
            channel.initiate()

    def wait_for_event(self, event: nidcpower.Event) -> None:
        """Wait for an event on every channel."""
        for channel in self.channels:
            channel.wait_for_event(event, SEQUENCE_TIMEOUT)

    def abort(self) -> None:
        """Abort every channel."""
        for channel in self.channels:
            channel.abort()

    def shutdown(self) -> None:
        """Abort, power off and reset every channel, ignoring driver errors."""
        for step in (
            self.abort,
            lambda: self.set_output_enabled(False),
            lambda: self.set_output_connected(False),
            self._reset,
        ):
            with contextlib.suppress(nidcpower.Error):
                step()

    def _reset(self) -> None:
        for channel in self.channels:
            channel.reset()

    def set_sequence(
        self, output_function: nidcpower.OutputFunction, values: list[float], source_delay: float
    ) -> None:
        """Set a sequence of voltage or current values, each with the same source delay."""
        if output_function == nidcpower.OutputFunction.DC_VOLTAGE:
            shared = [self._voltage_share(value) for value in values]
        else:
            shared = [self._current_share(value) for value in values]
        source_delays = [source_delay] * len(shared)
        for channel in self.channels:
            channel.set_sequence(shared, source_delays)

    def configure_triggers(self, measure_when: nidcpower.MeasureWhen) -> None:
        """Set Measure When on the master and synchronize the slaves to the master's triggers."""
        master_channel, *slave_channels = self.channels
        source_terminal = build_terminal_name(self.master, "SourceTrigger")
        measure_terminal = build_terminal_name(self.master, "MeasureTrigger")
        master_channel.measure_when = measure_when
        master_channel.commit()
        for channel in slave_channels:
            channel.source_trigger_type = nidcpower.TriggerType.DIGITAL_EDGE
            channel.digital_edge_source_trigger_input_terminal = source_terminal
            channel.measure_trigger_type = nidcpower.TriggerType.DIGITAL_EDGE
            channel.digital_edge_measure_trigger_input_terminal = measure_terminal
            channel.measure_when = nidcpower.MeasureWhen.ON_MEASURE_TRIGGER
            channel.commit()

    def configure_master_source_trigger(self, input_terminal: str) -> None:
        """Start the master's source from a digital edge on `input_terminal`."""
        channel = self.channels[0]
        channel.source_trigger_type = nidcpower.TriggerType.DIGITAL_EDGE
        channel.digital_edge_source_trigger_input_terminal = input_terminal

    def configure_master_measure_trigger(self, input_terminal: str) -> None:
        """Start the master's measurements from a digital edge on `input_terminal`."""
        channel = self.channels[0]
        channel.measure_trigger_type = nidcpower.TriggerType.DIGITAL_EDGE
        channel.digital_edge_measure_trigger_input_terminal = input_terminal

    def fetch_voltage(self) -> float:
        """Fetch one voltage measurement of every channel and combine them for the ganging."""
        voltages = [
            channel.fetch_multiple(1, FETCH_TIMEOUT)[0].voltage for channel in self.channels
        ]
        if self.config == SystemConfiguration.SERIES:
            return sum(voltages)
        if self.config == SystemConfiguration.PARALLEL:
            return sum(voltages) / len(voltages)
        return math.nan


def build_terminal_name(ganged_channel: GangedChannel, local_terminal_name: str) -> str:
    """Return the fully qualified name of a trigger terminal of the channel's device."""
    if ganged_channel.session.instrument_model == PXI_4132_MODEL:
        return f"/{ganged_channel.device}/{local_terminal_name}"
    return build_trigger_terminal(
        ganged_channel.device, ganged_channel.channel_number, local_terminal_name
    )


def build_trigger_terminal(device: str, channel: str, trigger_or_event: str) -> str:
    """Return the terminal name of a trigger or event of the device's engine."""
    return f"/{device}/Engine{channel}/{trigger_or_event}"


def calculate_linear_sweep(start: float, stop: float, points: int) -> list[float]:
    """Return `points` evenly spaced values from `start` to `stop`."""
    step = (stop - start) / (points - 1) if points > 1 else 0.0
    return [start + step * index for index in range(max(points, 0))]


def calculate_logarithmic_sweep(start: float, stop: float, points_per_decade: int) -> list[float]:
    """Return values from `start` to `stop` with `points_per_decade` points per decade.

    The last value is clamped to `stop`. An invalid range returns an empty list.
    """
    if start <= 0 or stop < start or points_per_decade <= 0:
        return []
    factor = 10 ** (1 / points_per_decade)
    values = []
    value = start
    while True:
        values.append(min(value, stop))
        if value >= stop:
            return values
        value = start * factor ** len(values)


def calculate_sweep_values(
    sweep_type: SweepType, start: float, stop: float, points: int
) -> list[float]:
    """Return the source voltage sweep for the given sweep type."""
    if sweep_type == SweepType.LOGARITHMIC:
        return calculate_logarithmic_sweep(start, stop, points)
    return calculate_linear_sweep(start, stop, points)


def calculate_voltage_deviation(voltages: Sequence[float], nominal_voltage: float) -> list[float]:
    """Return the deviation of each voltage from `nominal_voltage` in percent.

    A zero nominal voltage gives NaN for every point.
    """
    if nominal_voltage == 0:
        return [math.nan] * len(voltages)
    return [(voltage - nominal_voltage) / nominal_voltage * 100 for voltage in voltages]
