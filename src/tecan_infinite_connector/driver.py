"""Talks to the Tecan Infinite 200 PRO through pylabrobot.

This module knows nothing about SiLA. It wraps pylabrobot's experimental
`ExperimentalTecanInfinite200ProBackend` and adds what the SiLA features need:

- Only one operation runs on the reader at a time (an `asyncio.Lock`).
- The drawer state is remembered, because the reader is not asked for it.
- The installed filters are read once at startup, and every fluorescence and
  absorbance read is checked against them.

Reader quirks this module works around (found by testing on the real reader):

- Connecting sends `INIT FORCE`, which resets the reader and pulls the drawer in.
- Closing an already closed drawer makes pylabrobot wait for a reply that never
  comes, so drawer moves are skipped when the drawer is already in place.
- Asking for a filter that is not installed makes the reader stop responding,
  so wavelengths are validated before anything is sent to the reader.
- Absorbance light comes from the excitation filter slide: the F200 PRO has no
  monochromator, so only the installed filter wavelengths and bandwidths work.

Units used by the reader's protocol: wavelengths and bandwidths are sent and
received in tenths of a nanometer (e.g. 4850 means 485.0 nm).
"""

import asyncio
import collections.abc
import dataclasses
import logging

from pylabrobot.plate_reading.tecan.infinite_backend import ExperimentalTecanInfinite200ProBackend
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb

logger = logging.getLogger(__name__)

ROWS = "ABCDEFGH"
"""Row letters of a 96-well plate, top to bottom."""

FLUORESCENCE_USAGE = "FI"
"""How the reader labels filters registered for fluorescence."""


@dataclasses.dataclass
class OpticalFilter:
    """One filter in the reader's filter slide, as reported by the reader."""

    position: int
    wavelength_nm: float
    bandwidth_nm: float
    usage: str
    """The measurement mode the filter is registered for, e.g. "FI" (fluorescence) or "ABS" (absorbance)."""


class FilterNotInstalledError(Exception):
    """No installed filter matches the requested wavelength."""


