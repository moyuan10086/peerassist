"""Allow explicit review export lifecycle states."""

from alembic import op
from infrastructure.migrations.v0003_review_export_states import (
    downgrade_v0003,
    upgrade_v0003,
)

revision = "0003_review_export_states"
down_revision = "0002_review_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_v0003(op)


def downgrade() -> None:
    downgrade_v0003(op)
