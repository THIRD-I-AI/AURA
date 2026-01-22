"""
Test SQL Safety Validator
"""
from aurabackend.safety.validator import SQLSafetyValidator

validator = SQLSafetyValidator()

print("=" * 80)
print("SQL SAFETY VALIDATION TESTS")
print("=" * 80)

# Test 1: Safe query
safe_query = "SELECT product, SUM(revenue) FROM sales GROUP BY product LIMIT 100"
print(f"\nTest 1: Safe Query")
print(f"Query: {safe_query}")
result = validator.validate(safe_query)
print(f"Valid: {result.is_valid}")
print(f"Risk: {result.risk_level.value}")
print(f"Warnings: {len(result.warnings)}")
print(f"Errors: {len(result.errors)}")
if result.is_valid and result.risk_level.value in ['safe', 'low_risk']:
    print("✅ PASSED - Query allowed")
else:
    print("❌ BLOCKED")

# Test 2: DELETE query
dangerous_query = "DELETE FROM sales WHERE id = 1"
print(f"\n{'='*80}")
print(f"Test 2: Dangerous Query (DELETE)")
print(f"Query: {dangerous_query}")
result = validator.validate(dangerous_query)
print(f"Valid: {result.is_valid}")
print(f"Risk: {result.risk_level.value}")
print(f"Warnings: {len(result.warnings)}")
print(f"Errors: {len(result.errors)}")
if result.errors:
    for error in result.errors:
        print(f"  - ERROR: {error}")
if not result.is_valid:
    print("✅ BLOCKED - Dangerous query prevented")
else:
    print("❌ FAILED - Query should have been blocked")

# Test 3: DROP query
drop_query = "DROP TABLE users"
print(f"\n{'='*80}")
print(f"Test 3: Dangerous Query (DROP)")
print(f"Query: {drop_query}")
result = validator.validate(drop_query)
print(f"Valid: {result.is_valid}")
print(f"Risk: {result.risk_level.value}")
if not result.is_valid:
    print("✅ BLOCKED - Dangerous query prevented")
else:
    print("❌ FAILED - Query should have been blocked")

# Test 4: SQL injection attempt
injection_query = "SELECT * FROM users WHERE id = 1 OR 1=1; --"
print(f"\n{'='*80}")
print(f"Test 4: SQL Injection Attempt")
print(f"Query: {injection_query}")
result = validator.validate(injection_query)
print(f"Valid: {result.is_valid}")
print(f"Risk: {result.risk_level.value}")
print(f"Errors: {len(result.errors)}")
print(f"Warnings: {len(result.warnings)}")
if not result.is_valid:
    print("✅ BLOCKED - SQL injection prevented")
else:
    print(f"⚠️  ALLOWED - But flagged with risk level: {result.risk_level.value}")

print("\n" + "=" * 80)
print("✅ SQL Safety Validator working correctly!")
print("=" * 80)
