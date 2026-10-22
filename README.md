# Tecan Infinite 200 PRO SiLA2 connector

This connector lets you control a **Tecan Infinite 200 PRO** plate reader over **SiLA2**, the open standard for lab
instrument communication. Any SiLA2 client can then use the reader, including the UniteLabs platform.

The connector is built with UniteLabs' Python toolkit, [unitelabs-cdk](https://pypi.org/project/unitelabs-cdk/). It
talks to the reader through [pylabrobot](https://github.com/PyLabRobot/pylabrobot) over USB, so no Tecan software is
needed.

It was written for, and tested on, a filter-based **Infinite F200 PRO**. Other Infinite 200 PRO variants, such as the
monochromator-based M200 PRO, have not been tested.

## What it can do

| SiLA feature | Commands and properties | What they do |
|---|---|---|
| `DrawerController` | `OpenDrawer`, `CloseDrawer`, `DrawerOpen` | Move the plate drawer out or in, and report whether it is open |
| `InstrumentStatusProvider` | `Connected`, `Busy`, `ExcitationFilters`, `EmissionFilters` | Connection state, whether an operation is running, and the installed filters |
| `FluorescenceReader` | `ReadFluorescence` | Read fluorescence from the top of all 96 wells and return the value of each well |
| `AbsorbanceReader` | `ReadAbsorbance` | Read absorbance (OD) of all 96 wells. The wavelength must match an installed excitation filter |

`ReadFluorescence` takes these parameters:
- excitation and emission wavelength (nm)
- gain (1–255)
- number of flashes
- integration time (µs)
- focal height (mm)

It reports progress while it runs (about 80 s per plate). The wavelengths must match filters that are installed in the
reader. Any other wavelength is rejected with a `FilterNotInstalled` error before the reader moves.

**Absorbance is experimental.** `ReadAbsorbance` is built on pylabrobot's `read_absorbance`, which is written for the
M series (monochromator). The F200 PRO has filters instead, and this reader lists all four of its excitation filters
as fluorescence (`FI`) filters. On the reader this was tested on (2026-10-22), the reference measurement failed with
`ERR30: ReferencChannel: Gain:0, Counts:~50390`, so absorbance reads did not complete. The feature is included
for other F200 PRO units, for example ones with absorbance filters installed, and has not been verified end to end.

**Not supported yet:** luminescence reads, reading only some wells, and plate types other than standard
96-well plates.

## How it is organized

```
src/tecan_infinite_connector/
  __init__.py   starts everything: connects to the reader and registers the SiLA features
  features.py   the SiLA features; translates SiLA requests into driver calls
  driver.py     talks to the reader through pylabrobot; no SiLA code here
tests/
  test_driver.py    tests that run without the reader
  smoke_client.py   end-to-end test with the real reader
scripts/            use the reader directly from Python, without SiLA
  check_connection.py    connect and list the installed filters
  drawer.py              open or close the drawer
  read_fluorescence.py   read a plate and save the result as CSV
```

The reader-specific logic lives in `driver.py`, and its top comment lists the reader quirks the code works around. In
`features.py`, method names, type hints and docstrings become the SiLA definitions that clients see. See the comment at
the top of that file.

## Setup

These steps are written for Windows, which is what the reader's PC runs.

### 1. Switch the reader's USB driver to WinUSB

pylabrobot needs the generic **WinUSB** driver instead of Tecan's own.

1. Close any Tecan software, such as Magellan or i-control.
2. Open **Device Manager**.
3. Right-click **Tecan i200Pro Device** and choose **Update driver**.
4. Choose **Browse my computer**, then **Let me pick from a list**.
5. Untick **Show compatible hardware**.
6. Under **Universal Serial Bus devices**, select **WinUSB Device**.
7. Unplug and replug the reader's USB cable.

While WinUSB is installed, Tecan's software cannot use the reader. To switch back, use the same steps but pick the
Tecan driver.

### 2. Install

You need Python 3.10 or newer.

```powershell
python -m venv env
.\env\Scripts\activate
python -m pip install -e .
```

This also installs pylabrobot and unitelabs-cdk.

### 3. Create the configuration

```powershell
config create --app tecan_infinite_connector:create_app --path config.json
```

Then open `config.json` and set `"port"` under `sila_server` to a fixed number, for example `50052`.

`config.json` is specific to each machine, so it is not part of the repository. `config.example.json` shows what it
looks like.

## Running

```powershell
connector start --app tecan_infinite_connector:create_app -cfg config.json
```

The reader resets when the connector starts, which takes about 15 s. After that the SiLA server listens on the
configured port. To stop it, press `Ctrl+C`. This also pulls the drawer in.

## Testing

**Without the reader:**

```powershell
python -m unittest discover tests
```

These tests check the connector's own logic: filter parsing, wavelength checks, drawer state and feature definitions.

**With the reader:** start the connector first, then run the command below. It moves the drawer out and back in and
reads one plate, so make sure nothing is in the way of the drawer.

```powershell
python tests\smoke_client.py 127.0.0.1 50052
```

## Connecting to UniteLabs

Add the connection details from your UniteLabs workspace to `config.json`, then restart the connector:

```json
"cloud_servers": {
  "enabled": true,
  "endpoints": [{ "name": "unitelabs", "hostname": "<from UniteLabs>", "port": 443, "tls": true }]
}
```

## Known limitations

- **Experimental backend.** pylabrobot's Infinite support is experimental, and this connector uses some of its
  internal functions. A pylabrobot update could break it, so the version is pinned in `pyproject.toml`.
- **Drawer state is not measured.** `DrawerOpen` is the position this connector last moved the drawer to. It is not read
  from the reader.
- **Occasional slowdown.** If the reader stops answering a command, pylabrobot reconnects and resets it. The operation
  still completes, but about 60–70 s later than usual.
- **No security on the local network.** By default the server accepts connections from the local network without
  encryption. Before using it on a shared network, enable TLS or limit `hostname` to `127.0.0.1`.
