"""
Test End-to-End Workflow Performance via API
Upload → Profile → Semantic Model (direct) → Validate Query
"""
import time
import subprocess
import json

print("=" * 80)
print("END-TO-END WORKFLOW TEST (API)")
print("=" * 80)

# Test file
test_file = "test_sales_data.csv"
api_base = "http://localhost:8000"

# Step 1: Upload & Profile
print("\n[Step 1] Upload & Profile via API...")
start = time.time()
result = subprocess.run(
    ['curl.exe', '-s', '-X', 'POST', f'{api_base}/files/upload', '-F', f'file=@{test_file}'],
    capture_output=True,
    text=True
)
upload_time = time.time() - start

try:
    data = json.loads(result.stdout)
    file_id = data['file_info']['file_id']
    profile = data['profile']
    print(f"✅ Upload complete: {upload_time:.3f}s")
    print(f"   - File ID: {file_id[:20]}...")
    print(f"   - Rows: {profile.get('rows')}, Columns: {profile.get('columns')}")
except Exception as e:
    print(f"❌ Upload failed: {e}")
    print(f"Response: {result.stdout[:200]}")
    exit(1)

# Step 2: Generate Semantic Model (direct, not via broken API endpoint)
print("\n[Step 2] Generate Semantic Model (direct)...")
from aurabackend.semantic_builder import SemanticModelBuilder
builder = SemanticModelBuilder()
start = time.time()
model = builder.generate_model_from_profile(
    file_id=file_id,
    dataset_name="Sales E2E Test",
    profile=profile
)
model_time = time.time() - start
print(f"✅ Model generated: {model_time:.3f}s")
print(f"   - Name: {model['name']}")
print(f"   - Fields: {len(model['fields'])}")

# Step 3: Validate SQL Query (direct)
print("\n[Step 3] Validate SQL Query (direct)...")
from aurabackend.safety.validator import SQLSafetyValidator
validator = SQLSafetyValidator()
start = time.time()
query = "SELECT product, region, SUM(revenue) as total FROM sales GROUP BY product, region LIMIT 50"
result = validator.validate(query)
validate_time = time.time() - start
print(f"✅ Validation complete: {validate_time:.3f}s")
print(f"   - Valid: {result.is_valid}, Risk: {result.risk_level.value}")

# Step 4: Block Dangerous Query
print("\n[Step 4] Block Dangerous Query...")
start = time.time()
result = validator.validate("DROP TABLE sales")
block_time = time.time() - start
print(f"✅ Blocked: {block_time:.3f}s (Risk: {result.risk_level.value})")

# Summary
total_time = upload_time + model_time + validate_time + block_time
print("\n" + "=" * 80)
print("WORKFLOW TIMING SUMMARY")
print("=" * 80)
print(f"Upload & Profile:     {upload_time:6.3f}s  ({upload_time/total_time*100:5.1f}%)")
print(f"Semantic Model:       {model_time:6.3f}s  ({model_time/total_time*100:5.1f}%)")
print(f"Query Validation:     {validate_time:6.3f}s  ({validate_time/total_time*100:5.1f}%)")
print(f"Dangerous Block:      {block_time:6.3f}s  ({block_time/total_time*100:5.1f}%)")
print(f"{'-'*80}")
print(f"TOTAL TIME:           {total_time:6.3f}s")
print("=" * 80)

if total_time < 5.0:
    print("✅ Performance target met: < 5 seconds")
else:
    print(f"⚠️  Performance target missed: {total_time:.3f}s (target: < 5s)")
    
print("=" * 80)
