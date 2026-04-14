"""Verify and reset admin password."""
import asyncio
from passlib.context import CryptContext
from sqlalchemy import text
from app.core.config import settings
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

ADMIN_PHONE = "+923000000000"
EXPECTED_PASSWORD = "Admin@EasyPay123"
NEW_PASSWORD = "Admin@EasyPay123"  # reset to same known value

async def run():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession)
    async with Session() as session:
        row = await session.execute(
            text("SELECT phone_number, email, password_hash, is_active, is_verified, is_superuser FROM users WHERE is_superuser=true")
        )
        admin = row.fetchone()
        if not admin:
            print("❌ No admin user found!")
            return
        print(f"Phone  : {admin[0]}")
        print(f"Email  : {admin[1]}")
        print(f"Active : {admin[3]}")
        print(f"Verified: {admin[4]}")
        print(f"Superuser: {admin[5]}")

        match = pwd_ctx.verify(EXPECTED_PASSWORD, admin[2])
        print(f"Password '{EXPECTED_PASSWORD}' matches hash: {match}")

        if not match:
            print("⚠️  Password mismatch — resetting to Admin@EasyPay123 ...")
            new_hash = pwd_ctx.hash(NEW_PASSWORD)
            await session.execute(
                text("UPDATE users SET password_hash=:h, is_active=true, is_verified=true WHERE is_superuser=true"),
                {"h": new_hash}
            )
            await session.commit()
            print(f"✅ Password reset to: {NEW_PASSWORD}")
        else:
            print("✅ Password is correct. Ensuring account is active/verified...")
            await session.execute(
                text("UPDATE users SET is_active=true, is_verified=true WHERE is_superuser=true")
            )
            await session.commit()
            print("✅ Done.")
    await engine.dispose()

asyncio.run(run())
