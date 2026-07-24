"""Persist review workspace state."""

from alembic import op
from infrastructure.migrations.v0002_review_workspace import downgrade_v0002, upgrade_v0002

revision = "0002_review_workspace"
down_revision = "0001_platform_m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_v0002(op)


def downgrade() -> None:
    downgrade_v0002(op)
