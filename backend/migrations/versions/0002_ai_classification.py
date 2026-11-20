"""Add complaint category confirmation and AI analysis history.

Revision ID: 0002_ai_classification
Revises: 0001_safe_schema_baseline
"""
from alembic import op
from sqlalchemy import inspect

revision = '0002_ai_classification'
down_revision = '0001_safe_schema_baseline'
branch_labels = None
depends_on = None


def upgrade():
    from app import db
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table('issues'):
        columns = {column['name'] for column in inspector.get_columns('issues')}
        if 'confirmed_category' not in columns:
            op.add_column('issues', db.Column('confirmed_category', db.String(100)))
    # Safe for installations where the baseline create_all already saw the
    # current model, and for databases that have only been upgraded to Phase 1.
    db.metadata.create_all(bind=bind, checkfirst=True)
    inspector = inspect(bind)
    if inspector.has_table('ai_analyses'):
        indexes = {index['name'] for index in inspector.get_indexes('ai_analyses')}
        if 'ix_ai_analyses_issue_id' not in indexes:
            op.create_index('ix_ai_analyses_issue_id', 'ai_analyses', ['issue_id'], unique=False)
        if 'ix_ai_analyses_created_at' not in indexes:
            op.create_index('ix_ai_analyses_created_at', 'ai_analyses', ['created_at'], unique=False)


def downgrade():
    # Preserve report history. This additive migration is intentionally
    # forward-only for the same data-safety reason as the Phase 1 baseline.
    pass
