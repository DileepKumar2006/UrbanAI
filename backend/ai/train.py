"""Fine-tune MobileNetV3-Small and save measured test metrics."""

import argparse
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

from .categories import CATEGORIES, SLUGS


def _load_libraries():
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, Dataset
        from torchvision import transforms
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small
        from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
    except ImportError as error:
        raise RuntimeError('Install backend/requirements-ai.txt before training.') from error
    return (torch, nn, DataLoader, Dataset, transforms, MobileNet_V3_Small_Weights,
            mobilenet_v3_small, accuracy_score, confusion_matrix, precision_recall_fscore_support)


def _calibrate_thresholds(probabilities, targets, minimum_examples=10):
    import numpy as np

    probabilities = np.asarray(probabilities)
    targets = np.asarray(targets)
    confidence = probabilities.max(axis=1)
    correct = probabilities.argmax(axis=1) == targets

    def threshold_for(target_precision):
        for threshold in np.arange(0.50, 1.001, 0.01):
            selected = confidence >= threshold
            if int(selected.sum()) >= minimum_examples and float(correct[selected].mean()) >= target_precision:
                return round(float(threshold), 2)
        return None

    medium = threshold_for(0.70)
    high = threshold_for(0.90)
    validated = len(targets) >= minimum_examples and medium is not None and high is not None
    return {
        'medium': medium if medium is not None else 1.0,
        'high': high if high is not None else 1.0,
        'validated': validated,
        'validation_examples': int(len(targets)),
        'minimum_examples_per_precision_check': minimum_examples,
        'target_precision': {'medium': 0.70, 'high': 0.90},
    }


