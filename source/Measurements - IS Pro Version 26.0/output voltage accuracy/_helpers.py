"""Helper functions for the Output Voltage Accuracy measurement."""


def format_dut_info(status: str, voltage: float, current: float) -> str:
    """Format the DUT status text shown in the UI."""
    return "The DUT is powered %s\nVoltage Level: %.3f V\nCurrent Limit: %.3f A" % (
        status,
        voltage,
        current,
    )
