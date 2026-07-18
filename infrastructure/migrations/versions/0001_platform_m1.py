"""Create the authoritative PeerAssist M1 PostgreSQL schema.

This unpublished base revision owns only the platform tables and relationship
constraints declared in ``postgres_schema``. Downgrade deliberately leaves
unrelated M0/file-backed resources untouched.
"""

from alembic import op
from infrastructure.migrations.v0001_schema import (
    downgrade_v0001,
    upgrade_v0001,
)

revision = "0001_platform_m1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_v0001(op.get_bind())


def downgrade() -> None:
    downgrade_v0001(op.get_bind())
