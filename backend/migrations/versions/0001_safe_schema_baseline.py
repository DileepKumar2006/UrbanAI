"""Adopt or bootstrap the UrbanAI schema without replacing existing data.

Revision ID: 0001_safe_schema_baseline
Revises:
"""
from alembic import op
from sqlalchemy import inspect

revision = '0001_safe_schema_baseline'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # This first migration is deliberately idempotent: on existing databases
    # it creates only absent tables, adds the one previously runtime-added
    # column, and creates missing indexes. It never rewrites complaint rows.
    from app import db
    bind = op.get_bind()
    db.metadata.create_all(bind=bind, checkfirst=True)
    inspector = inspect(bind)
    if inspector.has_table('issues'):
        columns = {column['name'] for column in inspector.get_columns('issues')}
        if 'assigned_department' not in columns:
            op.add_column('issues', db.Column('assigned_department', db.String(120)))
        existing_indexes = {index['name'] for index in inspector.get_indexes('issues')}
        for name, columns in {
            'ix_issues_status_created_at': ['status', 'created_at'],
            'ix_issues_type_status': ['issue_type', 'status'],
            'ix_issues_department_status': ['assigned_department', 'status'],
            'ix_issues_creator_created_at': ['created_by', 'created_at'],
        }.items():
            if name not in existing_indexes:
                op.create_index(name, 'issues', columns, unique=False)

def downgrade():
    # A baseline may be stamped onto a database containing live data. Dropping
    # its tables would be destructive, so rollback is intentionally forward-only.
    pass
