"""initial schema

Revision ID: e9e0b3d14d71
Revises: 
Create Date: 2026-05-21 11:42:00.829981

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e9e0b3d14d71'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table('businesses',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('api_keys',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('key_prefix', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash')
    )
    op.create_index(op.f('ix_api_keys_business_id'), 'api_keys', ['business_id'], unique=False)
    op.create_table('customers',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('business_id', 'email', name='uq_customers_business_email')
    )
    op.create_index(op.f('ix_customers_business_id'), 'customers', ['business_id'], unique=False)
    op.create_table('webhook_endpoints',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.String(length=2000), nullable=False),
    sa.Column('signing_secret', sa.String(length=64), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_webhook_endpoints_business_id'), 'webhook_endpoints', ['business_id'], unique=False)
    op.create_table('invoices',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('state', sa.String(length=20), server_default='draft', nullable=False),
    sa.Column('total_cents', sa.BigInteger(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('draft','open','paid','void','uncollectible')", name='ck_invoices_state'),
    sa.CheckConstraint('total_cents >= 0', name='ck_invoices_total_nonneg'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_invoices_business_created', 'invoices', ['business_id', 'created_at'], unique=False)
    op.create_index('ix_invoices_business_state', 'invoices', ['business_id', 'state'], unique=False)
    op.create_index(op.f('ix_invoices_customer_id'), 'invoices', ['customer_id'], unique=False)
    op.create_table('webhook_deliveries',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('webhook_endpoint_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('last_error', sa.String(length=2000), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('pending','delivered','exhausted')", name='ck_webhook_deliveries_status'),
    sa.CheckConstraint('attempts >= 0', name='ck_webhook_deliveries_attempts_nonneg'),
    sa.ForeignKeyConstraint(['webhook_endpoint_id'], ['webhook_endpoints.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_webhook_deliveries_status_next_attempt', 'webhook_deliveries', ['status', 'next_attempt_at'], unique=False)
    op.create_table('invoice_line_items',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount_cents = quantity * unit_amount_cents', name='ck_line_items_amount_matches'),
    sa.CheckConstraint('quantity > 0', name='ck_line_items_qty_positive'),
    sa.CheckConstraint('unit_amount_cents >= 0', name='ck_line_items_unit_amount_nonneg'),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_invoice_line_items_invoice_id'), 'invoice_line_items', ['invoice_id'], unique=False)
    op.create_table('payment_attempts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('card_token', sa.String(length=100), nullable=False),
    sa.Column('psp_ref', sa.String(length=100), nullable=True),
    sa.Column('failure_code', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending','succeeded','failed')", name='ck_payment_attempts_status'),
    sa.CheckConstraint('amount_cents > 0', name='ck_payment_attempts_amount_pos'),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('invoice_id', 'idempotency_key', name='uq_payment_attempts_invoice_idem_key')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_table('payment_attempts')
    op.drop_index(op.f('ix_invoice_line_items_invoice_id'), table_name='invoice_line_items')
    op.drop_table('invoice_line_items')
    op.drop_index('ix_webhook_deliveries_status_next_attempt', table_name='webhook_deliveries')
    op.drop_table('webhook_deliveries')
    op.drop_index(op.f('ix_invoices_customer_id'), table_name='invoices')
    op.drop_index('ix_invoices_business_state', table_name='invoices')
    op.drop_index('ix_invoices_business_created', table_name='invoices')
    op.drop_table('invoices')
    op.drop_index(op.f('ix_webhook_endpoints_business_id'), table_name='webhook_endpoints')
    op.drop_table('webhook_endpoints')
    op.drop_index(op.f('ix_customers_business_id'), table_name='customers')
    op.drop_table('customers')
    op.drop_index(op.f('ix_api_keys_business_id'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_table('businesses')
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
