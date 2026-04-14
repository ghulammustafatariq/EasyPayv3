import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

async def check():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession)
    async with Session() as session:
        result = await session.execute(text("SELECT id, phone_number, email, is_verified FROM users ORDER BY created_at DESC LIMIT 10"))
        rows = result.fetchall()
        for r in rows:
            print(f"id={r[0]}, phone={r[1]}, email={r[2]}, verified={r[3]}")
        if not rows:
            print("NO USERS IN DATABASE")
    await engine.dispose()

asyncio.run(check())
