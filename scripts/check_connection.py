"""Connect to the reader, print its installed filters, and disconnect.

Usage:  python scripts/check_connection.py

A quick check that the USB driver (WinUSB) and pylabrobot work, without SiLA.
Connecting resets the reader and pulls the drawer in, so keep the drawer clear.
"""

import asyncio

from tecan_infinite_connector.driver import InfiniteDriver


async def main() -> None:
    reader = InfiniteDriver()
    await reader.connect()
    try:
        print("Connected to the Tecan Infinite 200 PRO.\n")
        for title, filters in (("Excitation", reader.excitation_filters), ("Emission", reader.emission_filters)):
            print(f"{title} filters:")
            for f in filters:
                print(f"  position {f.position}: {f.wavelength_nm:g} nm / {f.bandwidth_nm:g} nm bandwidth ({f.usage})")
    finally:
        await reader.disconnect()
    print("\nDisconnected.")


if __name__ == "__main__":
    asyncio.run(main())
