"""Configure and run the NI-DCPower line-regulation measurement."""

from collections.abc import Generator, Sequence
from enum import Enum
from typing import NamedTuple

import hightime
from ni.protobuf.types.xydata_pb2 import DoubleXYData
from ni_measurement_plugin_sdk_service.session_management import SessionInformation
from nidcpower import (
    Error,
    Event,
    MeasurementTypes,
    MeasureWhen,
    OutputFunction,
    Sense,
    Session,
    SourceMode,
    TriggerType,
)


# Mode of operation ENUM
class ModeOfOperation(Enum):
    """Select DUT power control or measurement."""

    Power_On_DUT = 0
    PerformMeasurement = 1
    Power_Off_DUT = 2
    pass


# Sweep type indicator ENUM
class SweepType(Enum):
    """Select linear points or logarithmic points per decade."""

    Linear = 0
    Logarithmic = 1
    pass


# function to take voltage and current levels and return status string
def format_power_on_result(voltage_level: float, current_level: float) -> str:
    """Format the measured power-on voltage and current."""
    voltage_string = str(voltage_level)
    current_string = str(current_level)
    return (
        f"The DUT is powered ON\n"
        f"Voltage Level: {voltage_string[:min(len(voltage_string), voltage_string.index('.')+4)]}\n"
        f"Current Level: {current_string[:min(len(current_string), voltage_string.index('.')+4)]}"
    )


def generate_sequence(
    sweep_type: SweepType, start: float, stop: float, steps: int, with_end_points: bool = True
) -> list[float]:
    """Generate the original voltage sweep, including endpoint handling."""
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
    pass


# function to determine current limit based on power boundary
def get_current_limit(voltage_level: float, current_limit: float, power_limit: float) -> float:
    """Apply the original source power boundary and current truncation."""
    if voltage_level * current_limit > power_limit:
        value = str(float(power_limit / voltage_level))
        return float(value[: min(len(value), value.index(".") + 4)])
    return current_limit


# function to build terminal name
def build_trigger_terminal(resource_name: str, channel_name: str, event_name: str) -> str:
    """Build the NI-DCPower engine trigger terminal."""
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
    device_name, _, channel = mapped_channel.rpartition("/")
    return ResolvedPin(info.session, device_name or info.resource_name, channel)


# function to start source device and keep it in running state
def power_on_dut(
    session: Session, channel_name: str, voltage_level: float, current_limit: float
) -> tuple[float, float]:
    """Power the DUT on and return measured voltage and current."""
    try:
        # configure the session
        session.channels[channel_name].sense = Sense.REMOTE
        session.channels[channel_name].source_mode = SourceMode.SINGLE_POINT
        session.channels[channel_name].output_function = OutputFunction.DC_VOLTAGE

        session.channels[channel_name].voltage_level_autorange = True
        session.channels[channel_name].current_limit_autorange = True

        session.channels[channel_name].current_limit = current_limit
        session.channels[channel_name].voltage_level = voltage_level

        session.channels[channel_name].commit()
        session.channels[channel_name].measure_when = MeasureWhen.ON_DEMAND
        session.channels[channel_name].initiate()

        session.channels[channel_name].wait_for_event(event_id=Event.SOURCE_COMPLETE, timeout=5)
        voltage = session.channels[channel_name].measure(measurement_type=MeasurementTypes.VOLTAGE)
        current = session.channels[channel_name].measure(measurement_type=MeasurementTypes.CURRENT)

        session.channels[channel_name].abort()
        return voltage, current

    except Error:
        session.channels[channel_name].abort()
        session.output_enabled = False
        session.channels[channel_name].reset()
        raise


# function to stop both source and load devices power
def power_off_dut(
    source_session: Session,
    source_device_channel: str,
    load_session: Session,
    load_device_channel: str,
) -> None:
    """Disable and reset the source and load."""
    source_session.channels[source_device_channel].output_enabled = False
    load_session.channels[load_device_channel].output_enabled = False

    source_session.channels[source_device_channel].reset()
    load_session.channels[load_device_channel].reset()


# function to power off power supplies in case of error in perform measurement
def reset_sessions(
    source_session: Session,
    source_device_channel: str,
    load_session: Session,
    load_device_channel: str,
) -> None:
    """Abort, disable, and reset both measurement channels."""
    source_session.channels[source_device_channel].abort()
    load_session.channels[load_device_channel].abort()

    source_session.channels[source_device_channel].output_enabled = False
    load_session.channels[load_device_channel].output_enabled = False

    source_session.channels[source_device_channel].reset()
    load_session.channels[load_device_channel].reset()


