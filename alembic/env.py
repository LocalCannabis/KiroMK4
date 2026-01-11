"""
Alembic environment configuration for Kiro.

Supports both sync (for migrations) and async (for autogenerate) operations.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import Kiro models so Alembic can see them
from kiro.models import Base
from kiro.config import get_config

# Alembic Config object
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate
target_metadata = Base.metadata


def get_database_url() -> str:
    """Get database URL from Kiro config."""
    kiro_config = get_config()
    url = kiro_config.database.url
    
    # For sync migrations, convert async URL to sync
    # aiosqlite -> pysqlite, asyncpg -> psycopg2
    url = url.replace("+aiosqlite", "")
    url = url.replace("+asyncpg", "+psycopg2")
    
    return url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    Generates SQL script without connecting to database.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with an active connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    from sqlalchemy.ext.asyncio import create_async_engine
    
    kiro_config = get_config()
    
    connectable = create_async_engine(
        kiro_config.database.url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    Creates engine and runs migrations against actual database.
    """
    # Use sync engine for online migrations (simpler for Alembic)
    from sqlalchemy import create_engine
    
    url = get_database_url()
    
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
