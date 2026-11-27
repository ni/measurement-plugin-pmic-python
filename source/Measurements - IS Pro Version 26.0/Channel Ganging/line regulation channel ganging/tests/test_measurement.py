"""Tests for the ganged line regulation measurement."""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import measurement
import nidcpower
from configure_ganged_dc_power import GangedSession


class MeasurementTests(unittest.TestCase):
    """Test measurement coordination without hardware."""

    def test_waits_on_each_scoped_source_channel(self) -> None:
        """Wait for sequence completion on the driver channels, not their metadata."""
        source = MagicMock(spec=GangedSession)
        load = MagicMock(spec=GangedSession)
        source.ganged_channels = [
            SimpleNamespace(device="Dev1", channel="Dev1/0"),
            SimpleNamespace(device="Dev2", channel="Dev2/0"),
        ]
        load.ganged_channels = [SimpleNamespace(device="Dev3", channel="Dev3/0")]
        source.channels = [MagicMock(), MagicMock()]
        source.fetch_voltage.return_value = 6.0
        load.fetch_voltage.return_value = 1.0

        args = dict.fromkeys(inspect.signature(measurement.measure).parameters, 1.0)
        args.update(
            source_master_resource_name="source",
            load_master_resource_name="load",
            source_slave_resource_names=["source_slave"],
            load_slave_resource_names=[],
            source_delay=2.0,
            aperture_time=0.005,
        )
        service = MagicMock()
        with (
            patch.object(measurement, "measurement_service", service),
            patch.object(measurement, "GangedSession", side_effect=[source, load]),
            patch.object(measurement, "resolve_pins", side_effect=[[], []]),
            patch.object(measurement, "calculate_sweep_values", return_value=[6.0, 7.0]),
            patch.object(measurement, "_configure_and_settle_source"),
            patch.object(measurement, "_configure_and_settle_load"),
            patch.object(measurement, "build_terminal_name", return_value="/Dev1/Engine0/Trigger"),
        ):
            measurement.measure(**args)

        for channel in source.channels:
            channel.wait_for_event.assert_called_once_with(
                nidcpower.Event.SEQUENCE_ITERATION_COMPLETE, timeout=5.01
            )


if __name__ == "__main__":
    unittest.main()
