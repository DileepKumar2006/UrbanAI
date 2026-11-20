"""Validate licensed image folders, deduplicate, and create stable splits.

Input: raw/<category-slug>/<image> plus a reviewed source-manifest.csv.
Only exact byte duplicates are grouped; cross-category label conflicts are excluded.
"""

import argparse
import csv
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .categories import CATEGORIES

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}
MANIFEST_COLUMNS = ('relative_path', 'source', 'license', 'license_url', 'attribution', 'permission_verified')


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def prepare_dataset(input_dir, source_manifest, output_dir, seed=20261120):
    root = Path(input_dir).resolve()
    manifest_path = Path(source_manifest).resolve()
    output = Path(output_dir).resolve()
    label_by_slug = dict((slug, label) for label, slug in CATEGORIES)
    sources = {}
    with manifest_path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        missing_columns = set(MANIFEST_COLUMNS) - set(reader.fieldnames or ())
        if missing_columns:
            raise ValueError(f'Source manifest is missing columns: {", ".join(sorted(missing_columns))}')
        for row in reader:
            relative = Path(row['relative_path'].strip())
            if relative.is_absolute() or '..' in relative.parts:
                continue
            sources[relative.as_posix()] = row

    report = {
        'seed': seed,
        'categories': [label for label, _slug in CATEGORIES],
        'sources_manifest': str(manifest_path),
        'excluded': [],
        'duplicates': [],
        'conflicting_duplicates': [],
        'splits': {split: {slug: 0 for _label, slug in CATEGORIES} for split in ('train', 'validation', 'test')},
    }
    candidates = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if not _inside(path, root):
            report['excluded'].append({'path': str(path), 'reason': 'path escapes input root'})
            continue
        relative = path.relative_to(root).as_posix()
        category_slug = path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else ''
        if category_slug not in label_by_slug:
            report['excluded'].append({'path': relative, 'reason': 'unknown category folder'})
            continue
        source = sources.get(relative)
        if not source:
            report['excluded'].append({'path': relative, 'reason': 'missing source/license manifest row'})
            continue
        required = ('source', 'license', 'permission_verified')
        verified = source.get('permission_verified', '').strip().casefold() in {'yes', 'true', '1', 'verified'}
        if not verified or any(not source.get(field, '').strip() for field in required[:-1]):
            report['excluded'].append({'path': relative, 'reason': 'source, license, or permission is not verified'})
            continue
        try:
            with Image.open(path) as image:
                ImageOps.exif_transpose(image).convert('RGB').load()
        except (UnidentifiedImageError, OSError, ValueError) as error:
            report['excluded'].append({'path': relative, 'reason': f'corrupt image ({error.__class__.__name__})'})
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        candidates.append({
            'path': path, 'relative': relative, 'slug': category_slug,
            'label': label_by_slug[category_slug], 'sha256': digest, 'source': source,
        })

    by_hash = defaultdict(list)
    for candidate in candidates:
        by_hash[candidate['sha256']].append(candidate)
    unique = []
    for digest, group in by_hash.items():
        categories = {item['slug'] for item in group}
        if len(categories) > 1:
            report['conflicting_duplicates'].append({
                'sha256': digest, 'paths': [item['relative'] for item in group],
                'categories': sorted(categories),
            })
            continue
        group.sort(key=lambda item: item['relative'])
        unique.append(group[0])
        for duplicate in group[1:]:
            report['duplicates'].append({'path': duplicate['relative'], 'kept': group[0]['relative'], 'sha256': digest})

    output.mkdir(parents=True, exist_ok=True)
    for split in ('train', 'validation', 'test'):
        for _label, slug in CATEGORIES:
            (output / split / slug).mkdir(parents=True, exist_ok=True)
    selected_manifest = []
    by_category = defaultdict(list)
    for item in unique:
        by_category[item['slug']].append(item)

    for _label, slug in CATEGORIES:
        items = by_category[slug]
        items.sort(key=lambda item: hashlib.sha256(f'{seed}:{item["sha256"]}'.encode()).hexdigest())
        count = len(items)
        if count >= 3:
            validation_count = max(1, round(count * 0.1))
            test_count = max(1, round(count * 0.1))
            while validation_count + test_count >= count:
                if validation_count >= test_count and validation_count > 1:
                    validation_count -= 1
                else:
                    test_count -= 1
            splits = {
                'train': items[:count - validation_count - test_count],
                'validation': items[count - validation_count - test_count:count - test_count],
                'test': items[count - test_count:],
            }
        elif count == 2:
            splits = {'train': items[:1], 'validation': items[1:], 'test': []}
        else:
            splits = {'train': items, 'validation': [], 'test': []}
        for split_name, members in splits.items():
            for item in members:
                filename = f'{item["sha256"][:12]}{item["path"].suffix.lower()}'
                destination = output / split_name / slug / filename
                shutil.copyfile(item['path'], destination)
                report['splits'][split_name][slug] += 1
                selected_manifest.append({
                    'split': split_name, 'category': item['label'], 'relative_path': destination.relative_to(output).as_posix(),
                    'sha256': item['sha256'], 'source': item['source']['source'],
                    'license': item['source']['license'], 'license_url': item['source'].get('license_url', ''),
                    'attribution': item['source'].get('attribution', ''),
                })
    with (output / 'dataset_manifest.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=('split', 'category', 'relative_path', 'sha256', 'source', 'license', 'license_url', 'attribution'))
        writer.writeheader()
        writer.writerows(selected_manifest)
    report['total_images'] = sum(sum(counts.values()) for counts in report['splits'].values())
    report['unique_hashes_never_cross_splits'] = len(unique)
    (output / 'preparation_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='dataset/raw')
    parser.add_argument('--source-manifest', default='dataset/source-manifest.csv')
    parser.add_argument('--output', default='artifacts/dataset-splits')
    parser.add_argument('--seed', type=int, default=20261120)
    args = parser.parse_args()
    try:
        report = prepare_dataset(args.input, args.source_manifest, args.output, args.seed)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({'total_images': report['total_images'], 'splits': report['splits'],
                      'excluded': len(report['excluded']), 'duplicates': len(report['duplicates']),
                      'conflicting_duplicates': len(report['conflicting_duplicates'])}, indent=2))


if __name__ == '__main__':
    main()
