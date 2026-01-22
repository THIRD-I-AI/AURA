import sqlite3

conn = sqlite3.connect('aurabackend/data/metadata.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cur.fetchall()

print('\nDatabase Tables:')
for t in tables:
    print(f'  - {t[0]}')
print(f'\nTotal: {len(tables)} tables')

# Get column info for each table
print('\nTable Schemas:')
for t in tables:
    table_name = t[0]
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = cur.fetchall()
    print(f'\n  {table_name} ({len(columns)} columns):')
    for col in columns:
        print(f'    - {col[1]} ({col[2]})')

conn.close()
print('\nSchema verification complete')
