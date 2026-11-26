# Nagrik image-classification research record

## Research problem and scope

The task is image-based assistance for seven civic complaint categories. A proposed MobileNetV3-Small classifier is integrated behind an explicit model-availability check. The citizen confirms or changes its suggestion; the classifier does not submit or route a complaint on its own. This is a research plan and implementation record, not a claim of validated model performance.

## Current evidence

The repository currently contains the inference and training pipeline, category definitions, deterministic hash-grouped dataset preparation, model metadata reporting, and AI review flow. It does **not** contain a verified labeled training dataset, trained weights, independent test set, or measured evaluation results. The three user-supplied road photos are examples only and are not treated as training data or as database complaints. Consequently, inference is truthfully reported as unavailable until an authorized, licensed dataset is reviewed and a checkpoint is trained and evaluated.

No accuracy, precision, recall, F1, confusion matrix, novelty, or comparative performance claim is made.

## Dataset provenance and preparation

Use `dataset/source-manifest.template.csv` to record image origin, license, attribution, and permission. Exclude private citizen uploads unless informed consent and retention/usage terms explicitly permit research use. Group perceptual duplicates by image hash before splitting to reduce train/test leakage. The preparation report must be reviewed before training, with all seven labels present in train, validation, and test splits. Preserve the manifest and seed with every experiment.

### Public-source screening (2026-11-26)

- Mendeley Data's *Urban Civic Issues Image Dataset: Potholes and Garbage (QR4Change)* (DOI 10.17632/zndzygc3p3.2) is listed under CC BY 4.0 and contains pothole/road and garbage/non-garbage labels. Its public metadata reports about 6.2 GB across 4,938 files. This is a potential source for pothole and waste classes only; the non-issue examples must not be relabeled as the positive `other` civic category. Folder/category mapping, image quality, attribution, and terms for any embedded third-party material must be checked before download/use.
- A Roboflow Universe *Civic-issues* dataset is listed with CC BY 4.0 and classes including pothole, garbage, fallen tree, streetlight, and water leak, but a reproducible public export/version was not verified in this review. Fallen tree is not a complete or representative substitute for all road obstructions.
- A separate Roboflow *Civic Issues Computer Vision Dataset* lists CC BY 4.0 and versions for potholes, bins/illegal dumping, broken streetlights, fallen trees, and waterlogging. The listed version counts range from 50 to 250 images. It does not provide verified water-leak, sewage/drainage, or generic other-civic labels, and no export was downloaded or audited in this session.
- RDD2020 is an open CC BY 4.0 road-damage source with pothole and crack annotations, but the papers describe detection annotations rather than a ready-to-use seven-way civic classification set. It can support road-damage examples after a provenance-preserving conversion; it does not fill the missing civic classes.
- These findings do not provide verified examples for drainage/sewage and general other civic problems. They are not sufficient evidence to train or claim a validated seven-class production model. Do not install a partial model as if it supports all seven classes. Dataset audit and permissions remain outstanding.

## Planned experimental protocol

1. Freeze a versioned, permission-checked manifest and a hash-based train/validation/test split.
2. Record preprocessing, image dimensions, augmentation, class counts, random seeds, dependency versions, model version, and compute device.
3. Compare a majority-class baseline with MobileNetV3-Small transfer learning. Add EfficientNet or ResNet only if hardware and dataset size permit a fair, reproducible comparison.
4. Select thresholds on validation data only. Report top-1 accuracy, per-class precision/recall/F1, macro-F1, class support, confusion matrix, and calibration results on the untouched test split.
5. Evaluate low-confidence abstention and citizen override rates before enabling suggestions. Do not use overall accuracy alone when class counts are imbalanced.
6. Publish the dataset reference hash, training config, code revision, evaluation JSON, and limitations with each model artifact. Keep model weights outside source control and deploy only a separately reviewed artifact.

## Limitations and responsible use

Photographs vary by camera, lighting, road material, language of accompanying descriptions, and city. Categories can overlap visually. A high score on one city's data does not establish generalization to another. Human confirmation remains required; uncertain or unavailable predictions must permit manual reporting. The application does not infer severity, repair priority, or whether a report is genuine from the image.

## Reproducibility commands

From `backend/`, after adding reviewed data and the source manifest:

```powershell
python -m ai.prepare_dataset --input ai/dataset/raw --source-manifest ai/dataset/source-manifest.csv --output artifacts/dataset-splits --seed 20261120
python -m pip install -r requirements-ai.txt
python -m ai.train --data artifacts/dataset-splits --output ai/models
```

The trainer writes actual metrics to `ai/models/evaluation.json`; no results should be reported until this workflow has completed successfully on the reviewed dataset.
