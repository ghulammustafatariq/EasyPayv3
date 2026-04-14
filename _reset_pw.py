"""Reset password for a user in the database."""
import asyncio
from passlib.context import CryptContext
from sqlalchemy import text, update
from app.core.config import settings
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

async def reset():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession)
    new_hash = pwd_ctx.hash("MyPass1!")
    async with Session() as session:
        result = await session.execute(
            text("UPDATE users SET password_hash = :h WHERE phone_number = :p"),
            {"h": new_hash, "p": "03217218258"}
        )
        await session.commit()
        print(f"Updated {result.rowcount} row(s)")
    await engine.dispose()

asyncio.run(reset())
