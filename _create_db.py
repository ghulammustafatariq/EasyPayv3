import asyncpg
import asyncio

async def main():
    conn = await asyncpg.connect(
        host="localhost", port=5432,
        user="postgres", password="Mustafa@1122",
        database="postgres"
    )
    exists = await conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname=$1", "easypay"
    )
    if exists:
        print("Database 'easypay' already exists.")
    else:
        await conn.execute("CREATE DATABASE easypay")
        print("Database 'easypay' created successfully.")
    await conn.close()

asyncio.run(main())
