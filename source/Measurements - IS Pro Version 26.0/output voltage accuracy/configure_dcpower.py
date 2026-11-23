"""Helper functions to configure and use NI-DCPower sessions."""

from collections.abc import Sequence
from typing import NamedTuple

import nidcpower
from ni_measurement_plugin_sdk_service.session_management import SessionInformation


class ResolvedPin(NamedTuple):
    """The NI-DCPower session, device name and channel that a pin maps to."""

    session: nidcpower.Session
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


def configure_dcpower_source(
    session: nidcpower.Session,
    channel_name: str,
    voltage_level: float,
    current_limit: float,
    dut_setup_time: float,
    aperture_time: float,
) -> None:
    """Configure the source SMU."""
    channel = session.channels[channel_name]
    try:
        channel.sense = nidcpower.Sense.REMOTE
        channel.source_mode = nidcpower.SourceMode.SINGLE_POINT
        channel.output_function = nidcpower.OutputFunction.DC_VOLTAGE

        channel.voltage_level = voltage_level
        channel.current_limit = current_limit

        channel.voltage_level_autorange = True
        channel.current_limit_autorange = True
        channel.source_delay = dut_setup_time
        channel.aperture_time = aperture_time
        channel.measure_when = nidcpower.MeasureWhen.ON_DEMAND

        channel.commit()

    except nidcpower.Error:
        _disable_and_reset(session, channel_name)
        raise


def configure_dcpower_load(
    session: nidcpower.Session,
    channel_name: str,
    current_level: float,
    voltage_limit_range: float,
    dut_setup_time: float,
    aperture_time: float,
) -> None:
    """Configure the load SMU."""
    channel = session.channels[channel_name]
    try:
        channel.sense = nidcpower.Sense.REMOTE
        channel.source_mode = nidcpower.SourceMode.SINGLE_POINT
        channel.output_function = nidcpower.OutputFunction.DC_CURRENT

        channel.configure_aperture_time(aperture_time, units=nidcpower.ApertureTimeUnits.SECONDS)

        channel.current_level = current_level
        channel.current_level_autorange = True
        channel.voltage_limit_range = voltage_limit_range
        channel.source_delay = dut_setup_time
        channel.aperture_time = aperture_time
        channel.measure_when = nidcpower.MeasureWhen.ON_DEMAND

        channel.commit()

    except nidcpower.Error:
        _disable_and_reset(session, channel_name)
        raise


def measure_dcpower(session: nidcpower.Session, channel_name: str) -> tuple[float, float]:
    """Measure the voltage and current."""
    channel = session.channels[channel_name]
    try:
        with channel.initiate():
            channel.wait_for_event(event_id=nidcpower.Event.SOURCE_COMPLETE, timeout=5)
            measurements = channel.measure_multiple()

        return measurements[0].voltage, measurements[0].current

    except nidcpower.Error:
        _disable_and_reset(session, channel_name)
        raise


def measure_voltage(
    session: nidcpower.Session, channel_name: str, no_of_samples_to_fetch: int
) -> list[float]:
    """Measure multiple voltage points."""
    channel = session.channels[channel_name]
    try:
        channel.measure_record_length = no_of_samples_to_fetch
        channel.measure_record_length_is_finite = True
        channel.measure_when = nidcpower.MeasureWhen.AUTOMATICALLY_AFTER_SOURCE_COMPLETE
        channel.commit()

        volts = []
        with channel.initiate():
            samples_acquired = 0
            while samples_acquired < no_of_samples_to_fetch:
                measurements = channel.fetch_multiple(count=channel.fetch_backlog)
                samples_acquired += len(measurements)
                volts.extend(measurement.voltage for measurement in measurements)

        return volts

    except nidcpower.Error:
        _disable_and_reset(session, channel_name)
        raise


def abort_channels(
    source_session: nidcpower.Session,
    source_channel_name: str,
    load_session: nidcpower.Session,
    load_channel_name: str,
) -> None:
    """Abort both instruments. The SDK closes the sessions."""
    source_session.channels[source_channel_name].abort()
    load_session.channels[load_channel_name].abort()


def power_on_dut(
    session: nidcpower.Session, channel_name: str, voltage_level: float, current_limit: float
) -> tuple[float, float]:
    """Power on the DUT with the source SMU and return the measured voltage and current."""
    channel = session.channels[channel_name]
    try:
        channel.sense = nidcpower.Sense.REMOTE
        channel.source_mode = nidcpower.SourceMode.SINGLE_POINT
        channel.output_function = nidcpower.OutputFunction.DC_VOLTAGE

        channel.voltage_level = voltage_level
        channel.current_limit = current_limit

        channel.voltage_level_autorange = True
        channel.current_limit_autorange = True

        channel.commit()
        voltage, current = measure_dcpower(session, channel_name)

        channel.abort()
        return voltage, current

    except nidcpower.Error:
        _disable_and_reset(session, channel_name)
        raise


def power_off_dut(
    source_session: nidcpower.Session,
    source_channel_name: str,
    load_session: nidcpower.Session,
    load_channel_name: str,
) -> None:
    """Disable and reset both the load and the source."""
    _disable_and_reset(load_session, load_channel_name)
    _disable_and_reset(source_session, source_channel_name)


def _disable_and_reset(session: nidcpower.Session, channel_name: str) -> None:
    channel = session.channels[channel_name]
    channel.output_enabled = False
    channel.reset()
