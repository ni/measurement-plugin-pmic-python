"""Helper functions to configure and use NI-DCPower sessions."""

from collections.abc import Sequence
from datetime import timedelta
from typing import NamedTuple

from ni_measurement_plugin_sdk_service.session_management import SessionInformation
from nidcpower import (
    ApertureTimeUnits,
    Error,
    Event,
    MeasureWhen,
    OutputFunction,
    Sense,
    Session,
    SourceMode,
)


class ResolvedPin(NamedTuple):
    """The NI-DCPower session, device name (for logging) and mapped channel selector of a pin."""

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
        raise ValueError(
            f"Pin {pin!r} must map to exactly one NI-DCPower session.channels[channel_name]."
        )
    info, mapped_channel = matches[0]
    device_name, _, _ = mapped_channel.rpartition("/")
    return ResolvedPin(info.session, device_name or info.resource_name, mapped_channel)


def configure_source(
    session: Session,
    channel_name: str,
    voltage_level: float,
    current_limit: float,
    dut_setup_time: float,
    aperture_time: float,
) -> None:
    """Configure the source SMU."""
    try:
        session.channels[channel_name].sense = Sense.REMOTE
        session.channels[channel_name].source_mode = SourceMode.SINGLE_POINT
        session.channels[channel_name].output_function = OutputFunction.DC_VOLTAGE

        session.channels[channel_name].voltage_level = voltage_level
        session.channels[channel_name].current_limit = current_limit

        session.channels[channel_name].voltage_level_autorange = True
        session.channels[channel_name].current_limit_autorange = True
        session.channels[channel_name].source_delay = dut_setup_time
        session.channels[channel_name].aperture_time = aperture_time
        session.channels[channel_name].measure_when = MeasureWhen.ON_DEMAND

        session.channels[channel_name].commit()

    except Error:
        _disable_and_reset(session, channel_name)
        raise


def configure_load(
    session: Session,
    channel_name: str,
    current_level: float,
    voltage_limit_range: float,
    dut_setup_time: float,
    aperture_time: float,
) -> None:
    """Configure the load SMU."""
    try:
        session.channels[channel_name].sense = Sense.REMOTE
        session.channels[channel_name].source_mode = SourceMode.SINGLE_POINT
        session.channels[channel_name].output_function = OutputFunction.DC_CURRENT

        session.channels[channel_name].configure_aperture_time(
            aperture_time, units=ApertureTimeUnits.SECONDS
        )

        session.channels[channel_name].current_level = current_level
        session.channels[channel_name].current_level_autorange = True
        session.channels[channel_name].voltage_limit_range = voltage_limit_range
        session.channels[channel_name].source_delay = dut_setup_time
        session.channels[channel_name].aperture_time = aperture_time
        session.channels[channel_name].measure_when = MeasureWhen.ON_DEMAND

        session.channels[channel_name].commit()

    except Error:
        _disable_and_reset(session, channel_name)
        raise


def measure_dcpower(session: Session, channel_name: str) -> tuple[float, float]:
    """Measure the voltage and current."""
    try:
        with session.channels[channel_name].initiate():
            session.channels[channel_name].wait_for_event(event_id=Event.SOURCE_COMPLETE, timeout=5)
            measurements = session.channels[channel_name].measure_multiple()

        return measurements[0].voltage, measurements[0].current

    except Error:
        _disable_and_reset(session, channel_name)
        raise


def measure_voltage(
    session: Session, channel_name: str, no_of_samples_to_fetch: int
) -> tuple[list[float], timedelta]:
    """Measure multiple voltage points.

    Returns the voltages and the time between samples reported by NI-DCPower.
    """
    try:
        session.channels[channel_name].measure_record_length = no_of_samples_to_fetch
        session.channels[channel_name].measure_record_length_is_finite = True
        session.channels[channel_name].measure_when = (
            MeasureWhen.AUTOMATICALLY_AFTER_SOURCE_COMPLETE
        )
        session.channels[channel_name].commit()
        sample_interval = session.channels[channel_name].measure_record_delta_time

        volts = []
        with session.channels[channel_name].initiate():
            samples_acquired = 0
            while samples_acquired < no_of_samples_to_fetch:
                measurements = session.channels[channel_name].fetch_multiple(
                    count=session.channels[channel_name].fetch_backlog
                )
                samples_acquired += len(measurements)
                volts.extend(measurement.voltage for measurement in measurements)

        return volts, sample_interval

    except Error:
        _disable_and_reset(session, channel_name)
        raise


def abort_channels(
    source_session: Session,
    source_channel_name: str,
    load_session: Session,
    load_channel_name: str,
) -> None:
    """Abort both instruments. The SDK closes the sessions."""
    source_session.channels[source_channel_name].abort()
    load_session.channels[load_channel_name].abort()


def power_on_dut(
    session: Session, channel_name: str, voltage_level: float, current_limit: float
) -> tuple[float, float]:
    """Power on the DUT with the source SMU and return the measured voltage and current."""
    try:
        session.channels[channel_name].sense = Sense.REMOTE
        session.channels[channel_name].source_mode = SourceMode.SINGLE_POINT
        session.channels[channel_name].output_function = OutputFunction.DC_VOLTAGE

        session.channels[channel_name].voltage_level = voltage_level
        session.channels[channel_name].current_limit = current_limit

        session.channels[channel_name].voltage_level_autorange = True
        session.channels[channel_name].current_limit_autorange = True

        session.channels[channel_name].commit()
        voltage, current = measure_dcpower(session, channel_name)

        session.channels[channel_name].abort()
        return voltage, current

    except Error:
        _disable_and_reset(session, channel_name)
        raise


def power_off_dut(
    source_session: Session,
    source_channel_name: str,
    load_session: Session,
    load_channel_name: str,
) -> None:
    """Disable and reset both the load and the source."""
    _disable_and_reset(load_session, load_channel_name)
    _disable_and_reset(source_session, source_channel_name)


def _disable_and_reset(session: Session, channel_name: str) -> None:
    session.channels[channel_name].output_enabled = False
    session.channels[channel_name].reset()
