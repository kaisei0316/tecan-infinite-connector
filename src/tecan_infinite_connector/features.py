"""The SiLA features this connector offers.

Each class below becomes one SiLA feature. The CDK builds the SiLA definition
from the code itself:

- Method names become command and property names (`open_drawer` -> `OpenDrawer`).
- Type hints become SiLA data types, including units and allowed ranges.
- Docstrings become the descriptions clients see. The `Args`, `Returns` and
  `Raises` sections name the parameters, responses and errors, so keep them
  accurate when changing a method.

The features only translate between SiLA and `InfiniteDriver`; all reader
logic lives in `driver.py`.
"""

import asyncio
import dataclasses
import datetime
import time

import typing_extensions as typing

from unitelabs.cdk import sila

from . import driver
from .driver import InfiniteDriver

ORIGINATOR = "jp.riken"
"""Who defines these features; part of every feature's SiLA identifier."""

CATEGORY = "platereading"

NANOMETER = sila.constraints.Unit(label="nm", components=[sila.constraints.UnitComponent(unit="Meter")], factor=1e-9)
MICROSECOND = sila.constraints.Unit(label="us", components=[sila.constraints.UnitComponent(unit="Second")], factor=1e-6)
MILLIMETER = sila.constraints.Unit(label="mm", components=[sila.constraints.UnitComponent(unit="Meter")], factor=1e-3)

ESTIMATED_READ_SECONDS = 85.0
"""Typical duration of a full 96-well fluorescence read; only used for the progress shown to clients."""


# Errors clients can receive. The docstring is the error description they see.


class InstrumentError(Exception):
    """The instrument did not respond or reported an error."""


class FilterNotInstalled(Exception):
    """No installed fluorescence filter matches the requested wavelength."""


# Data types used in responses. The "Attributes" section describes each field to clients.


@dataclasses.dataclass
class OpticalFilter(sila.CustomDataType):
    """
    An optical filter installed in the reader's filter slide.

    Attributes:
      Position: Position of the filter in the slide, starting at 0.
      Wavelength: Center wavelength in nm.
      Bandwidth: Bandwidth in nm.
      Usage: Measurement mode the filter is registered for (e.g. FI for fluorescence, ABS for absorbance).
    """

    position: int
    wavelength: float
    bandwidth: float
    usage: str


@dataclasses.dataclass
class WellValue(sila.CustomDataType):
    """
    The measured value of a single well.

    Attributes:
      Well: Well name, e.g. A1.
      Value: Measured value.
    """

    well: str
    value: float


@dataclasses.dataclass
class FluorescenceResult(sila.CustomDataType):
    """
    The result of a fluorescence plate read.

    Attributes:
      ExcitationWavelength: Excitation wavelength in nm.
      EmissionWavelength: Emission wavelength in nm.
      Gain: PMT gain used.
      Timestamp: Completion time of the read (ISO 8601, local time with offset).
      Values: Relative fluorescence units per well, in row-major order (A1, A2, ..., H12).
    """

    excitation_wavelength: int
    emission_wavelength: int
    gain: int
    timestamp: str
    values: list[WellValue]


class DrawerController(sila.Feature):
    """Open and close the plate drawer of the Tecan Infinite 200 PRO."""

    def __init__(self, reader: InfiniteDriver):
        super().__init__(originator=ORIGINATOR, category=CATEGORY, version="1.0", maturity_level="Draft")
        self.reader = reader

    @sila.UnobservableCommand()
    async def open_drawer(self) -> None:
        """
        Move the plate carrier out of the reader. Does nothing if the drawer is already open.

        Raises:
          InstrumentError: The instrument did not respond or reported an error.
        """

        try:
            await self.reader.open_drawer()
        except Exception as error:
            raise InstrumentError(f"Opening the drawer failed: {error!r}") from error

    @sila.UnobservableCommand()
    async def close_drawer(self) -> None:
        """
        Move the plate carrier into the reader. Does nothing if the drawer is already closed.

        Raises:
          InstrumentError: The instrument did not respond or reported an error.
        """

        try:
            await self.reader.close_drawer()
        except Exception as error:
            raise InstrumentError(f"Closing the drawer failed: {error!r}") from error

    @sila.UnobservableProperty()
    async def drawer_open(self) -> bool:
        """Whether the plate drawer is currently open (as last commanded by this server)."""

        return self.reader.drawer_open


