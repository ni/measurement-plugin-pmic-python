"""Efficiency and load-regulation measurement service."""

import logging
import pathlib
import sys
from collections.abc import Generator

import click
import ni_measurement_plugin_sdk_service as nims
from configure_dc_power import (
    build_trigger_terminal,
    configure_load,
    configure_source,
    Event,
    format_power_on_result,
    generate_sequence,
    initiate_load,
    initiate_source,
    ModeOfOperation,
    perform_measurements,
    power_off_dut,
    power_on_dut,
    reset_sessions,
    resolve_pin,
    SweepType,
)
from ni.protobuf.types.xydata_pb2 import DoubleXYData
from ni_measurement_plugin_sdk_service.session_management import (
    INSTRUMENT_TYPE_NI_DCPOWER,
)

MeasurementResult = tuple[
    str,
    list[float],
    int,
    int,
    list[float],
    list[DoubleXYData],
    list[DoubleXYData],
    list[DoubleXYData],
]


def build_xy_curves(
    load_currents: list[float], values: list[float], points_per_curve: int
) -> list[DoubleXYData]:
    """Build one load current vs. value curve for each source voltage."""
    if points_per_curve < 1:
        return []
    return [
        DoubleXYData(
            x_data=load_currents[start : start + points_per_curve],
            y_data=values[start : start + points_per_curve],
        )
        for start in range(0, len(values), points_per_curve)
    ]


script_or_exe = sys.executable if getattr(sys, "frozen", False) else __file__
service_directory = pathlib.Path(script_or_exe).resolve().parent
measurement_service = nims.MeasurementService(
    service_config_path=service_directory / "EfficiencyAndLoadRegulation_PMIC.serviceconfig",
    ui_file_paths=[service_directory / "EfficiencyAndLoadRegulation_PMIC.measui"],
)


