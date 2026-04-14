import asyncio
from sqlalchemy import text
from app.db.base import get_async_session_context, AsyncSessionLocal

async def check():
    async with AsyncSessionLocal() as session:
        r = await session.execute(text("SELECT id, phone_number, email, substring(password_hash,1,20) as ph, is_verified FROM users WHERE phone_number='03217218258'"))
        row = r.fetchone()
        if row:
            print(f"id={row[0]}, phone={row[1]}, email={row[2]}, hash={row[3]}..., verified={row[4]}")
        else:
            print("NOT FOUND")

asyncio.run(check())
