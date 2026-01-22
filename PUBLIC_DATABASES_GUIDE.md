# Public Databases for AURA Connection Guide

## Overview
This guide covers publicly accessible databases you can connect to AURA for data analysis, visualization, and pipeline execution.

---

## 📊 Free Public Databases

### 1. **Google BigQuery Public Datasets** ⭐ RECOMMENDED
**Best for**: Large-scale data analysis, multiple domains

```
Connection Type: BigQuery (SQL)
Authentication: Google Cloud credentials
Cost: Free tier available (1TB/month query)
Data Types: Public datasets from Google, WHO, NYC, NOAA, etc.

Available Datasets:
├── bigquery-public-data:covid19_* (COVID-19 data)
├── bigquery-public-data:nyc_tlc (NYC taxi/Citi Bike)
├── bigquery-public-data:github_repos (GitHub metadata)
├── bigquery-public-data:world_bank_wdi (World Bank data)
├── bigquery-public-data:noaa_gsod (Weather data)
├── bigquery-public-data:wikipedia (Wikipedia views)
├── bigquery-public-data:fcc_reports (FCC reports)
└── Many more...
```

**Setup for AURA:**
```python
# In aurabackend/database/connection_manager.py
from google.cloud import bigquery

class BigQueryConnector:
    def __init__(self, project_id):
        self.client = bigquery.Client(project=project_id)
    
    def execute_query(self, sql):
        query_job = self.client.query(sql)
        return query_job.result().to_dataframe()
```

**Connection URL:**
```
bigquery://project-id/dataset-name
```

---

### 2. **PostgreSQL Public Databases**

#### PostgreSQL Demo Database (pagila)
```
Host: pgbin.ru
Port: 5432
Database: pagila
User: reader
Password: reader

Description: Movie rental database with sample data
Schema: actor, film, rental, payment, etc.
```

#### AdventureWorks (PostgreSQL Port)
```
Host: demo.postgresqltutorial.com
Port: 5432
Database: dvdrental
User: postgres
Password: postgres

Description: DVD rental business database
Tables: 15+ tables with rich sample data
```

#### Northwind Database (PostgreSQL)
```
Host: localhost (after setup)
Setup: Download from GitHub and restore

Description: Classic business database
- Customers, Products, Orders, Employees
```

---

### 3. **MySQL Public Databases**

#### MySQL Demo Server
```
Host: sql.altibase.com
Port: 3306
Database: EMPLOYEES
User: demo
Password: demo

Description: Employee management database
Tables: employees, departments, salaries, titles
```

#### Sakila Database (Sample)
```
Host: Various public MySQL servers
Database: sakila
User: root
Password: (varies)

Description: Movie rental system
Tables: actor, film, rental, customer, payment
```

---

### 4. **SQLite Public Datasets**

#### Chinook Database
```
Type: SQLite file (download)
Size: ~600 KB
URL: https://github.com/lerocha/chinook-database

Description: Music store database
Tables: artist, album, track, customer, invoice, etc.
```

#### World Database
```
Type: SQLite format
Contents: Countries, cities, languages
Tables: countries (239), cities (4,079)
```

**Easy Integration:**
```python
import sqlite3

conn = sqlite3.connect('chinook.db')
df = pd.read_sql_query("SELECT * FROM customer", conn)
```

---

### 5. **REST API Databases**

#### OMDb (Open Movie Database) ⭐
```
Endpoint: http://www.omdbapi.com/
API Key: Free tier available
Rate Limit: 1,000 requests/day (free)

Query Example:
http://www.omdbapi.com/?t=Inception&apikey=YOUR_KEY

Data Available:
- Movie titles, ratings, plot, cast
- IMDB metadata
- Box office info
```

**AURA Integration:**
```python
import requests

def fetch_movie_data(title, api_key):
    response = requests.get(
        'http://www.omdbapi.com/',
        params={'t': title, 'apikey': api_key}
    )
    return response.json()
```

#### OpenWeather API
```
Endpoint: https://api.openweathermap.org/
Free Tier: 60 calls/minute
Current Data: Temperature, humidity, wind, pressure

AURA Use Case: Weather analysis, trend visualization
```

#### REST Countries API
```
Endpoint: https://restcountries.com/v3.1/all
No Authentication: Completely free
Data: 250+ countries
Fields: name, population, area, capital, currency, language
```

---

