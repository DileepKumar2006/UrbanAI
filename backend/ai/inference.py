"""CPU-first MobileNetV3 inference. No weights or predictions are fabricated."""

from functools import lru_cache
from pathlib import Path
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .categories import SLUGS, SLUG_TO_LABEL


class ModelUnavailable(RuntimeError):
    pass


def configured_model_path():
    return Path(__file__).resolve().parent / 'models' / 'urbanai-mobilenetv3-small.pt'


@lru_cache(maxsize=2)
def _load_model(path_string, modified_ns):
    model_path = Path(path_string)
    if not model_path.is_file():
        raise ModelUnavailable('No trained UrbanAI classifier is installed.')
    try:
        import torch
        from torch import nn
        from torchvision.models import mobilenet_v3_small
    except ImportError as error:
        raise ModelUnavailable('Install backend/requirements-ai.txt to enable image inference.') from error

    try:
        checkpoint = torch.load(model_path, map_location='cpu', weights_only=True)
        if checkpoint.get('categories') != list(SLUGS):
            raise ModelUnavailable('The installed model has an incompatible category list.')
        model = mobilenet_v3_small(weights=None)
        input_features = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(input_features, len(SLUGS))
        model.load_state_dict(checkpoint['state_dict'], strict=True)
        model.eval()
    except ModelUnavailable:
        raise
    except Exception as error:
        raise ModelUnavailable(f'The trained model could not be loaded: {error.__class__.__name__}.') from error

    return torch, model, checkpoint


def classify_image(image_bytes, model_path=None):
    path = Path(model_path) if model_path else configured_model_path()
    if not path.is_file():
        raise ModelUnavailable('No trained UrbanAI classifier is installed.')
    torch, model, checkpoint = _load_model(str(path.resolve()), path.stat().st_mtime_ns)
    try:
        from torchvision import transforms
        with Image.open(BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image).convert('RGB')
        tensor = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ])(image).unsqueeze(0)
        with torch.inference_mode():
            probabilities = torch.softmax(model(tensor), dim=1)[0]
        confidence, class_index = torch.max(probabilities, dim=0)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError('The image could not be decoded for classification.') from error
    except Exception as error:
        raise RuntimeError('Image inference failed.') from error

    confidence_value = float(confidence.item())
    thresholds = checkpoint.get('confidence_thresholds') or {}
    validated = bool(checkpoint.get('thresholds_validated'))
    medium = float(thresholds.get('medium', 0.0))
    high = float(thresholds.get('high', 1.0))
    if not validated:
        band = 'uncalibrated'
    elif confidence_value >= high:
        band = 'high'
    elif confidence_value >= medium:
        band = 'medium'
    else:
        band = 'low'

    slug = SLUGS[int(class_index.item())]
    return {
        'predicted_category': SLUG_TO_LABEL[slug],
        'confidence_score': confidence_value,
        'model_version': str(checkpoint.get('model_version') or 'unknown'),
        'processing_status': 'requires_review' if band in {'low', 'uncalibrated'} else 'completed',
        'confidence_band': band,
        'review_required': band in {'low', 'uncalibrated'},
        'thresholds_validated': validated,
    }
