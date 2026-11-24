"""TestStand support utilities for the measurement plug-in."""

import pathlib
from typing import Any


class TestStandSupport:
    """Communicate with TestStand sequence execution context."""

    _PIN_MAP_ID_VAR = "NI.MeasurementPlugIns.PinMapId"

    def __init__(self, sequence_context: Any) -> None:
        """Initialize from a TestStand SequenceContext COM object."""
        self._sequence_context = sequence_context

    def get_active_pin_map_id(self) -> str:
        """Return the pin map ID registered with the pin map service."""
        run_time_variables = self._sequence_context.Execution.RunTimeVariables
        if not run_time_variables.Exists(self._PIN_MAP_ID_VAR, 0x0):
            return ""
        return run_time_variables.GetValString(self._PIN_MAP_ID_VAR, 0x0)

    def resolve_file_path(self, file_path: str) -> str:
        """Resolve a relative path using TestStand search directories."""
        if pathlib.Path(file_path).is_absolute():
            return file_path
        (_, absolute_path, _, _, user_canceled) = self._sequence_context.Engine.FindFileEx(
            fileToFind=file_path,
            absolutePath=None,
            srchDirType=None,
            searchDirectoryIndex=None,
            userCancelled=None,
            searchContext=self._sequence_context.SequenceFile,
        )
        if user_canceled:
            raise RuntimeError("File lookup canceled by user.")
        return absolute_path

