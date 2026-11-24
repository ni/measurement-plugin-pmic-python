"""PMIC Output Voltage Accuracy Measurement."""

import logging
import pathlib
import sys
from enum import Enum

import click
import ni_measurement_plugin_sdk_service as nims
from configure_dcpower import (
    abort_channels,
    configure_load,
    configure_source,
    measure_voltage,
    power_off_dut,
    power_on_dut,
    resolve_pin,
)
from ni.protobuf.types.xydata_pb2 import DoubleXYData
from ni_measurement_plugin_sdk_service.session_management import (
    INSTRUMENT_TYPE_NI_DCPOWER,
)


class ModeOfOperation(Enum):
    """Mode of operation of the measurement."""

    Power_on_dut = 0
    Perform_measurement = 1
    Power_off_dut = 2


script_or_exe = sys.executable if getattr(sys, "frozen", False) else __file__
service_directory = pathlib.Path(script_or_exe).resolve().parent
measurement_service = nims.MeasurementService(
    service_config_path=service_directory / "OutputVoltageAccuracy_PMIC.serviceconfig",
    ui_file_paths=[service_directory / "OutputVoltageAccuracy_PMIC.measui"],
)


def perform_measurement(
    measurements: list[float], total: float, nominal_output_voltage: float
) -> tuple[float, float, float]:
    """Calculate the output voltage and its accuracy in volts and percent."""
    output_voltage = total / len(measurements)
    diff = abs(output_voltage - nominal_output_voltage)

    output_voltage_accuracy = (diff / nominal_output_voltage) * 100
    output_voltage_accuracy_mv = diff

    return output_voltage, output_voltage_accuracy_mv, output_voltage_accuracy


@measurement_service.register_measurement
# On-Off feature
@measurement_service.configuration(
    "Mode of operation",
    nims.DataType.Enum,
    ModeOfOperation.Perform_measurement,
    enum_type=ModeOfOperation,
)
# Measurement Settings
@measurement_service.configuration("Dut setup time (s)", nims.DataType.Float, 1.0)
# Aperture time is the period during which an ADC reads the voltage or current
# on a power supply or SMU
@measurement_service.configuration("Aperture time (s)", nims.DataType.Float, 0.005)
@measurement_service.configuration("Nominal output voltage (V)", nims.DataType.Float, 3.3)
# Source Settings
@measurement_service.configuration(
    "Source resource name",
    nims.DataType.IOResource,
    "PPS",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration("Source voltage level (V)", nims.DataType.Float, 6.0)
@measurement_service.configuration("Source current limit (A)", nims.DataType.Float, 25.0)
# Load Settings
@measurement_service.configuration(
    "Load resource name",
    nims.DataType.IOResource,
    "E-load",
    instrument_type=INSTRUMENT_TYPE_NI_DCPOWER,
)
@measurement_service.configuration("Load current level (A)", nims.DataType.Float, 1.0)
@measurement_service.configuration("Load voltage limit range (V)", nims.DataType.Float, 6.0)
@measurement_service.configuration("Measurement duration (s)", nims.DataType.Float, 1.0)
# configure outputs
@measurement_service.output("Load voltage vs time", nims.DataType.DoubleXYData)
@measurement_service.output("Measured output voltage(V)", nims.DataType.Float)
@measurement_service.output("Output voltage accuracy (V)", nims.DataType.Float)
@measurement_service.output("Output voltage accuracy (percent)", nims.DataType.Float)
@measurement_service.output("DUT status", nims.DataType.String)
def measure(
    mode_of_operation: ModeOfOperation,
    dut_setup_time: float,
    aperture_time: float,
    nominal_output_voltage: float,
    source_resource_name: str,
    source_voltage_level: float,
    source_current_limit: float,
    load_resource_name: str,
    load_current_level: float,
    load_voltage_limit_range: float,
    measurement_duration: float,
) -> tuple[DoubleXYData, float, float, float, str]:
    """Power the DUT on or off, or measure its output voltage accuracy."""
    load_volt_vs_time = DoubleXYData()
    output_voltage = output_voltage_accuracy_mv = output_voltage_accuracy = 0.0
    dut_status = ""

    if mode_of_operation == ModeOfOperation.Power_on_dut:
        with measurement_service.context.reserve_session(source_resource_name) as reservation:
            with reservation.initialize_nidcpower_session() as session_info:
                source_session, _, source_channel = resolve_pin(
                    [session_info], source_resource_name
                )
                supply_voltage, supply_current = power_on_dut(
                    source_session,
                    source_channel,
                    source_voltage_level,
                    source_current_limit,
                )
        dut_status = (
            f"The DUT is powered ON\nVoltage Level: {supply_voltage:.3f} V\n"
            f"Current Limit: {supply_current:.3f} A"
        )

    elif mode_of_operation == ModeOfOperation.Perform_measurement:
        pins = [source_resource_name, load_resource_name]
        with measurement_service.context.reserve_sessions(pins) as reservation:
            with reservation.initialize_nidcpower_sessions() as session_infos:
                source_session, source_device_name, source_channel = resolve_pin(
                    session_infos, source_resource_name
                )
                load_session, _, load_channel = resolve_pin(session_infos, load_resource_name)
                logging.info(
                    "Source: device=%s channel=%s | Load: channel=%s",
                    source_device_name,
                    source_channel,
                    load_channel,
                )
                try:
                    configure_source(
                        source_session,
                        source_channel,
                        source_voltage_level,
                        source_current_limit,
                        dut_setup_time,
                        aperture_time,
                    )
                    source_session.channels[source_channel].initiate()

                    configure_load(
                        load_session,
                        load_channel,
                        load_current_level,
                        load_voltage_limit_range,
                        dut_setup_time,
                        aperture_time,
                    )

                    no_of_samples_to_fetch = int(measurement_duration / aperture_time) + 1
                    measurements = measure_voltage(
                        load_session, load_channel, no_of_samples_to_fetch
                    )
                finally:
                    abort_channels(source_session, source_channel, load_session, load_channel)

        length = len(measurements)
        dt = measurement_duration / length
        total = 0.0
        for i in range(1, length + 1):
            total += measurements[i - 1]
            load_volt_vs_time.x_data.append(float(i * dt))
            load_volt_vs_time.y_data.append(measurements[i - 1])

        output_voltage, output_voltage_accuracy_mv, output_voltage_accuracy = perform_measurement(
            measurements, total, nominal_output_voltage
        )

    elif mode_of_operation == ModeOfOperation.Power_off_dut:
        pins = [source_resource_name, load_resource_name]
        with measurement_service.context.reserve_sessions(pins) as reservation:
            with reservation.initialize_nidcpower_sessions() as session_infos:
                source_session, _, source_channel = resolve_pin(session_infos, source_resource_name)
                load_session, _, load_channel = resolve_pin(session_infos, load_resource_name)
                power_off_dut(source_session, source_channel, load_session, load_channel)
        dut_status = "The DUT is powered OFF"

    return (
        load_volt_vs_time,
        output_voltage,
        output_voltage_accuracy_mv,
        output_voltage_accuracy,
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
    """Host the Output Voltage Accuracy service."""
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
