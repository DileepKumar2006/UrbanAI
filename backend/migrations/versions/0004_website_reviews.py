"""Create the independent, moderated website review store.

Revision ID: 0003_website_reviews
Revises: 0002_ai_classification
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_website_reviews'
down_revision = '0003_road_sections'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table('website_reviews'):
        return
    op.create_table(
        'website_reviews',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('review_text', sa.String(length=2000), nullable=False),
        sa.Column('moderation_status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('moderated_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('moderated_at', sa.DateTime()),
        sa.CheckConstraint('rating >= 1 AND rating <= 5', name='ck_website_reviews_rating_range'),
        sa.CheckConstraint("moderation_status IN ('pending', 'published', 'hidden')",
                           name='ck_website_reviews_moderation_status'),
        sa.UniqueConstraint('user_id', name='uq_website_reviews_user_id'),
    )
    op.create_index('ix_website_reviews_status_created', 'website_reviews',
                    ['moderation_status', 'created_at'], unique=False)


def downgrade():
    # Keep citizen reviews if an operator rolls the schema revision back.
    pass
