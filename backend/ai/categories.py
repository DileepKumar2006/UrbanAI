"""Canonical labels shared by data preparation, training and inference."""

CATEGORIES = (
    ('Pothole / Road Damage', 'pothole_road_damage'),
    ('Garbage Overflow', 'garbage_overflow'),
    ('Drainage / Sewage Problem', 'drainage_sewage_problem'),
    ('Broken Streetlight', 'broken_streetlight'),
    ('Water Leakage', 'water_leakage'),
    ('Road Obstruction', 'road_obstruction'),
    ('Other Civic Issues', 'other_civic_issues'),
)

LABELS = tuple(label for label, _slug in CATEGORIES)
SLUGS = tuple(slug for _label, slug in CATEGORIES)
SLUG_TO_LABEL = dict((slug, label) for label, slug in CATEGORIES)

# Retained waste-report labels from the existing application.
LEGACY_WASTE_CATEGORIES = (
    'Wet Waste',
    'Dry Waste',
    'Mixed Waste',
    'Improper Waste Segregation',
    'Overflowing Garbage',
    'Uncollected Waste',
)

COMPLAINT_CATEGORIES = LABELS + LEGACY_WASTE_CATEGORIES

DEPARTMENT_RECOMMENDATIONS = {
    'Pothole / Road Damage': 'Public Works Department',
    'Garbage Overflow': 'Solid Waste Management',
    'Overflowing Garbage': 'Solid Waste Management',
    'Wet Waste': 'Solid Waste Management',
    'Dry Waste': 'Solid Waste Management',
    'Mixed Waste': 'Solid Waste Management',
    'Improper Waste Segregation': 'Solid Waste Management',
    'Uncollected Waste': 'Solid Waste Management',
    'Drainage / Sewage Problem': 'Drainage Department',
    'Broken Streetlight': 'Electrical Department',
    'Water Leakage': 'Water Supply Department',
    'Road Obstruction': 'Traffic / Roads Department',
    'Other Civic Issues': 'General Administration',
}


def issue_type_for_category(category):
    if category == 'Pothole / Road Damage':
        return 'pothole'
    if category in LEGACY_WASTE_CATEGORIES or category == 'Garbage Overflow':
        return 'waste'
    return 'civic'


def recommended_department(category):
    return DEPARTMENT_RECOMMENDATIONS.get(category)
