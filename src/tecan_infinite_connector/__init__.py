"""UniteLabs SiLA2 connector for the Tecan Infinite 200 PRO (F200 PRO) plate reader, via pylabrobot.

`connector start --app tecan_infinite_connector:create_app` calls `create_app` below.
"""

import dataclasses

from unitelabs.cdk import Connector, ConnectorBaseConfig, SiLAServerConfig

from .driver import InfiniteDriver
from .features import AbsorbanceReader, DrawerController, FluorescenceReader, InstrumentStatusProvider

__version__ = "0.1.0"


@dataclasses.dataclass
class InfiniteConfig(ConnectorBaseConfig):
    """Connector configuration. Values in config.json override these defaults."""

    sila_server: SiLAServerConfig = dataclasses.field(
        default_factory=lambda: SiLAServerConfig(
            name="Tecan Infinite 200 PRO",
            type="PlateReader",
            description="Tecan Infinite F200 PRO plate reader (drawer control, status, fluorescence, absorbance) via pylabrobot.",
            version=__version__,
            vendor_url="https://www.riken.jp",
        )
    )


async def create_app(config: InfiniteConfig):
    """Connect to the reader, serve the SiLA features, and disconnect on shutdown.

    The CDK runs the code before `yield` at startup and the code after it at shutdown.
    """
    reader = InfiniteDriver()
    await reader.connect()

    app = Connector(config)
    app.register(DrawerController(reader))
    app.register(InstrumentStatusProvider(reader))
    app.register(FluorescenceReader(reader))
    app.register(AbsorbanceReader(reader))

    yield app

    await reader.disconnect()
