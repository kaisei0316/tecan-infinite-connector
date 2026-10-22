"""Tests that run without the reader: python -m unittest discover tests

They replace pylabrobot's backend with a fake one, so they check this project's
own logic (filter parsing and validation, drawer state, SiLA definitions), not
the reader itself. Use tests/smoke_client.py for the real reader.
"""

import unittest

from unitelabs.cdk import Connector

from tecan_infinite_connector import InfiniteConfig
from tecan_infinite_connector.driver import FilterNotInstalledError, InfiniteDriver
from tecan_infinite_connector.features import (
    AbsorbanceReader,
    DrawerController,
    FluorescenceReader,
    InstrumentStatusProvider,
)

# Replies recorded from the real reader on 2026-10-21 (values in tenths of a nm).
FILTER_REPLIES = {
    "#EXCITATION WAVELENGTH": ["3600|4850|5350|5800"],
    "#EXCITATION BANDWIDTH": ["350|200|250|200"],
    "#EXCITATION USAGE": ["FI|FI|FI|FI"],
    "#EMISSION WAVELENGTH": ["4650|5350|5900|6200"],
    "#EMISSION BANDWIDTH": ["350|250|200|200"],
    "#EMISSION USAGE": ["FI|FI|FI|FI"],
}


class FakeIO:
    read_timeout = 30

    async def read(self, timeout=None):
        raise TimeoutError  # Nothing waiting in the USB buffer.


class FakeBackend:
    """Stands in for ExperimentalTecanInfinite200ProBackend and records what was asked of it."""

    def __init__(self):
        self.io = FakeIO()
        self.calls = []
        self.fluorescence_kwargs = None
        self.absorbance_kwargs = None

    async def setup(self):
        self.calls.append("setup")

    async def stop(self):
        self.calls.append("stop")

    async def open(self):
        self.calls.append("open")

    async def close(self, plate):
        self.calls.append("close")

    async def read_fluorescence(self, **kwargs):
        self.fluorescence_kwargs = kwargs
        return [{"data": [[float(row * 12 + column) for column in range(12)] for row in range(8)]}]

    async def read_absorbance(self, **kwargs):
        self.absorbance_kwargs = kwargs
        return [{"data": [[0.1 * column for column in range(12)] for _ in range(8)]}]

    async def _send_command(self, command, allow_timeout=False):
        return FILTER_REPLIES.get(command, ["ST"])

    async def _read_command_response(self, require_terminal=True):
        return ["ST"]

    def _reset_stream_state(self):
        pass


async def connected_driver() -> tuple[InfiniteDriver, FakeBackend]:
    reader = InfiniteDriver()
    reader.backend = FakeBackend()
    await reader.connect()
    return reader, reader.backend


class DriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_installed_filters(self):
        reader, _ = await connected_driver()

        self.assertEqual([f.wavelength_nm for f in reader.excitation_filters], [360.0, 485.0, 535.0, 580.0])
        self.assertEqual([f.bandwidth_nm for f in reader.emission_filters], [35.0, 25.0, 20.0, 20.0])
        self.assertTrue(all(f.usage == "FI" for f in reader.excitation_filters))

    async def test_rejects_uninstalled_wavelength_without_touching_reader(self):
        reader, backend = await connected_driver()

        with self.assertRaises(FilterNotInstalledError):
            await reader.read_fluorescence(600, 535, gain=100, flashes=25, integration_us=20, focal_height_mm=20)
        self.assertIsNone(backend.fluorescence_kwargs)

    async def test_read_uses_bandwidths_of_installed_filters(self):
        reader, backend = await connected_driver()

        table = await reader.read_fluorescence(485, 535, gain=100, flashes=25, integration_us=20, focal_height_mm=20)

        self.assertEqual(len(table), 8)
        self.assertEqual(backend.fluorescence_kwargs["excitation_bandwidth"], 200)  # 20 nm
        self.assertEqual(backend.fluorescence_kwargs["emission_bandwidth"], 250)  # 25 nm

    async def test_absorbance_accepts_installed_filter_with_its_bandwidth(self):
        reader, backend = await connected_driver()

        table = await reader.read_absorbance(580, flashes=25)

        self.assertEqual(len(table), 8)
        self.assertEqual(backend.absorbance_kwargs["wavelength"], 580)
        self.assertEqual(backend.absorbance_kwargs["bandwidth"], 20.0)  # nm

    async def test_absorbance_rejects_uninstalled_wavelength_without_touching_reader(self):
        reader, backend = await connected_driver()

        with self.assertRaises(FilterNotInstalledError):
            await reader.read_absorbance(600, flashes=25)
        self.assertIsNone(backend.absorbance_kwargs)

    async def test_drawer_moves_only_when_needed(self):
        reader, backend = await connected_driver()

        await reader.close_drawer()  # Already closed after connecting: no move.
        await reader.open_drawer()
        await reader.open_drawer()  # Already open: no move.
        await reader.close_drawer()

        self.assertEqual(backend.calls, ["setup", "open", "close"])
        self.assertFalse(reader.drawer_open)


class FeatureDefinitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_features_register(self):
        reader, _ = await connected_driver()
        app = Connector(InfiniteConfig())
        for feature in (DrawerController, InstrumentStatusProvider, FluorescenceReader, AbsorbanceReader):
            app.register(feature(reader))

        registered = {str(identifier) for identifier in app.sila_server.features}
        for name in ("DrawerController", "InstrumentStatusProvider", "FluorescenceReader", "AbsorbanceReader"):
            self.assertIn(f"jp.riken/platereading/{name}/v1", registered)


if __name__ == "__main__":
    unittest.main()
