"""Line Regulation measurement for PMIC with ganged power supplies and electronic loads."""

import logging
import pathlib
import sys

import click
import ni_measurement_plugin_sdk_service as nims
import nidcpower
from configure_ganged_dc_power import (
    build_terminal_name,
    calculate_sweep_values,
    calculate_voltage_deviation,
    CustomTransientResponse,
    GangedSession,
    LimitSymmetry,
    resolve_pins,
    SenseMode,
    SweepType,
    SystemConfiguration,
    TransientResponse,
)
from ni.protobuf.types.xydata_pb2 import DoubleXYData
from ni_measurement_plugin_sdk_service.session_management import (
    INSTRUMENT_TYPE_NI_DCPOWER,
)

script_or_exe = sys.executable if getattr(sys, "frozen", False) else __file__
service_directory = pathlib.Path(script_or_exe).resolve().parent
measurement_service = nims.MeasurementService(
    service_config_path=service_directory / "LineRegulation_ChannelGanging.serviceconfig",
    ui_file_paths=[service_directory / "LineRegulation_ChannelGanging.measui"],
)

MeasurementResult = tuple[list[float], list[float], list[float], DoubleXYData, DoubleXYData]


def _enable_and_settle_ganged_output(ganged: GangedSession) -> None:
    """Enable the output and apply the settings for the DUT setup time."""
    ganged.set_output_enabled(True)
    ganged.configure_triggers(nidcpower.MeasureWhen.AUTOMATICALLY_AFTER_SOURCE_COMPLETE)
    ganged.commit()
    ganged.initiate()
    ganged.wait_for_event(nidcpower.Event.SOURCE_COMPLETE)
    ganged.abort()


def _configure_and_settle_source(
    source: GangedSession,
    sense: SenseMode,
    voltage_level_range: float,
    current_limit_range: float,
    current_limit_low: float,
    current_limit_high: float,
    limit_symmetry: LimitSymmetry,
    start_voltage: float,
    transient_response: TransientResponse,
    custom_response: CustomTransientResponse,
    dut_setup_time: float,
) -> None:
    """Power the DUT at the start voltage and wait for the DUT setup time."""
    source.configure_source_mode(nidcpower.SourceMode.SINGLE_POINT)
    source.configure_sense(sense)
    source.configure_output_function(nidcpower.OutputFunction.DC_VOLTAGE)
    source.configure_voltage_level_range(voltage_level_range)
    source.configure_current_limit_range(current_limit_range)
    source.configure_current_limit(current_limit_low, current_limit_high, limit_symmetry)
    source.configure_voltage_level(start_voltage)
    source.configure_source_loop(
        transient_response, custom_response, dut_setup_time, None, is_current_loop=False
    )
    _enable_and_settle_ganged_output(source)


def _configure_and_settle_load(
    load: GangedSession,
    sense: SenseMode,
    voltage_limit_low: float,
    voltage_limit_high: float,
    limit_symmetry: LimitSymmetry,
    voltage_limit_range: float,
    current_level_range: float,
    current_level: float,
    transient_response: TransientResponse,
    custom_response: CustomTransientResponse,
    dut_setup_time: float,
) -> None:
    """Apply the load current and wait for the DUT setup time."""
    load.configure_source_mode(nidcpower.SourceMode.SINGLE_POINT)
    load.configure_sense(sense)
    load.configure_output_function(nidcpower.OutputFunction.DC_CURRENT)
    load.configure_voltage_limit(voltage_limit_low, voltage_limit_high, limit_symmetry)
    load.configure_voltage_limit_range(voltage_limit_range)
    load.configure_current_level_range(current_level_range)
    load.configure_current_level(current_level)
    load.configure_source_loop(
        transient_response, custom_response, dut_setup_time, None, is_current_loop=True
    )
    _enable_and_settle_ganged_output(load)


