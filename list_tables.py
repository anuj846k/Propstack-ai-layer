import asyncio
from app.config import settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    if not settings.supabase_db_url:
        print("No DB URL")
        return
    engine = create_async_engine(settings.supabase_db_url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
        for row in result:
            print(row[0])
    await engine.dispose()

asyncio.run(main())
