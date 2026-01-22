"""Create a test SQLite database with sample sales data"""
import sqlite3
import os

# Create data directory if it doesn't exist
os.makedirs('data', exist_ok=True)

# Create database and table
conn = sqlite3.connect('data/test_analytics.db')
cursor = conn.cursor()

# Create sales table
cursor.execute('''
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    revenue REAL NOT NULL,
    quantity INTEGER NOT NULL,
    sale_date DATE NOT NULL
)
''')

# Insert sample data
sample_data = [
    ('Laptop', 1200.00, 15, '2026-01-15'),
    ('Mouse', 25.00, 50, '2026-01-16'),
    ('Keyboard', 75.00, 30, '2026-01-17'),
    ('Monitor', 300.00, 20, '2026-01-18'),
    ('Laptop', 1200.00, 12, '2026-01-19'),
    ('Headphones', 80.00, 25, '2026-01-20'),
    ('Mouse', 25.00, 40, '2026-01-21'),
]

cursor.executemany('''
INSERT INTO sales (product_name, revenue, quantity, sale_date)
VALUES (?, ?, ?, ?)
''', sample_data)

conn.commit()

# Verify data
cursor.execute('SELECT COUNT(*) FROM sales')
count = cursor.fetchone()[0]
print(f"Created test database with {count} records")

cursor.execute('SELECT product_name, SUM(revenue) as total FROM sales GROUP BY product_name ORDER BY total DESC')
for row in cursor.fetchall():
    print(f"  {row[0]}: ${row[1]:.2f}")

conn.close()
print("\nTest database created successfully at data/test_analytics.db")
