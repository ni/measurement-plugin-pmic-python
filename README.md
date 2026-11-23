# PMIC - Python

 This MeasurementLink Python plugin makes measurements for Power Management IC's.
 
 The tests supplies power to the DUT and sinks power from the DUT and validates the DUT's specifications by performing measurements that are common for PMIC's.

## Key Features

   - Output Voltage Accuracy
   - Ripple
   - Line Regulation
   - Efficiency and Load Regulation

Click here for a detailed list of measurements and their functionality: [Measurement List](docs/measurements/meas-index.md)

## Plug-in Versions

The source is split into two sets of plug-ins under [source](source):

- **Measurements - IS Pro Version 24.0**: built for InstrumentStudio Pro 2024 Q1 using the `ni-measurementlink-service` package. Includes Output Voltage Accuracy, Ripple, Line Regulation, and Efficiency and Load Regulation.
- **Measurements - IS Pro Version 26.0**: built for InstrumentStudio Pro 2026 Q4 using the newer `ni-measurement-plugin-sdk-service` package. Includes Efficiency and Load Regulation and PSRR.

Use the set that matches your installed InstrumentStudio Pro version. For the mapping between InstrumentStudio versions and Measurement Plug-In SDK for Python releases (for example, InstrumentStudio 2026 Q1 corresponds to SDK 3.1.0), see the [examples version table](https://github.com/ni/measurement-plugin-python#examples). The 24.0 plug-ins predate the SDK rename, so for InstrumentStudio 2024 Q1 use the older `ni-measurementlink-service` releases listed under "Older versions" in that table.

## Hardware Dependencies

- NI Programmable Power Supply (PXIe-4151)
- NI Electronic Load (PXIe-4051)
- Digital Pattern Instrument (PXIe-6570/1) (note: the software does not include DPI or its dependencies, but it may be required to communicate with the DUT)
- NI Oscilloscope (PXI-5122) (note: required for Ripple measurement)

Tested hardware setup:
- PXIe-4151
- PXIe-4051
- PXI-5122
- PXIe-6570/1

## Software Dependencies
(*This section is applicable if you only want to use the pre-compiled plug-ins. If you want to open the source code, go to [software development](docs/sw-dev.md).*)  
Install from NI Package Manager:

- InstrumentStudio (2024 Q1 or higher)
- Measurement Link (2024 Q1 or higher; 24.0 plug-ins only)
- NI SDC Add-On (2023 Q4 or higher) (note: only if using DPI for DUT communication)
- NI-DCPower (2023 Q4 or higher)
- NI-Scope (2023 Q4 or higher) (note: required for ripple)

Download the latest NI package from the releases section of this repo or add the feed to NI Package Manager to get updates from this repo and other in this community. To use the NI Package Manager feeds, refer to this: [Subscribing to package feeds](https://github.com/NI-MeasurementLink-Plug-Ins/package-manager-feeds)

## Getting Started
When you are ready to start using the software, check out [this](docs/help.md).

## Contributing
Use the instructions in [software development](docs/sw-dev.md) for setting up a development environment and overview of the code.