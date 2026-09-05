"""Run the materialized Task10 native host; missing configuration fails startup."""
import asyncio

from governance_host import build_host, main


if __name__ == "__main__":
    asyncio.run(main())
