import asyncio
import argparse

from meshcore import MeshCore, EventType


async def main():     #Define the main coroutine.

    #Connect to MeshCore Radio.
    meshcore = await MeshCore.create_serial("/dev/ttyUSB0")
    
asyncio.run(main())

##Beginning of Global Functions.

