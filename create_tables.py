import asyncio
from aurabackend.metadata_store.db import get_engine
from aurabackend.metadata_store.models import Base

async def create_all_tables():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('✓ All tables created successfully')

asyncio.run(create_all_tables())