# function to start source device and keep power sourcing on
def initiate_source(
    session: Session,
    channel_name: str,
    voltage_level: float,
    current_limit: float,
    power_limit: float,
    source_delay: float,
) -> None:
    """Initialize the source at the sweep's starting voltage."""
    # configure the source session
    session.channels[channel_name].sense = Sense.REMOTE
    session.channels[channel_name].source_mode = SourceMode.SINGLE_POINT
    session.channels[channel_name].output_function = OutputFunction.DC_VOLTAGE

    session.channels[channel_name].voltage_level_autorange = True
    session.channels[channel_name].current_limit_autorange = True

    session.channels[channel_name].voltage_level = voltage_level
    session.channels[channel_name].current_limit = get_current_limit(
        voltage_level, current_limit, power_limit
    )
    session.channels[channel_name].source_delay = source_delay

    session.channels[channel_name].commit()
    session.channels[channel_name].initiate()

    session.channels[channel_name].wait_for_event(event_id=Event.SOURCE_COMPLETE)
    session.channels[channel_name].abort()
    return


# function to start load device and keep power sinking on
def initiate_load(
    session: Session,
    channel_name: str,
    current_level: float,
    voltage_limit_range: float,
    source_delay: float,
) -> None:
    """Initialize the load at the configured current."""
    # configure the load session
    session.channels[channel_name].sense = Sense.REMOTE
    session.channels[channel_name].source_mode = SourceMode.SINGLE_POINT
    session.channels[channel_name].output_function = OutputFunction.DC_CURRENT

    session.channels[channel_name].current_level_autorange = True

    session.channels[channel_name].current_level = current_level
    session.channels[channel_name].voltage_limit_range = voltage_limit_range
    session.channels[channel_name].source_delay = source_delay

    session.channels[channel_name].commit()
    session.channels[channel_name].initiate()

    session.channels[channel_name].wait_for_event(event_id=Event.SOURCE_COMPLETE)
    session.channels[channel_name].abort()
    return


# function to configure source for perform measurement
def configure_source(
    session: Session,
    channel_name: str,
    voltage_levels: list[float],
    current_limit: float,
    power_limit: float,
    source_delay: float,
    aperture_time: float,
) -> None:
    """Configure the source's power-limited advanced voltage sequence."""
    # configure the source session
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
        session.channels[channel_name].create_advanced_sequence_step()
        session.channels[channel_name].voltage_level = i
        session.channels[channel_name].current_limit = get_current_limit(
            i, current_limit, power_limit
        )

    session.channels[channel_name].measure_when = MeasureWhen.AUTOMATICALLY_AFTER_SOURCE_COMPLETE
    session.channels[channel_name].aperture_time = aperture_time

    session.channels[channel_name].commit()
    return


# function to configure load for perform measurement
def configure_load(
    session: Session,
    channel_name: str,
    current_level: float,
    voltage_limit_range: float,
    aperture_time: float,
    source_terminal_name: str,
    measure_terminal_name: str,
) -> None:
    """Configure the load to follow source and measurement triggers."""
    # configure the load session
    session.channels[channel_name].sense = Sense.REMOTE
    session.channels[channel_name].source_mode = SourceMode.SINGLE_POINT
    session.channels[channel_name].output_function = OutputFunction.DC_CURRENT

    session.channels[channel_name].current_level_autorange = True
    session.channels[channel_name].voltage_limit_range = voltage_limit_range
    session.channels[channel_name].current_level = current_level

    session.channels[channel_name].source_trigger_type = TriggerType.DIGITAL_EDGE
    session.channels[channel_name].measure_trigger_type = TriggerType.DIGITAL_EDGE

    session.channels[channel_name].digital_edge_source_trigger_input_terminal = source_terminal_name
    session.channels[channel_name].measure_when = MeasureWhen.ON_MEASURE_TRIGGER
    session.channels[channel_name].aperture_time = aperture_time
    session.channels[channel_name].digital_edge_measure_trigger_input_terminal = (
        measure_terminal_name
    )

    session.channels[channel_name].commit()
    return


# function to perform measurements
def perform_measurements(
    source_session: Session,
    source_device_channel: str,
    load_session: Session,
    load_device_channel: str,
    voltage_values: list[float],
    nominal_output_voltage: float,
    load_voltage_vs_source_voltage: DoubleXYData,
    load_voltage_dev_vs_source_voltage: DoubleXYData,
) -> Generator[None, None, None]:
    """Fetch paired readings and append voltage and percentage-deviation points."""
    for _ in voltage_values:
        source_measurement = source_session.channels[source_device_channel].fetch_multiple(
            count=1, timeout=hightime.timedelta(seconds=0.1)
        )[0]
        load_measurement = load_session.channels[load_device_channel].fetch_multiple(
            count=1, timeout=hightime.timedelta(seconds=0.1)
        )[0]
        source_voltage = source_measurement.voltage
        load_voltage = load_measurement.voltage
        load_voltage_vs_source_voltage.x_data.append(source_voltage)
        load_voltage_vs_source_voltage.y_data.append(load_voltage)
        load_voltage_dev_vs_source_voltage.x_data.append(source_voltage)
        load_voltage_dev_vs_source_voltage.y_data.append(
            (load_voltage - nominal_output_voltage) * 100 / nominal_output_voltage
        )
        yield
    return