@measurement_service.register_measurement
# On-Off feature
@measurement_service.configuration(
    "Mode of operation",
    nims.DataType.Enum,
    ModeOfOperation.PerformMeasurement,
    enum_type=ModeOfOperation,
)
# Measurement Settings
@measurement_service.configuration("DUT setup time", nims.DataType.Double, 1.0)
@measurement_service.configuration("Source delay", nims.DataType.Double, 0.005)
# Aperture time is the period during which an ADC reads the voltage or current
# on a power supply or SMU
@measurement_service.configuration("Aperture time", nims.DataType.Double, 0.005)
@measurement_service.configuration("Nominal output voltage", nims.DataType.Double, 3.3)
# Source Settings
@measurement_service.configuration(
    "Source resource name",
    nims.DataType.IOResource,
    "PPS",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration("Source current limit", nims.DataType.Double, 25.0)
@measurement_service.configuration("Source maximum power", nims.DataType.Double, 300.0)
@measurement_service.configuration("Source start voltage", nims.DataType.Double, 6.0)
@measurement_service.configuration("Source stop voltage", nims.DataType.Double, 20.0)
@measurement_service.configuration("Source voltage sweep points", nims.DataType.Int32, 4)
# Load Settings
@measurement_service.configuration(
    "Load resource name",
    nims.DataType.IOResource,
    "E-load",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration("Load voltage limit range", nims.DataType.Double, 5.0)
@measurement_service.configuration(
    "Load sweep type", nims.DataType.Enum, SweepType.Linear, enum_type=SweepType
)
@measurement_service.configuration("Load start current", nims.DataType.Double, 0.1)
@measurement_service.configuration("Load stop current", nims.DataType.Double, 24.0)
@measurement_service.configuration(
    "Load current sweep points or points per decade", nims.DataType.Int32, 10
)
# outputs
@measurement_service.output("Status", nims.DataType.String)
@measurement_service.output("Voltage values", nims.DataType.DoubleArray1D)
@measurement_service.output("Source sweep points", nims.DataType.Int32)
@measurement_service.output("Load sweep points", nims.DataType.Int32)
@measurement_service.output("Load currents", nims.DataType.DoubleArray1D)
@measurement_service.output("Efficiency", nims.DataType.DoubleXYDataArray1D)
@measurement_service.output("Load voltages", nims.DataType.DoubleXYDataArray1D)
@measurement_service.output("Load voltage deviation", nims.DataType.DoubleXYDataArray1D)
def measure(
    mode_of_operation: ModeOfOperation,
    dut_setup_time: float,
    source_delay: float,
    aperture_time: float,
    nominal_output_voltage: float,
    source_resource_name: str,
    source_current_limit: float,
    source_maximum_power: float,
    source_start_voltage: float,
    source_stop_voltage: float,
    source_voltage_sweep_points: int,
    load_resource_name: str,
    load_voltage_limit_range: float,
    load_sweep_type: SweepType,
    load_start_current: float,
    load_stop_current: float,
    load_current_sweep_points_or_points_per_decade: int,
) -> Generator[MeasurementResult, None, MeasurementResult]:
    """Run the selected power, efficiency, or load-regulation operation."""
    status = ""
    voltage_values: list[float] = []
    source_sweep_points = 0
    load_sweep_points = 0
    load_currents: list[float] = []
    efficiency: list[float] = []
    load_voltages: list[float] = []
    load_voltage_deviation: list[float] = []

    if mode_of_operation == ModeOfOperation.Power_On_DUT:
        with measurement_service.context.reserve_session(source_resource_name) as reservation:
            with reservation.initialize_nidcpower_session() as session_info:
                source_session, _, source_channel = resolve_pin(
                    [session_info], source_resource_name
                )
                voltage, current = power_on_dut(
                    source_session,
                    source_channel,
                    source_start_voltage,
                    source_current_limit,
                )
        status = format_power_on_result(voltage, current)

    elif mode_of_operation == ModeOfOperation.PerformMeasurement:
        if load_sweep_type not in (SweepType.Logarithmic, SweepType.Linear):
            raise ValueError(f"{load_sweep_type} Sweep Type is not supported ")

        pins = [source_resource_name, load_resource_name]
        with measurement_service.context.reserve_sessions(pins) as reservation:
            with reservation.initialize_nidcpower_sessions() as session_infos:
                source_session, source_device_name, source_device_channel = resolve_pin(
                    session_infos, source_resource_name
                )
                load_session, _, load_device_channel = resolve_pin(
                    session_infos, load_resource_name
                )
                logging.info(
                    "Source: device=%s channel=%s | Load: channel=%s",
                    source_device_name,
                    source_device_channel,
                    load_device_channel,
                )
                try:
                    voltage_values = generate_sequence(
                        SweepType.Linear,
                        source_start_voltage,
                        source_stop_voltage,
                        source_voltage_sweep_points,
                    )
                    initiate_source(
                        source_session,
                        source_device_channel,
                        source_start_voltage,
                        source_current_limit,
                        source_maximum_power,
                        dut_setup_time,
                    )
                    initiate_load(
                        load_session,
                        load_device_channel,
                        load_start_current,
                        load_voltage_limit_range,
                        dut_setup_time,
                    )
                    load_sweep_currents = generate_sequence(
                        load_sweep_type,
                        load_start_current,
                        load_stop_current,
                        load_current_sweep_points_or_points_per_decade,
                    )

                    load_sweep_points = len(load_sweep_currents)
                    current_values = len(voltage_values) * load_sweep_currents

                    configure_source(
                        source_session,
                        source_device_channel,
                        voltage_values,
                        source_current_limit,
                        source_maximum_power,
                        load_sweep_points,
                        source_delay,
                        aperture_time,
                    )
                    configure_load(
                        load_session,
                        load_device_channel,
                        current_values,
                        load_voltage_limit_range,
                        aperture_time,
                        build_trigger_terminal(
                            source_device_name, source_device_channel, "SourceTrigger"
                        ),
                        build_trigger_terminal(
                            source_device_name,
                            source_device_channel,
                            "SourceCompleteEvent",
                        ),
                    )

                    load_session.channels[load_device_channel].initiate()
                    source_session.channels[source_device_channel].initiate()
                    load_session.channels[load_device_channel].wait_for_event(
                        event_id=Event.SEQUENCE_ENGINE_DONE
                    )
                    source_sweep_points = len(voltage_values)

                    for _ in perform_measurements(
                        source_session,
                        source_device_channel,
                        load_session,
                        load_device_channel,
                        voltage_values,
                        load_sweep_points,
                        nominal_output_voltage,
                        load_currents,
                        load_voltages,
                        efficiency,
                        load_voltage_deviation,
                    ):
                        yield (
                            status,
                            voltage_values,
                            source_sweep_points,
                            load_sweep_points,
                            load_currents,
                            build_xy_curves(load_currents, efficiency, load_sweep_points),
                            build_xy_curves(load_currents, load_voltages, load_sweep_points),
                            build_xy_curves(
                                load_currents, load_voltage_deviation, load_sweep_points
                            ),
                        )

                    status = "The measurement is performed successfully"
                finally:
                    reset_sessions(
                        source_session,
                        source_device_channel,
                        load_session,
                        load_device_channel,
                    )

    elif mode_of_operation == ModeOfOperation.Power_Off_DUT:
        pins = [source_resource_name, load_resource_name]
        with measurement_service.context.reserve_sessions(pins) as reservation:
            with reservation.initialize_nidcpower_sessions() as session_infos:
                source_session, _, source_channel = resolve_pin(session_infos, source_resource_name)
                load_session, _, load_channel = resolve_pin(session_infos, load_resource_name)
                power_off_dut(source_session, source_channel, load_session, load_channel)
        status = "The DUT is powered off"

    return (
        status,
        voltage_values,
        source_sweep_points,
        load_sweep_points,
        load_currents,
        build_xy_curves(load_currents, efficiency, load_sweep_points),
        build_xy_curves(load_currents, load_voltages, load_sweep_points),
        build_xy_curves(load_currents, load_voltage_deviation, load_sweep_points),
    )


@click.command
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Enable verbose logging. Repeat to increase verbosity.",
)
def main(verbose: int) -> None:
    """Host the measurement service."""
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

