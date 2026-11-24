# Setup a Development Environment
If you want to commit changes to the repo, we recommend you use the current versions of application software unless a newer version enables new features. Software should work with newer versions of these dependencies.

## Software dependencies:

Install the below packages from NIPM

- Measurement Link (2024 Q1 or higher; 24.0 plug-ins only)
- NI-DCPower (2023 Q4 or higher)
- NI-FGEN (note: required for PSRR)
- NI-Scope (2023 Q4 or higher) (note: required for PSRR and ripple)

Refer to [this](https://www.ni.com/docs/en-US/bundle/measurementlink/page/python-measurement-dependencies.html) document for python measurement dependencies.

## Setup by InstrumentStudio Version

The plug-in sets in [source](../source) target different InstrumentStudio Pro versions and use different measurement SDKs. Set up the environment for the version you are working on, and use a separate virtual environment for each set. See the [README](../README.md#plug-in-versions) for details.

### InstrumentStudio Pro 2024 (IS Pro Version 24.0)

- Python >= 3.8 (3.9 recommended)

Install the below packages using pip:

- ni-measurementlink-service
- ni-measurementlink-generator
- nidcpower
- niscope

Source: [Measurements - IS Pro Version 24.0](../source/Measurements%20-%20IS%20Pro%20Version%2024.0)

Tested with:
- Instrument Studio 2024 Q1
- TestStand 2022 Q4
- Semiconductor Device Control Add-On 2023 Q4

### InstrumentStudio Pro 2026 (IS Pro Version 26.0)

Use Python 3.10 or higher.

Install each plug-in's dependencies using pip from its directory:

```bash
pip install -r requirements.txt
```

Source: [Measurements - IS Pro Version 26.0](../source/Measurements%20-%20IS%20Pro%20Version%2026.0)

Tested with:
- Instrument Studio 2026 Q4

## Building NIPM packages
To build NIPM packages for the measurement plugin, refer to [this](build-plugin.md) document.
