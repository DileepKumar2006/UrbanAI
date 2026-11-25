"""Add report grouping, language metadata, verification and event history.

Revision ID: 0005_civic_intelligence
Revises: 0004_website_reviews
"""
from alembic import op
import sqlalchemy as sa

revision = '0005_civic_intelligence'
down_revision = '0004_website_reviews'
branch_labels = None
depends_on = None


def _columns(table):
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('issue_groups'):
        op.create_table(
            'issue_groups',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if not inspector.has_table('issues'):
        return
    current = _columns('issues')
    if 'group_id' not in current:
        op.add_column('issues', sa.Column('group_id', sa.Integer(), sa.ForeignKey('issue_groups.id'), nullable=True))
    if 'description_language' not in current:
        op.add_column('issues', sa.Column('description_language', sa.String(12), nullable=False, server_default='en'))
    if 'admin_translation' not in current:
        op.add_column('issues', sa.Column('admin_translation', sa.Text(), nullable=True))
    if 'verification_status' not in current:
        op.add_column('issues', sa.Column('verification_status', sa.String(24), nullable=False, server_default='pending'))
    if 'verified_at' not in current:
        op.add_column('issues', sa.Column('verified_at', sa.DateTime(), nullable=True))

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('issue_history'):
        op.create_table(
            'issue_history',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('issue_id', sa.Integer(), sa.ForeignKey('issues.id', ondelete='CASCADE'), nullable=False),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('event_type', sa.String(40), nullable=False),
            sa.Column('old_status', sa.String(50)),
            sa.Column('new_status', sa.String(50)),
            sa.Column('details', sa.String(2000), nullable=False, server_default=''),
            sa.Column('image_url', sa.String(255)),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    indexes = {index['name'] for index in sa.inspect(op.get_bind()).get_indexes('issue_history')}
    if 'ix_issue_history_issue_id' not in indexes:
        op.create_index('ix_issue_history_issue_id', 'issue_history', ['issue_id'])
    if 'ix_issue_history_created_at' not in indexes:
        op.create_index('ix_issue_history_created_at', 'issue_history', ['created_at'])
    issue_indexes = {index['name'] for index in sa.inspect(op.get_bind()).get_indexes('issues')}
    if 'ix_issues_group_id' not in issue_indexes:
        op.create_index('ix_issues_group_id', 'issues', ['group_id'])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('department_routes'):
        op.create_table(
            'department_routes',
            sa.Column('category', sa.String(100), primary_key=True),
            sa.Column('department_name', sa.String(120), nullable=False),
            sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade():
    # Preserve citizen reports, evidence and history; roll back with a database restore.
    pass
