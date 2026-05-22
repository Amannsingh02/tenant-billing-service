"""add partial unique index on pending payment attempts

Revision ID: ec465f54d10f
Revises: e9e0b3d14d71
Create Date: 2026-05-22 14:54:20.028650

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ec465f54d10f'
down_revision: Union[str, None] = 'e9e0b3d14d71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # At most ONE pending payment_attempt per invoice. The partial WHERE
    # clause means succeeded/failed attempts don't count toward the limit —
    # only the in-flight pending ones.
    op.create_index(
        "uq_payment_attempts_one_pending_per_invoice",
        "payment_attempts",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_payment_attempts_one_pending_per_invoice",
        table_name="payment_attempts",
    )