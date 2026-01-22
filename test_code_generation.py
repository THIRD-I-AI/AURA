#!/usr/bin/env python
"""Test code generation to verify if it's using real Gemini AI or fallback"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'aurabackend'))

from code_generation_service.main import _engine
from shared.models import PlanStep

print("\n" + "="*80)
print("CODE GENERATION TEST - Real AI vs Fallback")
print("="*80 + "\n")

# Test 1: Custom request about customer frequency
print("[TEST 1] Custom Request - Customer Purchase Frequency")
print("-" * 80)
step1 = PlanStep(
    step='Analyze customer purchase frequency by region and month',
    task='Show which regions have the most frequent purchases',
    chart_type='heatmap'
)

result1 = _engine.generate(step1)
print(f"Source: {result1['source']}")
print(f"Generated SQL:\n{result1['sql']}\n")

# Test 2: Different unique request
print("[TEST 2] Different Request - Revenue by Segment")
print("-" * 80)
step2 = PlanStep(
    step='Revenue contribution by customer segment quarterly',
    task='Break down quarterly revenue by customer type',
    chart_type='stacked_bar'
)

result2 = _engine.generate(step2)
print(f"Source: {result2['source']}")
print(f"Generated SQL:\n{result2['sql']}\n")

# Test 3: Request that might match fallback pattern
print("[TEST 3] Fallback-Pattern Request - Top Products")
print("-" * 80)
step3 = PlanStep(
    step='top products by revenue',
    task='Find the top selling products',
    chart_type='bar_chart'
)

result3 = _engine.generate(step3)
print(f"Source: {result3['source']}")
print(f"Generated SQL:\n{result3['sql']}\n")

# Test 4: Trend analysis
print("[TEST 4] Trend Request - Monthly Revenue")
print("-" * 80)
step4 = PlanStep(
    step='revenue trends over time',
    task='Show monthly revenue patterns',
    chart_type='line_chart'
)

result4 = _engine.generate(step4)
print(f"Source: {result4['source']}")
print(f"Generated SQL:\n{result4['sql']}\n")

print("="*80)
print("ANALYSIS")
print("="*80)
if all(r['source'] == 'gemini' for r in [result1, result2, result3, result4]):
    print("✅ ALL TESTS USED GEMINI AI (Real Generation)")
    print("\nThe code generator is ACTUALLY GENERATING code using Google Gemini AI!")
else:
    print("⚠️ SOME TESTS USED FALLBACK (Hardcoded Queries)")
    print("\nResults:")
    print(f"  Test 1: {result1['source']}")
    print(f"  Test 2: {result2['source']}")
    print(f"  Test 3: {result3['source']}")
    print(f"  Test 4: {result4['source']}")
print("="*80 + "\n")
