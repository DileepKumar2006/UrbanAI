"""Add road-section data collection for Phase 6A.

Revision ID: 0003_road_sections
Revises: 0002_ai_classification
"""
from alembic import op

revision = '0003_road_sections'
down_revision = '0002_ai_classification'
branch_labels = None
depends_on = None


def upgrade():
    # The legacy baseline uses current SQLAlchemy metadata to bootstrap fresh
    # installations, so these tables may already exist there. Table.create's
    # checkfirst keeps this revision additive on both fresh and upgraded DBs.
    from app import (RoadConditionObservation, RoadImage, RoadInspection,
                     RoadMaintenanceRecord, RoadSection, RoadTrafficObservation,
                     RoadWeatherObservation)
    bind = op.get_bind()
    for model in (RoadSection, RoadConditionObservation, RoadInspection,
                  RoadWeatherObservation, RoadTrafficObservation,
                  RoadMaintenanceRecord, RoadImage):
        model.__table__.create(bind=bind, checkfirst=True)


def downgrade():
    # Preserve collected road records; rollback is intentionally forward-only.
    pass