class InfiniteDriver:
    """Controls one Tecan Infinite 200 PRO connected over USB (WinUSB driver)."""

    def __init__(self, read_timeout_s: int = 60) -> None:
        self.backend = ExperimentalTecanInfinite200ProBackend()
        # How long to wait for any single reply from the reader before giving up.
        self.backend.io.read_timeout = read_timeout_s

        self._lock = asyncio.Lock()
        self.connected = False
        self.drawer_open = False
        self.excitation_filters: list[OpticalFilter] = []
        self.emission_filters: list[OpticalFilter] = []

    @property
    def busy(self) -> bool:
        """Whether an operation is currently running on the reader."""
        return self._lock.locked()

    async def connect(self) -> None:
        """Open the USB connection, reset the reader and read its installed filters."""
        async with self._lock:
            await self.backend.setup()
            self.connected = True
            self.drawer_open = False  # The reset during setup pulls the drawer in.
            await self._load_filters()

    async def disconnect(self) -> None:
        """Close the USB connection. pylabrobot pulls the drawer in as part of this."""
        async with self._lock:
            if self.connected:
                await self.backend.stop()
            self.connected = False
            self.drawer_open = False

    async def open_drawer(self) -> None:
        """Move the plate carrier out. Does nothing if it is already out."""
        async with self._lock:
            if self.drawer_open:
                return
            await self._run_on_reader(self.backend.open())
            self.drawer_open = True

    async def close_drawer(self) -> None:
        """Move the plate carrier in. Does nothing if it is already in."""
        async with self._lock:
            if not self.drawer_open:
                return
            await self._run_on_reader(self.backend.close(plate=None))
            self.drawer_open = False

    async def read_fluorescence(
        self,
        excitation_nm: int,
        emission_nm: int,
        gain: int,
        flashes: int,
        integration_us: int,
        focal_height_mm: float,
    ) -> list[list[float | None]]:
        """Read fluorescence from the top of all 96 wells.

        Returns:
            An 8 x 12 table of relative fluorescence units: one list per row (A to H),
            each holding the values for columns 1 to 12.

        Raises:
            FilterNotInstalledError: A wavelength does not match an installed filter.
                Checked before anything is sent to the reader.
        """
        excitation_filter = self._find_filter(self.excitation_filters, excitation_nm, "fluorescence excitation")
        emission_filter = self._find_filter(self.emission_filters, emission_nm, "fluorescence emission")

        # The plate definition only tells pylabrobot where the wells are. Standard
        # 96-well plates share the same layout, so this Corning definition fits them.
        plate = cor_96_wellplate_360uL_Fb(name="plate")

        async with self._lock:
            try:
                results = await self._run_on_reader(
                    self.backend.read_fluorescence(
                        plate=plate,
                        wells=[],  # Empty means all wells.
                        excitation_wavelength=excitation_nm,
                        emission_wavelength=emission_nm,
                        focal_height=focal_height_mm,
                        flashes=flashes,
                        integration_us=integration_us,
                        gain=gain,
                        # pylabrobot takes bandwidths in tenths of a nm, and they must
                        # match the installed filter or the reader stops responding.
                        excitation_bandwidth=round(excitation_filter.bandwidth_nm * 10),
                        emission_bandwidth=round(emission_filter.bandwidth_nm * 10),
                    )
                )
            finally:
                self.drawer_open = False  # Every read ends by pulling the drawer in.
        return results[0]["data"]

    async def read_absorbance(self, wavelength_nm: int, flashes: int) -> list[list[float | None]]:
        """Read the absorbance of all 96 wells, using an excitation filter as the light source.

        This is the filter-based F200 PRO, so only the wavelengths of the installed
        excitation filters are possible. The reader accepts them for absorbance even
        though they are labeled for fluorescence ("FI").

        Returns:
            An 8 x 12 table of absorbance values (OD): one list per row (A to H),
            each holding the values for columns 1 to 12.

        Raises:
            FilterNotInstalledError: The wavelength does not match an installed filter.
                Checked before anything is sent to the reader.
        """
        optical_filter = self._find_filter(self.excitation_filters, wavelength_nm, "excitation", usage=None)
        plate = cor_96_wellplate_360uL_Fb(name="plate")

        async with self._lock:
            try:
                results = await self._run_on_reader(
                    self.backend.read_absorbance(
                        plate=plate,
                        wells=[],  # Empty means all wells.
                        wavelength=wavelength_nm,
                        flashes=flashes,
                        # Unlike fluorescence, this one is in nm. It must match the installed filter.
                        bandwidth=optical_filter.bandwidth_nm,
                    )
                )
            finally:
                self.drawer_open = False  # Every read ends by pulling the drawer in.
        return results[0]["data"]

    async def _run_on_reader(self, operation: collections.abc.Awaitable):
        """Run one pylabrobot operation, keeping the drawer state correct if it fails."""
        await self._discard_leftover_replies()
        try:
            return await operation
        except TimeoutError:
            # When the reader stops answering, pylabrobot reconnects and resets the
            # reader, and the reset pulls the drawer in.
            self.drawer_open = False
            raise

    async def _discard_leftover_replies(self) -> None:
        """Throw away replies still waiting in the USB buffer.

        A reply that arrives late (for example after a drawer move) would otherwise be
        taken as the answer to the next command. This reads the USB connection directly,
        because pylabrobot's own read treats "nothing to read" as a lost connection and
        resets the reader.
        """
        leftover = bytearray()
        for _ in range(8):
            try:
                leftover += await self.backend.io.read(timeout=1)
            except TimeoutError:
                break  # Nothing left to read.
        if leftover:
            logger.info("Discarded leftover reader output: %r", bytes(leftover))
            self.backend._reset_stream_state()

    @staticmethod
    def _find_filter(
        filters: list[OpticalFilter], wavelength_nm: int, kind: str, usage: str | None = FLUORESCENCE_USAGE
    ) -> OpticalFilter:
        """Return the filter at `wavelength_nm`, or raise FilterNotInstalledError.

        Only filters labeled with `usage` count; None accepts any label.
        """
        candidates = [f for f in filters if usage is None or f.usage == usage]
        for optical_filter in candidates:
            if abs(optical_filter.wavelength_nm - wavelength_nm) < 0.5:
                return optical_filter

        installed = ", ".join(f"{f.wavelength_nm:g}/{f.bandwidth_nm:g}" for f in candidates)
        raise FilterNotInstalledError(f"No {kind} filter at {wavelength_nm} nm. Installed: {installed or 'none'}.")

    async def _load_filters(self) -> None:
        """Ask the reader which excitation and emission filters are installed.

        pylabrobot has no public method for this, so the reader's own query commands
        are sent through the backend's internal `_send_command`.
        """
        reply = await self.backend._send_command("MODE FI.TOP", allow_timeout=True)
        if reply and reply[-1].startswith("BY#T"):
            # "BY#T..." means "busy, will finish later". Wait for the final "ST" (done),
            # otherwise it would be read as the answer to the next query.
            await self.backend._read_command_response(require_terminal=True)

        self.excitation_filters = await self._query_filters("EXCITATION")
        self.emission_filters = await self._query_filters("EMISSION")
        logger.info("Installed filters: excitation %s, emission %s", self.excitation_filters, self.emission_filters)

    async def _query_filters(self, side: str) -> list[OpticalFilter]:
        """Query one side ("EXCITATION" or "EMISSION") of the filter slide.

        The reader answers each query with one value per slide position, separated by
        "|", e.g. "#EXCITATION WAVELENGTH" -> "3600|4850|5350|5800".
        """

        async def query(field: str) -> list[str]:
            reply = await self.backend._send_command(f"#{side} {field}", allow_timeout=True)
            return reply[-1].split("|") if reply else []

        wavelengths = await query("WAVELENGTH")
        bandwidths = await query("BANDWIDTH")
        usages = await query("USAGE")

        return [
            OpticalFilter(
                position=position,
                wavelength_nm=int(wavelength) / 10,
                bandwidth_nm=int(bandwidth) / 10,
                usage=usage,
            )
            for position, (wavelength, bandwidth, usage) in enumerate(zip(wavelengths, bandwidths, usages))
            if int(wavelength) > 0  # 0 marks an empty position.
        ]
