"""Line regulation measurement for InstrumentStudio 2026."""

import logging
import pathlib
import sys
from collections.abc import Generator

import click
import ni_measurement_plugin_sdk_service as nims
from configure_dc_power import (
    ModeOfOperation,
    SweepType,
    build_trigger_terminal,
    configure_load,
    configure_source,
    format_power_on_result,
    generate_sequence,
    initiate_load,
    initiate_source,
    perform_measurements,
    power_off_dut,
    power_on_dut,
    reset_sessions,
    resolve_pin,
)
from ni.protobuf.types.xydata_pb2 import DoubleXYData
from ni_measurement_plugin_sdk_service.session_management import (
    INSTRUMENT_TYPE_NI_DCPOWER,
)
from nidcpower import Event

MeasurementResult = tuple[DoubleXYData, DoubleXYData, float, float, str]


script_or_exe = sys.executable if getattr(sys, "frozen", False) else __file__
service_directory = pathlib.Path(script_or_exe).resolve().parent
measurement_service = nims.MeasurementService(
    service_config_path=service_directory / "LineRegulation_PMIC.serviceconfig",
    ui_file_paths=[service_directory / "LineRegulation_PMIC.measui"],
)


@measurement_service.register_measurement
# DUT power control and measurement mode
@measurement_service.configuration(
    "Mode of operation",
    nims.DataType.Enum,
    ModeOfOperation.PerformMeasurement,
    enum_type=ModeOfOperation,
)
# Measurement settings
@measurement_service.configuration("DUT setup time (s)", nims.DataType.Double, 1.0)
@measurement_service.configuration("Source delay (s)", nims.DataType.Double, 0.005)
# Aperture time is the ADC integration time for a voltage or current reading.
@measurement_service.configuration("Aperture time (s)", nims.DataType.Double, 0.005)
@measurement_service.configuration("Nominal output voltage (V)", nims.DataType.Double, 3.3)
# Source and sweep settings
@measurement_service.configuration(
    "Source resource name",
    nims.DataType.IOResource,
    "PPS",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration("Source current limit (A)", nims.DataType.Double, 25.0)
@measurement_service.configuration(
    "Sweep type",
    nims.DataType.Enum,
    SweepType.Linear,
    enum_type=SweepType,
)
@measurement_service.configuration("Source maximum power (W)", nims.DataType.Double, 300.0)
@measurement_service.configuration("Source start voltage (V)", nims.DataType.Double, 6.0)
@measurement_service.configuration("Source stop voltage (V)", nims.DataType.Double, 20.0)
@measurement_service.configuration("Points or points per decade", nims.DataType.Int32, 10)
# Load settings
@measurement_service.configuration(
    "Load resource name",
    nims.DataType.IOResource,
    "E-load",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration("Load current level (A)", nims.DataType.Double, 1.0)
@measurement_service.configuration("Load voltage limit range (V)", nims.DataType.Double, 5.0)
# Measurement results
@measurement_service.output("Load voltage vs source voltage", nims.DataType.DoubleXYData)
@measurement_service.output("Load voltage dev vs source voltage", nims.DataType.DoubleXYData)
@measurement_service.output("Load voltage (V)", nims.DataType.Double)
@measurement_service.output("Load voltage deviation (percent)", nims.DataType.Double)
@measurement_service.output("DUT status", nims.DataType.String)
def measure(
    mode_of_operation: ModeOfOperation,
    dut_setup_time: float,
    source_delay: float,
    aperture_time: float,
    nominal_output_voltage: float,
    source_resource_name: str,
    source_current_limit: float,
    sweep_type: SweepType,
    source_maximum_power: float,
    source_start_voltage: float,
    source_stop_voltage: float,
    source_voltage_points_or_points_per_decade: int,
    load_resource_name: str,
    load_current_level: float,
    load_voltage_limit_range: float,
) -> Generator[MeasurementResult, None, MeasurementResult]:
    """Stream line-regulation results or power the DUT on or off."""
    load_voltage_vs_source_voltage = DoubleXYData()
    load_voltage_dev_vs_source_voltage = DoubleXYData()
    load_voltage = float()
    load_voltage_deviation = float()
    dut_status = ""

    if mode_of_operation == ModeOfOperation.Power_On_DUT:
        with measurement_service.context.reserve_session(source_resource_name) as reservation:
            with reservation.initialize_nidcpower_session() as session_info:
                source_session, _, source_channel = resolve_pin(
                    [session_info], source_resource_name
                )
                voltage, current = power_on_dut(
                    source_session, source_channel, source_start_voltage, source_current_limit
                )
        dut_status = format_power_on_result(voltage, current)
    elif mode_of_operation == ModeOfOperation.PerformMeasurement:
        pins = [source_resource_name, load_resource_name]
        with measurement_service.context.reserve_sessions(pins) as reservation:
            with reservation.initialize_nidcpower_sessions() as session_infos:
                source_session, source_device, source_channel = resolve_pin(
                    session_infos, source_resource_name
                )
                load_session, load_device, load_channel = resolve_pin(
                    session_infos, load_resource_name
                )
                if (source_device, source_channel) == (load_device, load_channel):
                    raise ValueError("The source and load must use different NI-DCPower channels.")
                try:
                    voltage_values = generate_sequence(
                        sweep_type,
                        source_start_voltage,
                        source_stop_voltage,
                        source_voltage_points_or_points_per_decade,
                    )
                    initiate_source(
                        source_session,
                        source_channel,
                        source_start_voltage,
                        source_current_limit,
                        source_maximum_power,
                        dut_setup_time,
                    )
                    initiate_load(
                        load_session,
                        load_channel,
                        load_current_level,
                        load_voltage_limit_range,
                        dut_setup_time,
                    )
                    configure_source(
                        source_session,
                        source_channel,
                        voltage_values,
                        source_current_limit,
                        source_maximum_power,
                        source_delay,
                        aperture_time,
                    )
                    configure_load(
                        load_session,
                        load_channel,
                        load_current_level,
                        load_voltage_limit_range,
                        aperture_time,
                        build_trigger_terminal(
                            source_device, source_channel.rsplit("/", 1)[-1], "SourceTrigger"
                        ),
                        build_trigger_terminal(
                            source_device,
                            source_channel.rsplit("/", 1)[-1],
                            "SourceCompleteEvent",
                        ),
                    )
                    load_session.channels[load_channel].initiate()
                    source_session.channels[source_channel].initiate()
                    source_session.channels[source_channel].wait_for_event(
                        event_id=Event.SEQUENCE_ITERATION_COMPLETE
                    )
                    gen = perform_measurements(
                        source_session,
                        source_channel,
                        load_session,
                        load_channel,
                        voltage_values,
                        nominal_output_voltage,
                        load_voltage_vs_source_voltage,
                        load_voltage_dev_vs_source_voltage,
                    )
                    for _ in gen:
                        load_voltage = sum(load_voltage_vs_source_voltage.y_data) / len(
                            load_voltage_vs_source_voltage.y_data
                        )
                        load_voltage_deviation = sum(
                            load_voltage_dev_vs_source_voltage.y_data
                        ) / len(load_voltage_dev_vs_source_voltage.y_data)
                        yield (
                            load_voltage_vs_source_voltage,
                            load_voltage_dev_vs_source_voltage,
                            load_voltage,
                            load_voltage_deviation,
                            dut_status,
                        )
                finally:
                    reset_sessions(source_session, source_channel, load_session, load_channel)
        dut_status = "The measurement is performed successfully"
    elif mode_of_operation == ModeOfOperation.Power_Off_DUT:
        pins = [source_resource_name, load_resource_name]
        with measurement_service.context.reserve_sessions(pins) as reservation:
            with reservation.initialize_nidcpower_sessions() as session_infos:
                source_session, _, source_channel = resolve_pin(session_infos, source_resource_name)
                load_session, _, load_channel = resolve_pin(session_infos, load_resource_name)
                power_off_dut(source_session, source_channel, load_session, load_channel)
        dut_status = "The DUT is powered off"

    return (
        load_voltage_vs_source_voltage,
        load_voltage_dev_vs_source_voltage,
        load_voltage,
        load_voltage_deviation,
        dut_status,
    )


@click.command
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Enable verbose logging. Repeat to increase verbosity.",
)
def main(verbose: int) -> None:
    """Host the Line Regulation (Py) service."""
    if verbose > 1:
        level = logging.DEBUG
    elif verbose == 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=level)

    with measurement_service.host_service():
        input("Press enter to close the measurement service.\n")


if __name__ == "__main__":
    main()
