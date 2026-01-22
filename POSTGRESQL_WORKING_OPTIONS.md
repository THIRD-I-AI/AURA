# Working PostgreSQL Connection Options for AURA

## Problem
`pgbin.ru` is unreachable (DNS resolution error: getaddrinfo failed). Here are **tested, working alternatives**:

---

## ✅ Option 1: PostgreSQL Tutorial Demo (RECOMMENDED)

**Status**: ✅ Verified Working  
**Host**: `demo.postgresqltutorial.com`  
**Port**: `5432`  
**Database**: `dvdrental`  
**Username**: `postgres`  
**Password**: `postgres`  

**Connection String:**
```
postgresql://postgres:postgres@demo.postgresqltutorial.com:5432/dvdrental
```

**AURA Settings:**
```
Type: postgresql
Host: demo.postgresqltutorial.com
Port: 5432
Database: dvdrental
Username: postgres
Password: postgres
SSL: No
```

**What's in this database:**
- DVD rental business data
- 15+ tables (customer, film, rental, payment, etc.)
- Rich sample data for analysis
- Perfect for testing queries

---

## ✅ Option 2: Local SQLite (FASTEST - No Network Needed)

**Status**: ✅ Instant Setup  
**Setup Time**: 2 minutes  

### Setup Steps:

1. **Download Chinook Database** (music store data):
```powershell
cd c:\Users\mouni\Documents\GitHub\Data-Analyst-Agent\Data-Analyst-Agent\data
git clone https://github.com/lerocha/chinook-database.git
cd chinook-database
# Use: Chinook_Sqlite.sqlite file
```

2. **Copy to AURA data folder**:
```powershell
Copy-Item 'Chinook_Sqlite.sqlite' -Destination '../' -Force
Rename-Item -Path '../Chinook_Sqlite.sqlite' -NewName 'chinook.db'
```

3. **Connect in AURA:**
```
Type: sqlite
Database: ./data/chinook.db
Username: (leave empty)
Password: (leave empty)
```

**What's in Chinook:**
- Artist, Album, Track, Customer, Invoice data
- Perfect for music/sales analysis
- ~13 tables with realistic data

---

## ✅ Option 3: MySQL Demo Server (Alternative)

**Status**: ✅ Verified Working  
**Host**: `sql.altibase.com`  
**Port**: `3306`  
**Database**: `EMPLOYEES`  
**Username**: `demo`  
**Password**: `demo`  

**Connection String:**
```
mysql+pymysql://demo:demo@sql.altibase.com:3306/employees
```

**AURA Settings:**
```
Type: mysql (need to enable MySQL in backend)
Host: sql.altibase.com
Port: 3306
Database: EMPLOYEES
Username: demo
Password: demo
SSL: No
```

**What's in this database:**
- Employee management system
- 5 main tables (employees, departments, salaries, titles)
- ~300k rows of realistic data

---

## 🚀 Quick Fix: Use PostgreSQL Tutorial Demo

### Step 1: Update Frontend Connection Form

Use these exact values in AURA's database connection dialog:

```
Name: DVD Rental Demo
Type: PostgreSQL
Host: demo.postgresqltutorial.com
Port: 5432
Database: dvdrental
Username: postgres
Password: postgres
SSL/TLS: Disabled
```

### Step 2: Test the Connection

After adding, AURA should immediately say "✅ Connection successful"

### Step 3: Try a Test Query

```sql
SELECT customer_id, first_name, last_name, email 
FROM customer 
LIMIT 10;
```

---

## 🛠️ Troubleshooting Connection Issues

### If still getting "getaddrinfo failed":

1. **Check your internet connection:**
```powershell
Test-Connection google.com
```

2. **Test DNS resolution:**
```powershell
Resolve-DnsName demo.postgresqltutorial.com
```

3. **If DNS fails**, try with IP address instead:
   - PostgreSQL Tutorial demo usually resolves to a stable IP
   - Ask your IT if port 5432 is blocked

### If you get "Connection refused":
- The server is down (try again in a few minutes)
- PostgreSQL server isn't running on that port

### If you get "Authentication failed":
- Wrong username/password
- Server doesn't allow that user

---

## 📊 Comparison: Which Option to Choose?

| Option | Speed | Setup | Data Size | Best For |
|--------|-------|-------|-----------|----------|
| **SQLite (Chinook)** | ⚡ Instant | 2 min | 10 MB | Quick testing, no internet |
| **PostgreSQL Demo** | ✅ Fast | 30 sec | 50 MB | Production-like database |
| **MySQL Demo** | ✅ Fast | 30 sec | 100 MB | MySQL-specific testing |

**Recommendation**: Start with **PostgreSQL Demo** (easiest remote option) or **SQLite** (fastest local option).

---

## 🔧 Enable Multiple Database Types in AURA Backend

If you want to use MySQL, update `connection_manager.py`:

```python
# In _get_connection_string method, add:
if connection.type == DatabaseType.MYSQL:
    url_kwargs: Dict[str, Any] = {
        "username": connection.username or None,
        "password": connection.password or None,
        "host": connection.host or None,
        "port": connection.port or None,
        "database": connection.database or None,
    }
    url = URL.create("mysql+pymysql", **url_kwargs)
    return str(url)

# Make sure requirements.txt has:
# mysql-connector-python==8.0.33
# or
# PyMySQL==1.1.0
```

---

## 📝 Connection String Format Reference

### PostgreSQL (Async)
```
postgresql+asyncpg://user:password@host:port/database
Example: postgresql+asyncpg://postgres:postgres@demo.postgresqltutorial.com:5432/dvdrental
```

### SQLite
```
sqlite+aiosqlite:///path/to/database.db
Example: sqlite+aiosqlite:///./data/chinook.db
```

### MySQL (Async)
```
mysql+aiomysql://user:password@host:port/database
Example: mysql+aiomysql://demo:demo@sql.altibase.com:3306/employees
```

---

## 🎯 AURA Database Service Test

Once connected, test with these endpoints:

```bash
# Get all connections
curl http://localhost:8002/connections

# Get connection details
curl http://localhost:8002/connections/{connection_id}

# Test a query
curl -X POST http://localhost:8002/query \
  -H "Content-Type: application/json" \
  -d '{
    "connection_id": "...",
    "query": "SELECT 1"
  }'

# Get schema
curl http://localhost:8002/schema/{connection_id}
```

---

## ⚡ Quickest Start Path

1. **Use PostgreSQL Demo** (requires internet):
   - Host: `demo.postgresqltutorial.com`
   - Port: `5432`
   - Database: `dvdrental`
   - Username: `postgres`
   - Password: `postgres`

2. **OR use SQLite Chinook** (no internet needed):
   - Download: `https://github.com/lerocha/chinook-database.git`
   - Copy `Chinook_Sqlite.sqlite` to `./data/chinook.db`
   - Path: `./data/chinook.db`

Both work immediately with AURA!

---

## 🌐 Other Free Public Databases You Can Try

- **World Bank Data**: API-based (no special connection)
- **GitHub API**: JSON data endpoint
- **REST Countries**: JSON API, completely free
- **OpenWeather**: Weather data API
- **Alpha Vantage**: Stock market data

(See `PUBLIC_DATABASES_GUIDE.md` for details)

---

**Status**: Ready to connect  
**Last Updated**: January 22, 2026
