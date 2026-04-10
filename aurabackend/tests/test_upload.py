#!/usr/bin/env python3
"""
Simple test script to verify file upload functionality
"""

import os
from pathlib import Path

import requests


def test_file_upload():
    """Test uploading the generated Parquet file"""

    # API endpoint
    api_url = "http://localhost:8000/files/upload"

    # Test file path (from our test script)
    test_file = Path(__file__).parent.parent / "data" / "test_files" / "sample_employees.parquet"

    if not test_file.exists():
        print(f"❌ Test file not found: {test_file}")
        print("Run 'python test_parquet_support.py' first to create test files")
        return

    print("🧪 Testing Parquet file upload...")
    print(f"📁 File: {test_file}")
    print(f"📊 Size: {test_file.stat().st_size} bytes")

    try:
        with open(test_file, 'rb') as f:
            files = {'file': ('products.parquet', f, 'application/octet-stream')}

            print(f"🚀 Uploading to {api_url}...")
            response = requests.post(api_url, files=files, timeout=30)

        print(f"📡 Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print("✅ Upload successful!")
            print(f"📋 File ID: {result['file_info']['file_id']}")
            print(f"📊 Rows: {result['file_info']['rows_count']}")
            print(f"📈 Columns: {result['file_info']['columns_count']}")
            print(f"📄 Preview: {len(result['preview'])} rows")

            # Show preview data
            if result['preview']:
                print("\n🔍 Data Preview:")
                for i, row in enumerate(result['preview'][:3]):
                    print(f"  Row {i+1}: {row}")

        else:
            print(f"❌ Upload failed: {response.status_code}")
            print(f"Response: {response.text}")

    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to API Gateway at http://localhost:8000")
        print("Make sure the API Gateway is running: python api_gateway/main.py")
    except Exception as e:
        print(f"❌ Error during upload: {e}")

def test_supported_formats():
    """Test the supported formats endpoint"""

    api_url = "http://localhost:8000/files/supported-formats"

    try:
        print("🧪 Testing supported formats endpoint...")
        response = requests.get(api_url, timeout=10)

        print(f"📡 Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print("✅ Endpoint working!")

            if 'supported_formats' in result:
                formats = result['supported_formats']
                print("\n📋 Supported Formats:")
                for format_name, info in formats.items():
                    print(f"  {info['icon']} {format_name}: {info['extensions']} - {info['description']}")

                if 'parquet' in formats:
                    print("\n🎉 Parquet support confirmed!")
                else:
                    print("\n⚠️ Parquet not found in supported formats")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Response: {response.text}")

    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to API Gateway at http://localhost:8000")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("🧪 AURA File Upload Test Suite")
    print("=" * 50)

    # Test 1: Supported formats
    test_supported_formats()

    print("\n" + "=" * 50)

    # Test 2: File upload
    test_file_upload()

    print("\n✅ Test complete!")
