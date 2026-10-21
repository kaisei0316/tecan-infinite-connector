"""Open or close the reader's plate drawer.

Usage:  python scripts/drawer.py open     (moves the drawer out and leaves it out)
        python scripts/drawer.py close    (moves the drawer in)

Keep hands and objects clear of the drawer. Connecting resets the reader, which
pulls the drawer in first, so "close" works whatever state the drawer was in.
"""

import argparse
import asyncio

from tecan_infinite_connector.driver import InfiniteDriver


async def main(action: str) -> None:
    reader = InfiniteDriver()
    await reader.connect()  # The reset during connect pulls the drawer in.

    if action == "close":
        await reader.disconnect()
        print("Drawer closed.")
        return

    await reader.open_drawer()
    # A normal disconnect would pull the drawer back in, so only close the USB connection.
    await reader.backend.io.stop()
    print("Drawer open. Run 'python scripts/drawer.py close' to close it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["open", "close"])
    asyncio.run(main(parser.parse_args().action))