def train(data_root, output_dir, model_version=None, epochs=15, batch_size=32, learning_rate=1e-4):
    (torch, nn, DataLoader, Dataset, transforms, weights_enum, mobilenet_v3_small,
     accuracy_score, confusion_matrix, precision_recall_fscore_support) = _load_libraries()
    root = Path(data_root).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'dataset_manifest.csv'
    dataset_reference = hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
    slug_to_index = {slug: index for index, slug in enumerate(SLUGS)}

    class SplitDataset(Dataset):
        def __init__(self, split, transform):
            self.transform = transform
            manifest_path = root / 'dataset_manifest.csv'
            import csv
            with manifest_path.open(newline='', encoding='utf-8') as handle:
                self.items = [row for row in csv.DictReader(handle) if row['split'] == split]
            self.items = [row for row in self.items if row['category'] in {label for label, _slug in CATEGORIES}]
            if not self.items:
                raise RuntimeError(f'The {split} split is empty. Add reviewed, licensed images before training.')

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            item = self.items[index]
            path = root / item['relative_path']
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
            category = item['category']
            label = next(index for index, (name, _slug) in enumerate(CATEGORIES) if name == category)
            return self.transform(image), label

    augmentation = transforms.Compose([
        transforms.Resize(256), transforms.RandomResizedCrop(224),
        transforms.RandomHorizontalFlip(), transforms.RandomRotation(8),
        transforms.ColorJitter(brightness=0.12, contrast=0.12, saturation=0.08),
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
    ])
    evaluation_transform = transforms.Compose([
        transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(),
        transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
    ])
    train_set = SplitDataset('train', augmentation)
    validation_set = SplitDataset('validation', evaluation_transform)
    test_set = SplitDataset('test', evaluation_transform)
    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=0)
    validation_loader = DataLoader(validation_set, batch_size=batch_size, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_set, batch_size=batch_size, shuffle=False, num_workers=0)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = mobilenet_v3_small(weights=weights_enum.DEFAULT)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(CATEGORIES))
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1))
    criterion = nn.CrossEntropyLoss()

    def collect(loader, compute_loss=False):
        model.eval()
        all_probabilities, all_targets, losses = [], [], []
        with torch.inference_mode():
            for images, targets in loader:
                images, targets = images.to(device), targets.to(device)
                logits = model(images)
                if compute_loss:
                    losses.append(float(criterion(logits, targets).item()))
                all_probabilities.extend(torch.softmax(logits, dim=1).cpu().tolist())
                all_targets.extend(targets.cpu().tolist())
        mean_loss = sum(losses) / len(losses) if losses else None
        return all_probabilities, all_targets, mean_loss

    best_state, best_validation_accuracy = None, -1.0
    history = []
    for epoch in range(epochs):
        model.train()
        running_loss, seen = 0.0, 0
        for images, targets in train_loader:
            images, targets = images.to(device), targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(images), targets)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.item()) * len(targets)
            seen += len(targets)
        scheduler.step()
        val_probs, val_targets, val_loss = collect(validation_loader, compute_loss=True)
        val_accuracy = float(accuracy_score(val_targets, [row.index(max(row)) for row in val_probs]))
        history.append({'epoch': epoch + 1, 'train_loss': running_loss / max(seen, 1),
                        'validation_loss': val_loss, 'validation_accuracy': val_accuracy})
        if val_accuracy > best_validation_accuracy:
            best_validation_accuracy = val_accuracy
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    if best_state is None:
        raise RuntimeError('Training did not produce a model checkpoint.')

    model.load_state_dict(best_state)
    val_probs, val_targets, _ = collect(validation_loader)
    test_probs, test_targets, test_loss = collect(test_loader, compute_loss=True)
    test_predictions = [row.index(max(row)) for row in test_probs]
    precision, recall, f1, support = precision_recall_fscore_support(
        test_targets, test_predictions, labels=list(range(len(CATEGORIES))), zero_division=0,
    )
    calibration = _calibrate_thresholds(val_probs, val_targets)
    version = model_version or datetime.now(timezone.utc).strftime('urbanai-mobilenetv3-small-%Y%m%dT%H%M%SZ')
    metrics = {
        'model_version': version,
        'architecture': 'MobileNetV3-Small',
        'pretrained_initialization': 'MobileNet_V3_Small_Weights.DEFAULT',
        'device': str(device),
        'epochs_requested': epochs,
        'validation_history': history,
        'test_loss': test_loss,
        'test_accuracy': float(accuracy_score(test_targets, test_predictions)),
        'macro_precision': float(precision.mean()),
        'macro_recall': float(recall.mean()),
        'macro_f1': float(f1.mean()),
        'class_metrics': {
            name: {'precision': float(precision[index]), 'recall': float(recall[index]),
                   'f1': float(f1[index]), 'support': int(support[index])}
            for index, (name, _slug) in enumerate(CATEGORIES)
        },
        'confusion_matrix': confusion_matrix(test_targets, test_predictions, labels=list(range(len(CATEGORIES)))).tolist(),
        'confusion_matrix_labels': [name for name, _slug in CATEGORIES],
        'confidence_threshold_calibration': calibration,
        'training_examples': len(train_set), 'validation_examples': len(validation_set),
        'test_examples': len(test_set),
    }
    checkpoint = {
        'state_dict': best_state,
        'categories': list(SLUGS),
        'model_version': version,
        'architecture': 'MobileNetV3-Small',
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'dataset_reference': f'dataset_manifest_sha256:{dataset_reference}' if dataset_reference else None,
        'supported_categories': [name for name, _slug in CATEGORIES],
        'thresholds_validated': calibration['validated'],
        'confidence_thresholds': {'medium': calibration['medium'], 'high': calibration['high']},
        'evaluation_summary': {key: metrics[key] for key in ('test_accuracy', 'macro_precision', 'macro_recall', 'macro_f1')},
    }
    torch.save(checkpoint, out_dir / 'urbanai-mobilenetv3-small.pt')
    (out_dir / 'evaluation.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='artifacts/dataset-splits')
    parser.add_argument('--output', default='ai/models')
    parser.add_argument('--model-version')
    parser.add_argument('--epochs', type=int, default=15)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--learning-rate', type=float, default=1e-4)
    args = parser.parse_args()
    try:
        metrics = train(args.data, args.output, args.model_version, args.epochs, args.batch_size, args.learning_rate)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({'model_version': metrics['model_version'], 'test_accuracy': metrics['test_accuracy'],
                      'macro_f1': metrics['macro_f1'], 'evaluation_file': str(Path(args.output) / 'evaluation.json')}, indent=2))


if __name__ == '__main__':
    main()
