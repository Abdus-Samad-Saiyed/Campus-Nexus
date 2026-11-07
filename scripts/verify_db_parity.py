import sqlite3
import psycopg
import sys

def verify_databases():
    sqlite_conn = sqlite3.connect('db.sqlite3')
    s_cur = sqlite_conn.cursor()
    s_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    sqlite_tables = sorted([r[0] for r in s_cur.fetchall()])

    pg_url = 'postgres://campus_admin:CampusPass123!@127.0.0.1:5432/campus_nexus'
    try:
        pg_conn = psycopg.connect(pg_url)
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}")
        return False
        
    p_cur = pg_conn.cursor()
    p_cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
    pg_tables = sorted([r[0] for r in p_cur.fetchall()])

    print("=== Tables in SQLite vs PostgreSQL ===")
    print(f"SQLite table count: {len(sqlite_tables)}")
    print(f"PostgreSQL table count: {len(pg_tables)}")

    common_tables = sorted(set(sqlite_tables).intersection(set(pg_tables)))

    print(f"\n{'Table Name':<35} | {'SQLite Count':<12} | {'Postgres Count':<14} | Match?")
    print("-" * 75)
    
    diffs = []
    for t in common_tables:
        s_cur.execute(f'SELECT count(*) FROM "{t}"')
        s_cnt = s_cur.fetchone()[0]
        p_cur.execute(f'SELECT count(*) FROM "{t}"')
        p_cnt = p_cur.fetchone()[0]
        
        status = "MATCH" if s_cnt == p_cnt else f"DIFF ({s_cnt} vs {p_cnt})"
        if s_cnt != p_cnt:
            diffs.append((t, s_cnt, p_cnt))
        print(f"{t:<35} | {s_cnt:<12} | {p_cnt:<14} | {status}")
        
    # Check if any tables exist only in one
    sqlite_only = set(sqlite_tables) - set(pg_tables)
    pg_only = set(pg_tables) - set(sqlite_tables)
    if sqlite_only:
        print(f"\nTables only in SQLite: {sqlite_only}")
    if pg_only:
        print(f"\nTables only in Postgres: {pg_only}")

    print("\nSummary of differences:")
    if not diffs and not sqlite_only:
        print("ALL common tables match perfectly!")
    else:
        for t, s_c, p_c in diffs:
            print(f"  {t}: SQLite={s_c}, Postgres={p_c}")
            
    return diffs

if __name__ == '__main__':
    verify_databases()
