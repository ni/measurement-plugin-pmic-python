"""Efficiency and load regulation measurement using NI-DCPower channel groups."""

import logging
import math
import pathlib
import sys
from collections.abc import Generator, Sequence

import click
import ni_measurement_plugin_sdk_service as nims
from configure_ganged_dc_power import (
    GangedSession,
    ModeOfOperation,
    SweepType,
    SystemConfiguration,
    format_power_on_result,
    generate_sequence,
    get_current_limit,
)
from ni.protobuf.types.xydata_pb2 import DoubleXYData
from ni_measurement_plugin_sdk_service.session_management import INSTRUMENT_TYPE_NI_DCPOWER

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
    service_config_path=service_directory / "EfficiencyAndLoadRegulationGanged_PMIC.serviceconfig",
    ui_file_paths=[service_directory / "EfficiencyAndLoadRegulation_PMIC.measui"],
)


@measurement_service.register_measurement
@measurement_service.configuration(
    "Mode of operation",
    nims.DataType.Enum,
    ModeOfOperation.PerformMeasurement,
    enum_type=ModeOfOperation,
)
@measurement_service.configuration("DUT setup time", nims.DataType.Double, 1.0)
@measurement_service.configuration("Source delay", nims.DataType.Double, 0.005)
@measurement_service.configuration("Aperture time", nims.DataType.Double, 0.005)
@measurement_service.configuration("Nominal output voltage", nims.DataType.Double, 3.3)
@measurement_service.configuration(
    "Source master resource name",
    nims.DataType.IOResource,
    "PPS",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration(
    "Source slave resource names",
    nims.DataType.IOResourceArray1D,
    [],
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration(
    "Source system configuration",
    nims.DataType.Enum,
    SystemConfiguration.Series,
    enum_type=SystemConfiguration,
)
@measurement_service.configuration("Source current limit", nims.DataType.Double, 25.0)
@measurement_service.configuration("Source maximum power", nims.DataType.Double, 300.0)
@measurement_service.configuration("Source start voltage", nims.DataType.Double, 6.0)
@measurement_service.configuration("Source stop voltage", nims.DataType.Double, 20.0)
@measurement_service.configuration("Source voltage sweep points", nims.DataType.Int32, 4)
@measurement_service.configuration(
    "Load master resource name",
    nims.DataType.IOResource,
    "E-load",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration(
    "Load slave resource names",
    nims.DataType.IOResourceArray1D,
    [],
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration(
    "Load system configuration",
    nims.DataType.Enum,
    SystemConfiguration.Parallel,
    enum_type=SystemConfiguration,
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
    source_master_resource_name: str,
    source_slave_resource_names: Sequence[str],
    source_system_configuration: SystemConfiguration,
    source_current_limit: float,
    source_maximum_power: float,
    source_start_voltage: float,
    source_stop_voltage: float,
    source_voltage_sweep_points: int,
    load_master_resource_name: str,
    load_slave_resource_names: Sequence[str],
    load_system_configuration: SystemConfiguration,
    load_voltage_limit_range: float,
    load_sweep_type: SweepType,
    load_start_current: float,
    load_stop_current: float,
    load_current_sweep_points_or_points_per_decade: int,
) -> Generator[MeasurementResult, None, MeasurementResult]:
    """Run the selected power, efficiency, or load-regulation operation."""
    status = ""
    voltage_values: list[float] = []
    load_currents: list[float] = []
    efficiency: list[float] = []
    load_voltages: list[float] = []
    load_voltage_deviation: list[float] = []
    source_sweep_points = 0
    load_sweep_points = 0
    source_resource_names = [source_master_resource_name, *source_slave_resource_names]
    load_resource_names = [load_master_resource_name, *load_slave_resource_names]

    def result() -> MeasurementResult:
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

    if mode_of_operation != ModeOfOperation.Power_Off_DUT:
        if source_system_configuration not in (
            SystemConfiguration.Series,
            SystemConfiguration.Parallel,
        ):
            raise ValueError("Unsupported source system configuration.")
        if not all(
            math.isfinite(value)
            for value in (source_start_voltage, source_current_limit, dut_setup_time)
        ):
            raise ValueError("Source voltage, current limit, and setup time must be finite.")
        if source_current_limit <= 0 or dut_setup_time < 0:
            raise ValueError("Source current limit must be positive and setup time non-negative.")
    if mode_of_operation == ModeOfOperation.PerformMeasurement:
        if load_system_configuration not in (
            SystemConfiguration.Series,
            SystemConfiguration.Parallel,
        ):
            raise ValueError("Unsupported load system configuration.")
        values = (
            source_stop_voltage,
            source_maximum_power,
            source_delay,
            aperture_time,
            nominal_output_voltage,
            load_voltage_limit_range,
            load_start_current,
            load_stop_current,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Measurement settings must be finite.")
        if (
            source_maximum_power <= 0
            or load_voltage_limit_range <= 0
            or aperture_time <= 0
            or source_delay < 0
        ):
            raise ValueError(
                "Power, load voltage limit, and aperture must be positive; "
                "delay must be non-negative."
            )

    if mode_of_operation == ModeOfOperation.Power_On_DUT:
        if not source_master_resource_name.strip():
            raise ValueError("A source master resource is required.")
        with measurement_service.context.reserve_sessions(source_resource_names) as reservation:
            with reservation.initialize_nidcpower_sessions() as infos:
                source = GangedSession(infos, source_resource_names, source_system_configuration)
                try:
                    voltage, current = source.power_on_channel_group(
                        source_start_voltage,
                        source_current_limit,
                        dut_setup_time,
                        True,
                    )
                except BaseException as original:
                    try:
                        source.reset_channel_group()
                    except Exception as cleanup_error:
                        raise BaseExceptionGroup(
                            "Power-on and cleanup failed", [original, cleanup_error]
                        )
                    raise
        status = format_power_on_result(voltage, current)
    elif mode_of_operation in (ModeOfOperation.PerformMeasurement, ModeOfOperation.Power_Off_DUT):
        if not source_master_resource_name.strip() or not load_master_resource_name.strip():
            raise ValueError("Source and load master resources are required.")
        resources = [*source_resource_names, *load_resource_names]
        if len(set(resources)) != len(resources):
            raise ValueError("Source and load resources must be distinct.")
        if mode_of_operation == ModeOfOperation.PerformMeasurement:
            if nominal_output_voltage == 0:
                raise ValueError("Nominal output voltage must not be zero.")
            if load_sweep_type not in (SweepType.Linear, SweepType.Logarithmic):
                raise ValueError(f"Unsupported load sweep type: {load_sweep_type}")
            if load_sweep_type == SweepType.Logarithmic and load_start_current <= 0:
                raise ValueError("Logarithmic sweep start current must be positive.")
            voltage_values = generate_sequence(
                SweepType.Linear,
                source_start_voltage,
                source_stop_voltage,
                source_voltage_sweep_points,
            )
            load_values = generate_sequence(
                load_sweep_type,
                load_start_current,
                load_stop_current,
                load_current_sweep_points_or_points_per_decade,
            )
            if not voltage_values or not load_values:
                raise ValueError("Source and load sweeps must each contain at least one point.")
            source_sweep_points, load_sweep_points = len(voltage_values), len(load_values)
        with measurement_service.context.reserve_sessions(resources) as reservation:
            with reservation.initialize_nidcpower_sessions() as infos:
                source = GangedSession(infos, source_resource_names, source_system_configuration)
                load = GangedSession(infos, load_resource_names, load_system_configuration)
                source.validate_distinct_group(load)
                if mode_of_operation == ModeOfOperation.Power_Off_DUT:
                    source.power_off_channel_group(load)
                    status = "The DUT is powered off"
                else:
                    with source.cleanup_on_exit(load):
                        source.power_on_channel_group(
                            source_start_voltage,
                            get_current_limit(
                                source_start_voltage, source_current_limit, source_maximum_power
                            ),
                            dut_setup_time,
                            True,
                        )
                        load.power_on_channel_group(
                            load_voltage_limit_range,
                            load_start_current,
                            dut_setup_time,
                            False,
                        )
                        source.configure_sweep(
                            load,
                            voltage_values,
                            load_values,
                            source_current_limit,
                            source_maximum_power,
                            load_voltage_limit_range,
                            source_delay,
                            aperture_time,
                        )
                        source.initiate_sweep(load)
                        source.wait_for_sweep()
                        load.wait_for_sweep()
                        for _ in range(source_sweep_points * load_sweep_points):
                            source_voltage, source_current = source.fetch_channel_group()
                            load_voltage, load_current = load.fetch_channel_group()
                            load_currents.append(abs(load_current))
                            load_voltages.append(load_voltage)
                            source_power = source_voltage * source_current
                            efficiency.append(
                                abs(load_voltage * load_currents[-1] * 100 / source_power)
                                if source_power
                                else float("nan")
                            )
                            load_voltage_deviation.append(
                                (load_voltage - nominal_output_voltage)
                                / nominal_output_voltage
                                * 100
                            )
                            yield result()
                        status = "The measurement is performed successfully"
    else:
        raise ValueError(f"Unsupported mode of operation: {mode_of_operation}")
    return result()


@click.command
@click.option("-v", "--verbose", count=True, help="Increase logging verbosity.")
def main(verbose: int) -> None:
    """Host the measurement service."""
    level = logging.DEBUG if verbose > 1 else logging.INFO if verbose else logging.WARNING
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=level)
    with measurement_service.host_service():
        input("Press enter to close the measurement service.\n")


if __name__ == "__main__":
    main()
