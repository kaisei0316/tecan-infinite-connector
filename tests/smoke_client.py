"""End-to-end check against a running connector and the real reader.

Usage (with the connector running):  python tests/smoke_client.py [host] [port]

This moves the drawer out and back in, and runs one full 96-well fluorescence
read (about 80 s). Make sure nothing is in the way of the drawer.
"""

import asyncio
import sys

from sila.client import Client, ClientConfig

FEATURES = "jp.riken/platereading"

READ_PARAMETERS = {
    "ExcitationWavelength": 485,
    "EmissionWavelength": 535,
    "Gain": 100,
    "Flashes": 25,
    "IntegrationTime": 20,
    "FocalHeight": 20.0,
}


async def main(host: str, port: int) -> None:
    client = Client(ClientConfig(hostname=host, port=port, tls=False))
    await client.open(timeout=10_000)  # SiLA client timeouts are in milliseconds.
    try:
        print("Server:", await client.get_server_name())
        print("Features:", await client.get_implemented_features())

        async def read_property(feature: str, name: str):
            return await client.get_unobservable_property(f"{FEATURES}/{feature}/v1/Property/{name}").read()

        async def run_command(feature: str, name: str):
            command = client.get_unobservable_command(f"{FEATURES}/{feature}/v1/Command/{name}")
            return await command.execute(timeout=120_000)

        for name in ["Connected", "Busy", "ExcitationFilters", "EmissionFilters"]:
            print(f"{name}:", await read_property("InstrumentStatusProvider", name))

        read_fluorescence = client.get_observable_command(f"{FEATURES}/FluorescenceReader/v1/Command/ReadFluorescence")

        print("\nUninstalled filter (expect FilterNotInstalled; the reader does not move):")
        try:
            execution = await read_fluorescence.execute(READ_PARAMETERS | {"ExcitationWavelength": 600})
            print("  unexpected result:", await execution.get_responses())
        except Exception as error:
            print("  ->", type(error).__name__, error)

        await run_command("DrawerController", "OpenDrawer")
        print("\nOpenDrawer -> DrawerOpen:", await read_property("DrawerController", "DrawerOpen"))
        await asyncio.sleep(2)
        await run_command("DrawerController", "CloseDrawer")
        print("CloseDrawer -> DrawerOpen:", await read_property("DrawerController", "DrawerOpen"))

        print("\nReadFluorescence Ex485/Em535 (about 80 s) ...")
        execution = await read_fluorescence.execute(READ_PARAMETERS)
        async for info in await execution.subscribe_status():
            if info.status.name in ("FINISHED_SUCCESSFULLY", "FINISHED_WITH_ERROR"):
                print("  status:", info.status.name)
                break
        result = (await execution.get_responses())["Result"]
        print("  wells:", len(result["Values"]), "| first three:", result["Values"][:3])
        print("  timestamp:", result["Timestamp"])
    finally:
        await client.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 50052
    asyncio.run(main(host, port))
