# Reviewed complaint image dataset

No training images or trained UrbanAI model weights are included in this
repository. The three supplied road photographs are preserved as UI examples;
they are not a representative labeled dataset and are not used for training.

Collect and review images with permission, then organize them as:

```text
dataset/raw/<category-slug>/your-image.jpg
```

The seven slugs are `pothole_road_damage`, `garbage_overflow`,
`drainage_sewage_problem`, `broken_streetlight`, `water_leakage`,
`road_obstruction`, and `other_civic_issues`. Record each image's origin,
license, license URL, attribution, and permission verification in
`source-manifest.csv` using the adjacent template. Do not add private citizen
photos or images whose reuse rights are unclear.

From `backend/`, prepare deterministic hash-grouped splits:

```powershell
python -m ai.prepare_dataset --input ai/dataset/raw --source-manifest ai/dataset/source-manifest.csv --output artifacts/dataset-splits
```

Review `artifacts/dataset-splits/preparation_report.json` and the split counts.
Training requires non-empty train, validation, and test splits for all seven
labels; gather enough distinct verified images per class before training.

Install optional local training dependencies and run:

```powershell
python -m pip install -r requirements-ai.txt
python -m ai.train --data artifacts/dataset-splits --output ai/models
```

Training saves `ai/models/urbanai-mobilenetv3-small.pt` and actual test metrics
to `ai/models/evaluation.json`. Model files are git-ignored. The API stays
available for manual reports if the optional dependencies or model are absent.
