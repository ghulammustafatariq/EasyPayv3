import asyncio
import asyncpg

async def migrate():
    conn = await asyncpg.connect(
        host="localhost", port=5432, user="postgres",
        password="Mustafa@1122", database="easypay"
    )
    result = await conn.execute(
        """
        ALTER TABLE zakat_calculations
          ADD COLUMN IF NOT EXISTS stocks_value       NUMERIC(12,2) NOT NULL DEFAULT 0.00,
          ADD COLUMN IF NOT EXISTS crypto_value       NUMERIC(12,2) NOT NULL DEFAULT 0.00,
          ADD COLUMN IF NOT EXISTS property_value     NUMERIC(12,2) NOT NULL DEFAULT 0.00,
          ADD COLUMN IF NOT EXISTS other_assets       NUMERIC(12,2) NOT NULL DEFAULT 0.00,
          ADD COLUMN IF NOT EXISTS gold_rate_source   VARCHAR(10)   NOT NULL DEFAULT 'manual',
          ADD COLUMN IF NOT EXISTS silver_rate_source VARCHAR(10)   NOT NULL DEFAULT 'manual',
          ADD COLUMN IF NOT EXISTS usd_pkr_rate       NUMERIC(10,4)
        """
    )
    print("Migration result:", result)
    # Verify
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='zakat_calculations' ORDER BY ordinal_position"
    )
    print("Columns:", [r["column_name"] for r in rows])
    await conn.close()

asyncio.run(migrate())
