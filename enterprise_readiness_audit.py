#!/usr/bin/env python
"""Enterprise Readiness Audit - Real World Functionality Tests"""

import requests
import json
import sys
import traceback

print("\n" + "="*80)
print("ENTERPRISE READINESS AUDIT - REAL WORLD TESTS")
print("="*80 + "\n")

passed = 0
failed = 0
issues = []

# Test 1: Check if API Gateway is responding
print("[TEST 1] API Gateway Health")
print("-" * 80)
try:
    response = requests.get("http://localhost:8000/health", timeout=5)
    if response.status_code == 200:
        print(f"✓ Status: {response.status_code}")
        print(f"✓ Response: {response.json()}")
        passed += 1
    else:
        print(f"✗ Status Code {response.status_code}")
        failed += 1
        issues.append("API Gateway returned non-200 status")
except Exception as e:
    print(f"✗ FAILED: {e}")
    failed += 1
    issues.append(f"API Gateway not responding: {str(e)}")
print()

# Test 2: Check file upload endpoint
print("[TEST 2] File Upload Functionality")
print("-" * 80)
try:
    with open('test_sales_data.csv', 'rb') as f:
        files = {'file': f}
        response = requests.post("http://localhost:8000/files", files=files, timeout=10)
    
    if response.status_code == 200:
        result = response.json()
        print(f"✓ Upload Status: {response.status_code}")
        print(f"✓ Response: {result}")
        if 'id' in result:
            print(f"✓ File ID: {result['id']}")
            passed += 1
        else:
            print(f"✗ No file ID in response")
            failed += 1
            issues.append("File upload succeeded but no ID returned")
    else:
        print(f"✗ Upload Failed: {response.status_code}")
        print(f"✗ Response: {response.text[:200]}")
        failed += 1
        issues.append(f"File upload failed with status {response.status_code}")
except FileNotFoundError:
    print(f"✗ Test file not found")
    failed += 1
    issues.append("Test file missing")
except Exception as e:
    print(f"✗ FAILED: {e}")
    traceback.print_exc()
    failed += 1
    issues.append(f"File upload error: {str(e)}")
print()

# Test 3: Check semantic models endpoint
print("[TEST 3] Semantic Models Retrieval")
print("-" * 80)
try:
    response = requests.get("http://localhost:8000/semantic/models", timeout=5)
    if response.status_code == 200:
        models = response.json()
        print(f"✓ Status: {response.status_code}")
        print(f"✓ Models retrieved: {len(models) if isinstance(models, list) else 'unknown'}")
        passed += 1
    else:
        print(f"✗ Status Code {response.status_code}")
        failed += 1
        issues.append(f"Semantic models endpoint returned {response.status_code}")
except Exception as e:
    print(f"✗ FAILED: {e}")
    failed += 1
    issues.append(f"Semantic models error: {str(e)}")
print()

# Test 4: Check database service
print("[TEST 4] Database Service Connectivity")
print("-" * 80)
try:
    response = requests.get("http://localhost:8002/health", timeout=5)
    if response.status_code == 200:
        print(f"✓ Status: {response.status_code}")
        print(f"✓ Response: {response.json()}")
        passed += 1
    else:
        print(f"✗ Status Code {response.status_code}")
        failed += 1
        issues.append("Database service not healthy")
except Exception as e:
    print(f"✗ FAILED: {e}")
    failed += 1
    issues.append(f"Database service not responding: {str(e)}")
print()

# Test 5: Check code generation service
print("[TEST 5] Code Generation Service")
print("-" * 80)
try:
    response = requests.get("http://localhost:8003/health", timeout=5)
    if response.status_code == 200:
        print(f"✓ Service Health: {response.status_code}")
        
        # Try to generate code
        payload = {
            "step": "Show top 10 products by revenue",
            "task": "Analyze best performing products",
            "chart_type": "bar_chart"
        }
        response = requests.post("http://localhost:8003/generate_code", json=payload, timeout=5)
        if response.status_code == 200:
            result = response.json()
            print(f"✓ Code Generation: {response.status_code}")
            print(f"✓ Source: {result.get('source')}")
            print(f"✓ SQL Preview: {result.get('sql')[:80]}...")
            passed += 1
        else:
            print(f"✗ Code Generation Failed: {response.status_code}")
            print(f"Response: {response.text[:200]}")
            failed += 1
            issues.append(f"Code generation returned {response.status_code}")
    else:
        print(f"✗ Service Health: {response.status_code}")
        failed += 1
        issues.append("Code generation service not healthy")
except Exception as e:
    print(f"✗ FAILED: {e}")
    traceback.print_exc()
    failed += 1
    issues.append(f"Code generation error: {str(e)}")
print()

# Test 6: Check all 8 services
print("[TEST 6] All Microservices Health Check")
print("-" * 80)
services = {
    8000: "API Gateway",
    8001: "Orchestration",
    8002: "Database",
    8003: "Code Generation",
    8004: "Scheduler",
    8005: "Knowledge Base",
    8006: "Metadata Store",
    8007: "Execution Sandbox"
}

healthy = 0
for port, name in services.items():
    try:
        response = requests.get(f"http://localhost:{port}/health", timeout=2)
        if response.status_code == 200:
            print(f"✓ {name} ({port})")
            healthy += 1
        else:
            print(f"✗ {name} ({port}): Status {response.status_code}")
    except Exception as e:
        print(f"✗ {name} ({port}): Not responding")

if healthy == 8:
    passed += 1
else:
    failed += 1
    issues.append(f"Only {healthy}/8 services healthy")
print()

# Summary
print("="*80)
print("AUDIT RESULTS")
print("="*80)
print(f"\nPassed Tests: {passed}")
print(f"Failed Tests: {failed}")
print(f"Success Rate: {passed}/{passed+failed} ({100*passed/(passed+failed) if (passed+failed) > 0 else 0:.0f}%)")

if issues:
    print("\nISSUES IDENTIFIED:")
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}")

print("\n" + "="*80)
if failed == 0:
    print("✓ SYSTEM APPEARS READY FOR ENTERPRISE DEPLOYMENT")
else:
    print(f"✗ {failed} CRITICAL ISSUES - NOT READY FOR DEPLOYMENT")
print("="*80 + "\n")