### 6. **Kaggle Datasets** (Need Download)
```
URL: https://www.kaggle.com/datasets

Popular Datasets:
├── Titanic (shipwreck data)
├── Iris (flower classification)
├── MNIST (handwritten digits)
├── MovieLens (ratings database)
├── COVID-19 (comprehensive)
├── Stock Market Data
├── Housing Prices
└── 100,000+ more datasets

Access: Download as CSV/JSON, import to AURA
```

---

### 7. **GitHub Public APIs**

#### GitHub REST API
```
Endpoint: https://api.github.com/
Rate Limit: 60 requests/hour (unauthenticated)
                5,000/hour (authenticated)

Available Data:
├── User repositories
├── Repository statistics
├── Commit history
├── Issues and pull requests
├── Releases
└── User activity
```

**AURA Integration:**
```python
import requests

def get_github_repos(username):
    url = f"https://api.github.com/users/{username}/repos"
    response = requests.get(url)
    return response.json()
```

---

### 8. **NASA Public APIs**

#### NASA APOD (Astronomy Picture of the Day)
```
Endpoint: https://api.nasa.gov/
Free API Key: Available
Rate Limit: 1,000 requests/hour

Data Available:
- Daily astronomy images
- Metadata, explanations
- Image URLs
```

#### NASA Earth Imagery
```
Data: Satellite imagery by coordinates
Resolution: Up to 15m
Coverage: Global
Use Case: Environmental analysis, trend visualization
```

---

### 9. **Financial Data APIs**

#### Alpha Vantage
```
Type: Stock market data API
API Key: Free tier available
Rate Limit: 5 requests/minute (free)

Data:
- Stock prices (intraday, daily, weekly, monthly)
- Technical indicators
- Cryptocurrency prices
- Foreign exchange rates

AURA Use Case: Stock analysis, price trends, portfolio tracking
```

**Example:**
```
https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=IBM&apikey=YOUR_KEY
```

#### Finnhub
```
Type: Real-time stock data
Free API: Yes
Rate Limit: 60 requests/minute

Data:
- Stock quotes, OHLC
- Company info
- News sentiment
- Economic calendars
```

---

### 10. **Statistical Databases**

#### World Bank Open Data
```
Endpoint: https://data.worldbank.org/
Format: JSON/XML/CSV
No Authentication: Free

Data Available:
- Economic indicators
- Population statistics
- Development metrics
- Climate data
- Education statistics
```

#### CDC Wonder API
```
Type: U.S. health/mortality data
Free: Yes
Data: Disease surveillance, vital statistics
Use: Health trend analysis
```

#### UN Data
```
Data: Social, economic, environmental statistics
Countries: 193+ UN member states
Years: Historical data (decades)
```

---

## 🔧 How to Connect to AURA

### Option 1: Direct Database Connection

**Frontend UI (DatabaseConnector.tsx):**
```typescript
// Add connection form
const connectionConfig = {
  type: 'postgresql', // or 'mysql', 'bigquery', 'sqlite'
  host: 'pgbin.ru',
  port: 5432,
  database: 'pagila',
  user: 'reader',
  password: 'reader'
};
```

**Backend Setup (database/main.py):**
```python
from sqlalchemy import create_engine

def create_connection(config):
    connection_string = f"{config['type']}://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"
    engine = create_engine(connection_string)
    return engine

# Execute query
def execute_query(engine, sql):
    with engine.connect() as connection:
        result = connection.execute(sql)
        return pd.DataFrame(result.fetchall())
```

---

### Option 2: REST API Integration

**Backend Service (code_generation_service/main.py):**
```python
import requests
import pandas as pd

async def fetch_from_api(api_url, params):
    response = requests.get(api_url, params=params)
    data = response.json()
    
    # Convert to DataFrame
    if isinstance(data, list):
        df = pd.DataFrame(data)
    else:
        df = pd.DataFrame([data])
    
    return df

# Use in pipeline
@app.post("/api/fetch-external-data")
async def fetch_external_data(source: str, query: dict):
    if source == 'omdb':
        return await fetch_from_api('http://www.omdbapi.com/', query)
    elif source == 'weather':
        return await fetch_from_api('https://api.openweathermap.org/', query)
```

---

### Option 3: CSV/JSON File Upload

**Already Supported in AURA:**
```
1. Download dataset from Kaggle/GitHub
2. Upload via FileUpload.tsx component
3. AURA parses and analyzes automatically
```

---

## 📋 Quick Connection Reference

