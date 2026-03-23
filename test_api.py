import urllib.request
import json
import time

url = 'http://localhost:8000/chat'
data = {
    'message': 'Calculate the survival rate for each Passenger Class (Pclass). Which class had the highest survival rate, and does this align with the "women and children first" narrative?',
    'session_id': 'test_debug'
}

req = urllib.request.Request(
    url, 
    data=json.dumps(data).encode('utf-8'), 
    headers={'Content-Type': 'application/json'}
)

print("Running API...")
try:
    with urllib.request.urlopen(req) as response:
        res = json.loads(response.read().decode('utf-8'))
        print("Success! SQL:")
        print(res.get("final_query"))
        if "execution_result" in res:
            print("Conclusion:")
            print(res["execution_result"].get("conclusion"))
except Exception as e:
    print(f"Error: {e}")
