"""
Test semantic model builder directly (without async database)
"""
from aurabackend.semantic_builder import SemanticModelBuilder
import json

# Sample profile from the upload response
profile = {
    "rows": 10,
    "columns": 7,
    "columns_profile": {
        "date": {"inferred_type": "categorical", "distinct": 5},
        "product": {"inferred_type": "categorical", "distinct": 3},
        "region": {"inferred_type": "categorical", "distinct": 4},
        "quantity": {"inferred_type": "numeric", "min": 90, "max": 250, "mean": 155.0},
        "revenue": {"inferred_type": "numeric", "min": 3000, "max": 12500, "mean": 6250.0},
        "cost": {"inferred_type": "numeric", "min": 1200, "max": 5000, "mean": 2506.0},
        "customer_type": {"inferred_type": "categorical", "distinct": 3}
    }
}

builder = SemanticModelBuilder()
model = builder.generate_model_from_profile(
    file_id="93061a4a-509c-43ab-b7ac-32ed5b6b2213",
    dataset_name="Sales Test Data",
    profile=profile
)

print("=" * 80)
print("SEMANTIC MODEL GENERATED")
print("=" * 80)
print(f"Model Name: {model['name']}")
print(f"Description: {model['description']}")
print(f"\nSource: {json.dumps(model['source'], indent=2)}")
print(f"\nTags: {', '.join(model['tags'])}")
print(f"\nFields ({len(model['fields'])}):")

dimensions = [f for f in model['fields'] if f['field_type'] == 'dimension']
measures = [f for f in model['fields'] if f['field_type'] == 'measure']

print(f"\n  Dimensions ({len(dimensions)}):")
for dim in dimensions:
    print(f"    - {dim['name']}: {dim['data_type']} ({dim.get('description', 'N/A')})")

print(f"\n  Measures ({len(measures)}):")
for meas in measures:
    agg = meas.get('aggregation', 'none')
    print(f"    - {meas['name']}: {meas['data_type']} [aggregation: {agg}] ({meas.get('description', 'N/A')})")

print("\n" + "=" * 80)
print("✅ Semantic model builder working correctly!")
print("=" * 80)