### PostgreSQL Connection Template
```
postgresql://user:password@host:port/database

Examples:
- postgresql://reader:reader@pgbin.ru:5432/pagila
- postgresql://postgres:postgres@demo.postgresqltutorial.com:5432/dvdrental
```

### MySQL Connection Template
```
mysql+pymysql://user:password@host:port/database

Examples:
- mysql+pymysql://demo:demo@sql.altibase.com:3306/employees
```

### BigQuery Connection Template
```
bigquery://project-id/dataset-name
# Requires Google Cloud credentials setup
```

### SQLite Connection Template
```
sqlite:///path/to/database.db

Example:
sqlite:///./chinook.db
```

---

## 🎯 Recommended Setup Steps

### Step 1: Choose a Database
- **Quick Start**: Use SQLite (Chinook) - download and import
- **Large Data**: Use Google BigQuery - millions of rows
- **REST API**: Use OMDb or Alpha Vantage - real-time data

### Step 2: Get Credentials
- If API: Sign up for free tier, get API key
- If SQL: Use provided credentials
- If File: Download CSV/JSON

### Step 3: Update AURA Backend
```python
# Add connection manager for chosen database
# Update database/connection_manager.py
```

### Step 4: Update Frontend
```typescript
// Add connection form fields in DatabaseConnector.tsx
// Match the database type requirements
```

### Step 5: Test Connection
```
1. Use AURA chat: "Connect to [database]"
2. Test query: "Show me the first 10 rows"
3. Create visualization
4. Schedule pipeline job
```

---

## 📊 Popular Dataset Combinations

### Scenario 1: Movie Analysis
```
Database: Sakila or Chinook
Tables: films, actors, ratings
Analysis: Genre trends, actor popularity
Visualization: Bar charts, word clouds
```

### Scenario 2: Economic Analysis
```
Database: World Bank API
Metrics: GDP, unemployment, trade data
Countries: Multiple or single country over time
Visualization: Line charts, geographic heatmaps
```

### Scenario 3: Stock Market Analysis
```
Database: Alpha Vantage API
Data: Historical prices, technical indicators
Period: Days, weeks, months, years
Visualization: Candlestick charts, trend lines
```

### Scenario 4: Health/COVID Data
```
Database: Google BigQuery (covid19_*)
Regions: Countries, states, counties
Metrics: Cases, deaths, recovery
Visualization: Time series, comparative analysis
```

---

## ⚠️ Important Notes

### API Rate Limits
```
- OMDb: 1,000 requests/day (free)
- OpenWeather: 60 calls/minute
- GitHub: 60/hour unauthenticated, 5,000/hour authenticated
- Alpha Vantage: 5 requests/minute (free)
```

### Authentication Requirements
```
✓ No auth needed: REST Countries, World Bank, GitHub (limited)
⚠️ Free API key needed: OMDb, Alpha Vantage, OpenWeather, NASA
✓ Google Account needed: BigQuery (free tier)
```

### Data Privacy
```
All suggested databases are:
✓ Publicly available
✓ Safe for development/learning
✓ No personal data included
✓ Terms of service allow analysis
```

---

## 🚀 Next Steps

### To Get Started Immediately:
1. **Download Chinook SQLite** - Easiest option
   ```
   git clone https://github.com/lerocha/chinook-database.git
   # Use chinook.db file
   ```

2. **Update AURA Database Service**
   ```python
   # Add SQLite support to database/main.py
   ```

3. **Upload to AURA**
   ```
   Frontend → Upload → Select chinook.db
   ```

4. **Start Analyzing**
   ```
   "Show me customer purchases by country"
   "Create a chart of album sales by genre"
   ```

---

## 📚 Additional Resources

### Public Database Directories
- **Kaggle**: https://www.kaggle.com/datasets
- **Google Dataset Search**: https://datasetsearch.research.google.com/
- **GitHub Awesome Datasets**: https://github.com/awesome-datasets/awesome-public-datasets
- **Data.world**: https://data.world/

### API Documentation
- **OpenAPI Hub**: https://openapis.dev/
- **ProgrammableWeb**: https://www.programmableweb.com/
- **RapidAPI**: https://rapidapi.com/

### AURA Integration Examples
- See `ARCHITECTURE.md` for data flow
- See `QUICK_REFERENCE.md` for commands
- Check `aurabackend/database/` for connection examples

---

**Last Updated**: January 22, 2026
**Status**: Ready for Integration
