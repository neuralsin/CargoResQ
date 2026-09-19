"""safe storage facilities, and the relay that sends cargo to one

Revision ID: 0009_storage_relay
Revises: 0008_suspect_positions
Create Date: 2026-09-19 10:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009_storage_relay'
down_revision: Union[str, None] = '0008_suspect_positions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'storage_facilities',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('operator_company_id', sa.String(length=64), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('address', sa.String(length=256), nullable=True),
        sa.Column('contact_phone', sa.String(length=32), nullable=True),
        sa.Column('refrigerated', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('min_temp_c', sa.Float(), nullable=True),
        sa.Column('max_temp_c', sa.Float(), nullable=True),
        sa.Column('hazmat_approved', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('capacity_m3', sa.Float(), nullable=False),
        sa.Column('available_m3', sa.Float(), nullable=False),
        sa.Column('handling_fee_inr', sa.Float(), server_default=sa.text('0'), nullable=False),
        sa.Column('storage_fee_inr_per_m3_day', sa.Float(), server_default=sa.text('0'), nullable=False),
        sa.Column('open_24h', sa.Boolean(), server_default=sa.text('1'), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('capacity_m3 > 0', name='chk_facility_capacity_positive'),
        sa.ForeignKeyConstraint(['operator_company_id'], ['companies.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_storage_facilities_operator_company_id'),
        'storage_facilities',
        ['operator_company_id'],
    )

    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('relay_facility_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('relay_reason', sa.String(length=256), nullable=True))
        batch_op.add_column(
            sa.Column('relay_committed_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            batch_op.f('ix_incidents_relay_facility_id'), ['relay_facility_id']
        )


def downgrade() -> None:
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_incidents_relay_facility_id'))
        batch_op.drop_column('relay_committed_at')
        batch_op.drop_column('relay_reason')
        batch_op.drop_column('relay_facility_id')

    op.drop_index(
        op.f('ix_storage_facilities_operator_company_id'), table_name='storage_facilities'
    )
    op.drop_table('storage_facilities')