@measurement_service.register_measurement
# Instrument pins
@measurement_service.configuration(
    "Source master resource name",
    nims.DataType.IOResource,
    "",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration(
    "Load master resource name",
    nims.DataType.IOResource,
    "",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration(
    "Source slave resource names",
    nims.DataType.IOResourceArray1D,
    [],
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration(
    "Load slave resource names",
    nims.DataType.IOResourceArray1D,
    [],
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
# Source settings
@measurement_service.configuration(
    "Source system configuration",
    nims.DataType.Enum,
    SystemConfiguration.PARALLEL,
    enum_type=SystemConfiguration,
)
@measurement_service.configuration("Source voltage level range", nims.DataType.Double, 20.0)
@measurement_service.configuration(
    "Source limit symmetry",
    nims.DataType.Enum,
    LimitSymmetry.ASYMMETRIC,
    enum_type=LimitSymmetry,
)
@measurement_service.configuration("Source current limit range", nims.DataType.Double, 25.0)
@measurement_service.configuration("Source current limit low", nims.DataType.Double, -0.6)
@measurement_service.configuration("Source current limit high", nims.DataType.Double, 25.0)
@measurement_service.configuration(
    "Source transient response",
    nims.DataType.Enum,
    TransientResponse.NORMAL,
    enum_type=TransientResponse,
)
@measurement_service.configuration(
    "Source sense", nims.DataType.Enum, SenseMode.REMOTE, enum_type=SenseMode
)
@measurement_service.configuration("Source start voltage", nims.DataType.Double, 6.0)
@measurement_service.configuration(
    "Source current sweep points or points per decade", nims.DataType.Int32, 10
)
@measurement_service.configuration("Source stop voltage", nims.DataType.Double, 14.0)
@measurement_service.configuration("Source voltage gain bandwidth", nims.DataType.Double, 2000.0)
@measurement_service.configuration(
    "Source voltage compensation frequency", nims.DataType.Double, 3530.0
)
@measurement_service.configuration("Source voltage pole-zero ratio", nims.DataType.Double, 2.0)
# Load settings
@measurement_service.configuration(
    "Load system configuration",
    nims.DataType.Enum,
    SystemConfiguration.PARALLEL,
    enum_type=SystemConfiguration,
)
@measurement_service.configuration("Load current level", nims.DataType.Double, 1.0)
@measurement_service.configuration("Load current level range", nims.DataType.Double, 80.0)
@measurement_service.configuration(
    "Load limit symmetry", nims.DataType.Enum, LimitSymmetry.SYMMETRIC, enum_type=LimitSymmetry
)
@measurement_service.configuration("Load voltage limit range", nims.DataType.Double, 6.0)
@measurement_service.configuration("Load voltage limit low", nims.DataType.Double, 0.0)
@measurement_service.configuration("Load voltage limit high", nims.DataType.Double, float("inf"))
@measurement_service.configuration(
    "Load transient response",
    nims.DataType.Enum,
    TransientResponse.NORMAL,
    enum_type=TransientResponse,
)
@measurement_service.configuration(
    "Load sense", nims.DataType.Enum, SenseMode.REMOTE, enum_type=SenseMode
)
# Measurement settings
@measurement_service.configuration("DUT setup time", nims.DataType.Double, 1.0)
@measurement_service.configuration("Source delay", nims.DataType.Double, 0.005)
# Aperture time is the ADC integration time for a voltage or current reading.
@measurement_service.configuration("Aperture time", nims.DataType.Double, 0.005)
@measurement_service.configuration("Load current gain bandwidth", nims.DataType.Double, 14000.0)
@measurement_service.configuration(
    "Load current compensation frequency", nims.DataType.Double, 180000.0
)
@measurement_service.configuration("Load current pole-zero ratio", nims.DataType.Double, 0.3)
@measurement_service.configuration("Nominal output voltage", nims.DataType.Double, 1.0)
@measurement_service.configuration(
    "Source sweep type", nims.DataType.Enum, SweepType.LOGARITHMIC, enum_type=SweepType
)
# Measurement results
@measurement_service.output("Ganged source voltages", nims.DataType.DoubleArray1D)
@measurement_service.output("Ganged load voltages", nims.DataType.DoubleArray1D)
@measurement_service.output("Load voltage deviation", nims.DataType.DoubleArray1D)
@measurement_service.output("Load voltage vs source voltage", nims.DataType.DoubleXYData)
@measurement_service.output("Load voltage deviation vs source voltage", nims.DataType.DoubleXYData)
def measure(
    source_master_resource_name: str,
    load_master_resource_name: str,
    source_slave_resource_names: list[str],
    load_slave_resource_names: list[str],
    source_system_configuration: SystemConfiguration,
    source_voltage_level_range: float,
    source_limit_symmetry: LimitSymmetry,
    source_current_limit_range: float,
    source_current_limit_low: float,
    source_current_limit_high: float,
    source_transient_response: TransientResponse,
    source_sense: SenseMode,
    source_start_voltage: float,
    source_current_sweep_points_or_points_per_decade: int,
    source_stop_voltage: float,
    source_voltage_gain_bandwidth: float,
    source_voltage_compensation_frequency: float,
    source_voltage_pole_zero_ratio: float,
    load_system_configuration: SystemConfiguration,
    load_current_level: float,
    load_current_level_range: float,
    load_limit_symmetry: LimitSymmetry,
    load_voltage_limit_range: float,
    load_voltage_limit_low: float,
    load_voltage_limit_high: float,
    load_transient_response: TransientResponse,
    load_sense: SenseMode,
    dut_setup_time: float,
    source_delay: float,
    aperture_time: float,
    load_current_gain_bandwidth: float,
    load_current_compensation_frequency: float,
    load_current_pole_zero_ratio: float,
    nominal_output_voltage: float,
    source_sweep_type: SweepType,
) -> MeasurementResult:
    """Sweep the ganged source voltage and measure the ganged load voltage."""
    if not source_master_resource_name or not load_master_resource_name:
        raise ValueError("The source and load master resource names are required.")
    voltage_values = calculate_sweep_values(
        source_sweep_type,
        source_start_voltage,
        source_stop_voltage,
        source_current_sweep_points_or_points_per_decade,
    )
    if not voltage_values:
        raise ValueError(
            "The source sweep is empty. Check the start voltage, stop voltage and sweep points."
        )
    source_pins = [source_master_resource_name, *source_slave_resource_names]
    load_pins = [load_master_resource_name, *load_slave_resource_names]
    source_response = CustomTransientResponse(
        source_voltage_gain_bandwidth,
        source_voltage_compensation_frequency,
        source_voltage_pole_zero_ratio,
    )
    load_response = CustomTransientResponse(
        load_current_gain_bandwidth,
        load_current_compensation_frequency,
        load_current_pole_zero_ratio,
    )

    pins = [pin for pin in (*source_pins, *load_pins) if pin]
    with measurement_service.context.reserve_sessions(pins) as reservation:
        with reservation.initialize_nidcpower_sessions() as session_infos:
            source = GangedSession(
                source_system_configuration, resolve_pins(session_infos, source_pins)
            )
            load = GangedSession(load_system_configuration, resolve_pins(session_infos, load_pins))
            resolved = [*source.ganged_channels, *load.ganged_channels]
            if len({(item.device, item.channel) for item in resolved}) != len(resolved):
                raise ValueError(
                    "Each source and load pin must use a different NI-DCPower channel."
                )
            try:
                _configure_and_settle_source(
                    source,
                    source_sense,
                    source_voltage_level_range,
                    source_current_limit_range,
                    source_current_limit_low,
                    source_current_limit_high,
                    source_limit_symmetry,
                    source_start_voltage,
                    source_transient_response,
                    source_response,
                    dut_setup_time,
                )
                _configure_and_settle_load(
                    load,
                    load_sense,
                    load_voltage_limit_low,
                    load_voltage_limit_high,
                    load_limit_symmetry,
                    load_voltage_limit_range,
                    load_current_level_range,
                    load_current_level,
                    load_transient_response,
                    load_response,
                    dut_setup_time,
                )

                source.configure_source_mode(nidcpower.SourceMode.SEQUENCE)
                load.configure_source_mode(nidcpower.SourceMode.SINGLE_POINT)
                source.configure_output_function(nidcpower.OutputFunction.DC_VOLTAGE)
                load.configure_output_function(nidcpower.OutputFunction.DC_CURRENT)
                source.configure_source_loop(
                    source_transient_response,
                    source_response,
                    source_delay,
                    aperture_time,
                    is_current_loop=False,
                )
                load.configure_source_loop(
                    load_transient_response,
                    load_response,
                    source_delay,
                    aperture_time,
                    is_current_loop=True,
                )
                source.set_sequence(
                    nidcpower.OutputFunction.DC_VOLTAGE, voltage_values, source_delay
                )

                # The load follows the source master's trigger and source complete event.
                load.configure_master_source_trigger(
                    build_terminal_name(source.master, "SourceTrigger")
                )
                source.configure_triggers(nidcpower.MeasureWhen.AUTOMATICALLY_AFTER_SOURCE_COMPLETE)
                load.configure_master_measure_trigger(
                    build_terminal_name(source.master, "SourceCompleteEvent")
                )
                load.configure_triggers(nidcpower.MeasureWhen.ON_MEASURE_TRIGGER)

                load.initiate()
                source.initiate()
                load.wait_for_event(nidcpower.Event.SOURCE_COMPLETE)

                sequence_timeout = len(voltage_values) * (source_delay + aperture_time) + 1.0
                for channel in source.channels:
                    channel.wait_for_event(
                        nidcpower.Event.SEQUENCE_ITERATION_COMPLETE,
                        timeout=sequence_timeout,
                    )

                source_voltages = []
                load_voltages = []
                for _ in voltage_values:
                    source_voltages.append(source.fetch_voltage())
                    load_voltages.append(load.fetch_voltage())
                load.abort()
                source.abort()
            finally:
                load.shutdown()
                source.shutdown()

    deviations = calculate_voltage_deviation(load_voltages, nominal_output_voltage)
    return (
        source_voltages,
        load_voltages,
        deviations,
        DoubleXYData(x_data=source_voltages, y_data=load_voltages),
        DoubleXYData(x_data=source_voltages, y_data=deviations),
    )


@click.command
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Enable verbose logging. Repeat to increase verbosity.",
)
def main(verbose: int) -> None:
    """Host the Line Regulation Channel Ganging (Py) service."""
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
