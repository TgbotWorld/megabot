import asyncio
import types

# Python 3.11+ compatibility shim for legacy libraries (like tenacity < 6 used by mega.py)
if not hasattr(asyncio, "coroutine"):
    asyncio.coroutine = types.coroutine
