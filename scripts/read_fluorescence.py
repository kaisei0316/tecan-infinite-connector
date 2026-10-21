"""Read fluorescence from all 96 wells and save the result as a CSV file.

Usage:  python scripts/read_fluorescence.py --ex 485 --em 535
        python scripts/read_fluorescence.py --ex 485 --em 535 --gain 80 --out results

The wavelengths must match installed filters; run scripts/check_connection.py
to list them. A read takes about 80 s and ends with the drawer closed.
"""

import argparse
import asyncio
import csv
import pathlib
import time

from tecan_infinite_connector.driver import ROWS, InfiniteDriver


async def main(args: argparse.Namespace) -> None:
    reader = InfiniteDriver()
    await reader.connect()
    try:
        print(f"Reading fluorescence Ex {args.ex} / Em {args.em} nm, gain {args.gain} (about 80 s) ...")
        table = await reader.read_fluorescence(
            excitation_nm=args.ex,
            emission_nm=args.em,
            gain=args.gain,
            flashes=args.flashes,
            integration_us=args.integration,
            focal_height_mm=args.focal_height,
        )
    finally:
        await reader.disconnect()

    print("\n     " + "".join(f"{column:>8}" for column in range(1, 13)))
    for row_letter, row_values in zip(ROWS, table):
        print(f"{row_letter:>4} " + "".join(f"{value:8.0f}" for value in row_values))

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"fluorescence_ex{args.ex}_em{args.em}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([""] + list(range(1, 13)))
        for row_letter, row_values in zip(ROWS, table):
            writer.writerow([row_letter] + row_values)
    print(f"\nSaved {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ex", type=int, required=True, help="excitation wavelength in nm")
    parser.add_argument("--em", type=int, required=True, help="emission wavelength in nm")
    parser.add_argument("--gain", type=int, default=100, help="PMT gain, 1-255 (default 100)")
    parser.add_argument("--flashes", type=int, default=25, help="flashes per well (default 25)")
    parser.add_argument("--integration", type=int, default=20, help="integration time in µs (default 20)")
    parser.add_argument("--focal-height", type=float, default=20.0, help="focal height in mm (default 20)")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("results"), help="folder for the CSV file")
    asyncio.run(main(parser.parse_args()))
