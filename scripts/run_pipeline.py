"""
Оркестратор полного цикла загрузки DWH через psql — воспроизводит тот же граф
задач, что и dags/dwh_load_pipeline.py (ensure_partitions -> load_facts ->
dq_check -> build_marts -> reconciliation), но выполняется одним скриптом без
поднятия полного стека Airflow. Используется для:
  1) первоначальной загрузки данных из CSV в стенд;
  2) проверки идемпотентности — при повторном запуске на том же окне данных
     число строк в фактах не должно измениться.

Требует переменные окружения:
  PGHOST (по умолчанию localhost), PGPORT (по умолчанию 5432),
  PGDATABASE (по умолчанию dwh_practice), PGUSER (по умолчанию dwh_practice),
  PGPASSWORD (обязательно), PSQL_PATH (путь к psql.exe).

Пути с кириллицей могут ломать кодировку аргументов psql на Windows, поэтому
перед \\copy CSV временно копируются в ASCII-путь.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SQL_DIR = os.path.join(ROOT, "sql")
DATA_DIR = os.path.join(ROOT, "data")

PSQL = os.environ.get("PSQL_PATH", r"C:\Program Files\PostgreSQL\16\bin\psql.exe")
PGHOST = os.environ.get("PGHOST", "localhost")
PGPORT = os.environ.get("PGPORT", "5432")
PGDATABASE = os.environ.get("PGDATABASE", "dwh_practice")
PGUSER = os.environ.get("PGUSER", "dwh_practice")

SCHEMA_FILES = [
    "00_schema_staging.sql",
    "01_schema_dds.sql",
    "02_partition_helper.sql",
    "03_schema_marts.sql",
]

STAGING_TABLES = {
    "stg.stg_devices": "stg_devices.csv",
    "stg.stg_customers": "stg_customers.csv",
    "stg.stg_service_centers": "stg_service_centers.csv",
    "stg.stg_stores": "stg_stores.csv",
    "stg.stg_statuses": "stg_statuses.csv",
    "stg.stg_repair_events": "stg_repair_events.csv",
    "stg.stg_storage_events": "stg_storage_events.csv",
}


def env():
    e = os.environ.copy()
    e["PGCLIENTENCODING"] = "UTF8"
    return e


def run_psql(args, check=True):
    base = [PSQL, "-h", PGHOST, "-p", PGPORT, "-d", PGDATABASE, "-U", PGUSER, "-v", "ON_ERROR_STOP=1"]
    cmd = base + args
    result = subprocess.run(cmd, env=env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        if check:
            raise RuntimeError(f"psql failed: {' '.join(args)}")
    return result


def run_sql_file(relative_path, variables=None):
    args = ["-f", os.path.join(ROOT, relative_path)]
    if variables:
        for k, v in variables.items():
            args += ["-v", f"{k}={v}"]
    print(f"\n=== {relative_path} ===")
    return run_psql(args)


def apply_schema():
    for f in SCHEMA_FILES:
        run_sql_file(os.path.join("sql", f))


def stage_csv_to_ascii_dir():
    ascii_dir = tempfile.mkdtemp(prefix="dwh_stage_")
    for fname in STAGING_TABLES.values():
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(ascii_dir, fname)
        shutil.copyfile(src, dst)
    return ascii_dir


def load_staging(ascii_dir):
    for table, fname in STAGING_TABLES.items():
        path = os.path.join(ascii_dir, fname).replace("\\", "/")
        print(f"\n=== \\copy {table} FROM {fname} ===")
        run_psql(["-c", f"TRUNCATE TABLE {table};"])
        run_psql(["-c", rf"\copy {table} FROM '{path}' WITH (FORMAT csv, HEADER true)"])


def load_dims():
    run_sql_file(os.path.join("sql", "04_load_dims.sql"))


def ensure_partitions(load_from, load_to):
    for table in ("dds.fact_repair", "dds.fact_storage"):
        run_psql(["-c", f"SELECT dds.create_missing_partitions('{table}', DATE '{load_from}', 366);"])


def load_facts(load_from, load_to):
    run_sql_file(os.path.join("sql", "05_load_fact_repair.sql"), {"load_from": load_from, "load_to": load_to})
    run_sql_file(os.path.join("sql", "05_load_fact_storage.sql"), {"load_from": load_from, "load_to": load_to})


def dq_check():
    run_sql_file(os.path.join("sql", "07_dq_status_order_check.sql"))


def build_marts():
    run_sql_file(os.path.join("sql", "06_build_marts.sql"))


def reconciliation():
    run_sql_file(os.path.join("sql", "08_reconciliation_check.sql"))


def row_counts():
    # ORDER BY t — иначе UNION ALL без сортировки может вернуть строки в разном
    # порядке между прогонами, и наивное сравнение строк даст ложный MISMATCH.
    q = """
    SELECT * FROM (
        SELECT 'fact_repair' AS t, COUNT(*) FROM dds.fact_repair
        UNION ALL SELECT 'fact_storage', COUNT(*) FROM dds.fact_storage
        UNION ALL SELECT 'fact_repair_quarantine', COUNT(*) FROM dds.fact_repair_quarantine
        UNION ALL SELECT 'mart_repair_sla', COUNT(*) FROM dm.mart_repair_sla
        UNION ALL SELECT 'mart_center_load', COUNT(*) FROM dm.mart_center_load
        UNION ALL SELECT 'mart_storage_turnover', COUNT(*) FROM dm.mart_storage_turnover
    ) x ORDER BY t;
    """
    r = run_psql(["-c", q])
    return r.stdout


def month_windows(load_from="2025-01-01", load_to="2026-01-01"):
    import datetime
    start = datetime.date.fromisoformat(load_from)
    end = datetime.date.fromisoformat(load_to)
    windows = []
    cur = start
    while cur < end:
        nxt = datetime.date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        nxt = min(nxt, end)
        windows.append((cur.isoformat(), nxt.isoformat()))
        cur = nxt
    return windows


def full_run(load_from="2025-01-01", load_to="2026-01-01", reload_staging=True):
    apply_schema()
    if reload_staging:
        ascii_dir = stage_csv_to_ascii_dir()
        try:
            load_staging(ascii_dir)
        finally:
            shutil.rmtree(ascii_dir, ignore_errors=True)
        load_dims()

    # Загрузка помесячно: реалистичнее (так работал бы реальный ежедневный/
    # ежемесячный инкремент) и не упирается в память Postgres на огромном
    # единичном INSERT с проверкой FK по 1.5+ млн строк за раз.
    ensure_partitions(load_from, load_to)
    for m_from, m_to in month_windows(load_from, load_to):
        print(f"\n--- loading window {m_from} .. {m_to} ---")
        load_facts(m_from, m_to)

    dq_check()
    build_marts()
    reconciliation()
    return row_counts()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    if mode == "full":
        counts = full_run()
        print("\n=== Row counts after run #1 ===")
        print(counts)

    elif mode == "idempotency-check":
        print("### RUN 1 (full load) ###")
        counts1 = full_run(reload_staging=True)
        print(counts1)

        print("### RUN 2 (same window, staging reloaded, facts must not duplicate) ###")
        counts2 = full_run(reload_staging=True)
        print(counts2)

        if counts1 == counts2:
            print("\nIDEMPOTENCY CHECK: OK — row counts identical after second run.")
        else:
            print("\nIDEMPOTENCY CHECK: MISMATCH!")
            print("Run 1:\n", counts1)
            print("Run 2:\n", counts2)
            sys.exit(1)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
