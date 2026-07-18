"""Create the authoritative PeerAssist M1 PostgreSQL schema.

This unpublished base revision owns only the platform tables and relationship
constraints declared in ``postgres_schema``. Downgrade deliberately leaves
unrelated M0/file-backed resources untouched.
"""

from alembic import op

from peerassist.platform.adapters.postgres_schema import (
    create_platform_schema,
    drop_platform_schema,
)

revision = "0001_platform_m1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_platform_schema(op.get_bind())


def downgrade() -> None:
    drop_platform_schema(op.get_bind())