class InstrumentStatusProvider(sila.Feature):
    """Report the connection state, activity and installed filters of the Tecan Infinite 200 PRO."""

    def __init__(self, reader: InfiniteDriver):
        super().__init__(originator=ORIGINATOR, category=CATEGORY, version="1.0", maturity_level="Draft")
        self.reader = reader

    @sila.UnobservableProperty()
    async def connected(self) -> bool:
        """Whether the server is connected to the reader over USB."""

        return self.reader.connected

    @sila.UnobservableProperty()
    async def busy(self) -> bool:
        """Whether the reader is currently executing a command (drawer move or measurement)."""

        return self.reader.busy

    @sila.UnobservableProperty()
    async def excitation_filters(self) -> list[OpticalFilter]:
        """The excitation filters installed in the filter slide, as reported by the reader at startup."""

        return [_to_sila_filter(f) for f in self.reader.excitation_filters]

    @sila.UnobservableProperty()
    async def emission_filters(self) -> list[OpticalFilter]:
        """The emission filters installed in the filter slide, as reported by the reader at startup."""

        return [_to_sila_filter(f) for f in self.reader.emission_filters]


class FluorescenceReader(sila.Feature):
    """
    Fluorescence intensity (top) reads of a 96-well plate on the Tecan Infinite 200 PRO.

    Excitation and emission wavelengths must match installed filters; the filter
    bandwidths are taken from the installed filters.
    """

    def __init__(self, reader: InfiniteDriver):
        super().__init__(originator=ORIGINATOR, category=CATEGORY, version="1.0", maturity_level="Draft")
        self.reader = reader

    @sila.ObservableCommand()
    async def read_fluorescence(
        self,
        excitation_wavelength: typing.Annotated[int, NANOMETER],
        emission_wavelength: typing.Annotated[int, NANOMETER],
        gain: typing.Annotated[int, sila.constraints.MinimalInclusive(1), sila.constraints.MaximalInclusive(255)],
        flashes: typing.Annotated[int, sila.constraints.MinimalInclusive(1), sila.constraints.MaximalInclusive(100)],
        integration_time: typing.Annotated[int, MICROSECOND, sila.constraints.MinimalInclusive(20)],
        focal_height: typing.Annotated[float, MILLIMETER, sila.constraints.MinimalInclusive(0.0)],
        *,
        status: sila.Status,
    ) -> FluorescenceResult:
        """
        Read fluorescence intensity from the top of all 96 wells of the plate in the reader.

        Args:
          ExcitationWavelength: Excitation wavelength in nm; must match an installed excitation filter.
          EmissionWavelength: Emission wavelength in nm; must match an installed emission filter.
          Gain: PMT gain (1-255). Lower it if wells saturate.
          Flashes: Number of flashes per well.
          IntegrationTime: Integration time in microseconds.
          FocalHeight: Focal height (Z position) in mm. 20 mm is the pylabrobot default.

        Returns:
          Result: The measured relative fluorescence units for each well.

        Raises:
          FilterNotInstalled: No installed fluorescence filter matches the requested wavelength.
          InstrumentError: The instrument did not respond or reported an error.
        """

        # Run the read in the background so progress can be reported while it runs.
        # The reader doesn't report progress itself, so it is estimated from elapsed time.
        read = asyncio.create_task(
            self.reader.read_fluorescence(
                excitation_nm=excitation_wavelength,
                emission_nm=emission_wavelength,
                gain=gain,
                flashes=flashes,
                integration_us=integration_time,
                focal_height_mm=focal_height,
            )
        )
        start = time.monotonic()
        while not read.done():
            elapsed = time.monotonic() - start
            status.update(
                progress=min(elapsed / ESTIMATED_READ_SECONDS, 0.99),
                remaining_time=datetime.timedelta(seconds=max(ESTIMATED_READ_SECONDS - elapsed, 0)),
            )
            await asyncio.wait({read}, timeout=1)

        try:
            plate_table = read.result()
        except driver.FilterNotInstalledError as error:
            raise FilterNotInstalled(str(error)) from error
        except Exception as error:
            raise InstrumentError(f"Fluorescence read failed: {error!r}") from error

        values = [
            WellValue(well=f"{driver.ROWS[row]}{column + 1}", value=float(value))
            for row, row_values in enumerate(plate_table)
            for column, value in enumerate(row_values)
            if value is not None
        ]
        return FluorescenceResult(
            excitation_wavelength=excitation_wavelength,
            emission_wavelength=emission_wavelength,
            gain=gain,
            timestamp=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            values=values,
        )


def _to_sila_filter(optical_filter: driver.OpticalFilter) -> OpticalFilter:
    """Convert the driver's filter description into the SiLA data type."""
    return OpticalFilter(
        position=optical_filter.position,
        wavelength=optical_filter.wavelength_nm,
        bandwidth=optical_filter.bandwidth_nm,
        usage=optical_filter.usage,
    )
