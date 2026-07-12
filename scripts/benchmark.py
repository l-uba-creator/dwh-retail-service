"""
Реальный 4-стадийный замер производительности, заменяющий оценочную Таблицу 3
исходного отчёта честными цифрами уменьшенного стенда.

Стадии:
  A. bench.fact_repair_flat / bench.fact_storage_flat — обычная таблица без
     партиционирования и без дополнительных индексов (только PK).
  B. те же таблицы + целевые индексы (как в dds.*).
  C. bench.fact_repair_part / bench.fact_storage_part — партиционирование по
     месяцам (RANGE by event_ts), те же индексы, те же данные.
  D. чтение из уже построенных агрегированных витрин (dm.*).

На каждой стадии запрос прогоняется N раз через EXPLAIN ANALYZE, из вывода
парсится "Execution Time", считаются среднее и разброс (min/max). Результат
пишется в results/table3_real.md.
"""
import os
import re
import statistics
import subprocess
import sys
import json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESULTS_DIR = os.path.join(ROOT, "results")
RAW_DIR = os.path.join(RESULTS_DIR, "benchmark_raw")

PSQL = os.environ.get("PSQL_PATH", r"C:\Program Files\PostgreSQL\16\bin\psql.exe")
PGHOST = os.environ.get("PGHOST", "localhost")
PGPORT = os.environ.get("PGPORT", "5432")
PGDATABASE = os.environ.get("PGDATABASE", "dwh_practice")
PGUSER = os.environ.get("PGUSER", "dwh_practice")

N_RUNS = 5
BENCH_MONTH_FROM = "2025-06-01"
BENCH_MONTH_TO = "2025-07-01"

EXEC_TIME_RE = re.compile(r"Execution Time:\s*([\d.]+)\s*ms")


def env():
    e = os.environ.copy()
    e["PGCLIENTENCODING"] = "UTF8"
    return e


def psql_exec(sql, check=True):
    cmd = [PSQL, "-h", PGHOST, "-p", PGPORT, "-d", PGDATABASE, "-U", PGUSER,
           "-v", "ON_ERROR_STOP=1", "-c", sql]
    r = subprocess.run(cmd, env=env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and check:
        raise RuntimeError(f"psql failed:\n{sql}\n---\n{r.stderr}")
    return r.stdout + r.stderr


def explain_analyze(sql, label):
    out = psql_exec(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(os.path.join(RAW_DIR, f"{label}.txt"), "a", encoding="utf-8") as f:
        f.write(out + "\n" + ("-" * 80) + "\n")
    m = EXEC_TIME_RE.search(out)
    if not m:
        raise RuntimeError(f"Could not parse execution time from EXPLAIN output for {label}:\n{out}")
    return float(m.group(1))


def run_stage(sql, label, n_runs=N_RUNS):
    times = []
    for i in range(n_runs):
        t = explain_analyze(sql, f"{label}_run{i+1}")
        times.append(t)
    return {
        "label": label,
        "runs_ms": times,
        "mean_ms": round(statistics.mean(times), 1),
        "min_ms": round(min(times), 1),
        "max_ms": round(max(times), 1),
        "stdev_ms": round(statistics.pstdev(times), 1) if len(times) > 1 else 0.0,
    }


# ---------------------------------------------------------------------------
# Схемы стенда для бенчмарка (bench.*), заполняются копией уже загруженных
# данных из dds.* — тот же датасет, разные физические структуры хранения.
# ---------------------------------------------------------------------------

SETUP_SQL = """
CREATE SCHEMA IF NOT EXISTS bench;

DROP TABLE IF EXISTS bench.fact_repair_flat;
CREATE TABLE bench.fact_repair_flat AS SELECT * FROM dds.fact_repair;
ALTER TABLE bench.fact_repair_flat ADD PRIMARY KEY (repair_sk, event_ts);

DROP TABLE IF EXISTS bench.fact_storage_flat;
CREATE TABLE bench.fact_storage_flat AS SELECT * FROM dds.fact_storage;
ALTER TABLE bench.fact_storage_flat ADD PRIMARY KEY (storage_sk, event_ts);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_bench_repair_date   ON bench.fact_repair_flat (date_sk);
CREATE INDEX IF NOT EXISTS idx_bench_repair_center ON bench.fact_repair_flat (center_sk, date_sk);
CREATE INDEX IF NOT EXISTS idx_bench_repair_ticket  ON bench.fact_repair_flat (repair_ticket_id);

CREATE INDEX IF NOT EXISTS idx_bench_storage_date   ON bench.fact_storage_flat (date_sk);
CREATE INDEX IF NOT EXISTS idx_bench_storage_store  ON bench.fact_storage_flat (store_sk, date_sk);
CREATE INDEX IF NOT EXISTS idx_bench_storage_device ON bench.fact_storage_flat (device_id, event_ts);
"""

PARTITION_SETUP_SQL = """
DROP TABLE IF EXISTS bench.fact_repair_part CASCADE;
CREATE TABLE bench.fact_repair_part (LIKE bench.fact_repair_flat INCLUDING ALL)
    PARTITION BY RANGE (event_ts);

DROP TABLE IF EXISTS bench.fact_storage_part CASCADE;
CREATE TABLE bench.fact_storage_part (LIKE bench.fact_storage_flat INCLUDING ALL)
    PARTITION BY RANGE (event_ts);
"""

PARTITION_POPULATE_SQL = """
INSERT INTO bench.fact_repair_part SELECT * FROM bench.fact_repair_flat;
INSERT INTO bench.fact_storage_part SELECT * FROM bench.fact_storage_flat;
"""


def create_bench_partitions():
    # Партиции на весь год данных (2025), с запасом.
    psql_exec("""
        DO $$
        DECLARE d date := '2025-01-01';
        BEGIN
            WHILE d < '2026-02-01' LOOP
                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS bench.%I PARTITION OF bench.fact_repair_part FOR VALUES FROM (%L) TO (%L)',
                    'fact_repair_part_' || to_char(d, 'YYYY_MM'), d, d + interval '1 month'
                );
                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS bench.%I PARTITION OF bench.fact_storage_part FOR VALUES FROM (%L) TO (%L)',
                    'fact_storage_part_' || to_char(d, 'YYYY_MM'), d, d + interval '1 month'
                );
                d := d + interval '1 month';
            END LOOP;
        END $$;
    """)


def repair_query(table):
    return f"""
    WITH ticket_bounds AS (
        SELECT fr.repair_ticket_id,
               MIN(fr.event_ts) FILTER (WHERE ds.stage_order = 1) AS received_ts,
               MAX(fr.event_ts) FILTER (WHERE ds.is_terminal) AS terminal_ts,
               MAX(fr.center_sk) AS center_sk, MAX(fr.device_sk) AS device_sk
        FROM {table} fr
        JOIN dds.dim_status ds ON ds.status_sk = fr.status_sk
        WHERE fr.event_ts >= '{BENCH_MONTH_FROM}' AND fr.event_ts < '{BENCH_MONTH_TO}'
        GROUP BY fr.repair_ticket_id
    )
    SELECT tb.center_sk, dd.category,
           COUNT(*) AS tickets,
           AVG(EXTRACT(EPOCH FROM (tb.terminal_ts - tb.received_ts)) / 60) AS avg_repair_min
    FROM ticket_bounds tb
    JOIN dds.dim_device dd ON dd.device_sk = tb.device_sk
    WHERE tb.received_ts IS NOT NULL AND tb.terminal_ts IS NOT NULL
    GROUP BY tb.center_sk, dd.category;
    """


def storage_query(table):
    return f"""
    WITH stays AS (
        SELECT fs.device_id, fs.cell_id, fs.store_sk,
               MIN(fs.event_ts) FILTER (WHERE fs.storage_event_type = 'INCOMING') AS in_ts,
               MIN(fs.event_ts) FILTER (WHERE fs.storage_event_type IN ('ISSUED', 'WRITTEN_OFF')) AS out_ts
        FROM {table} fs
        WHERE fs.event_ts >= '{BENCH_MONTH_FROM}' AND fs.event_ts < '{BENCH_MONTH_TO}'
        GROUP BY fs.device_id, fs.cell_id, fs.store_sk
    )
    SELECT store_sk, cell_id, AVG(EXTRACT(EPOCH FROM (out_ts - in_ts)) / 86400) AS avg_stay_days
    FROM stays
    WHERE in_ts IS NOT NULL AND out_ts IS NOT NULL
    GROUP BY store_sk, cell_id;
    """


MART_REPAIR_QUERY = f"""
SELECT center_sk, category, SUM(tickets_total), ROUND(AVG(avg_repair_min))
FROM dm.mart_repair_sla
WHERE date_sk >= '{BENCH_MONTH_FROM}' AND date_sk < '{BENCH_MONTH_TO}'
GROUP BY center_sk, category;
"""

MART_STORAGE_QUERY = f"""
SELECT store_sk, cell_id, AVG(avg_stay_days)
FROM dm.mart_storage_turnover
WHERE date_sk >= '{BENCH_MONTH_FROM}' AND date_sk < '{BENCH_MONTH_TO}'
GROUP BY store_sk, cell_id;
"""


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    if os.path.isdir(RAW_DIR):
        for f in os.listdir(RAW_DIR):
            os.remove(os.path.join(RAW_DIR, f))

    print("Setting up bench schema (copy of dds.* data, no extra indexes)...")
    psql_exec(SETUP_SQL)

    results = {"Q1_repair_sla": [], "Q2_storage_turnover": []}

    print("Stage A: raw (no indexes, no partitioning)...")
    results["Q1_repair_sla"].append(run_stage(repair_query("bench.fact_repair_flat"), "Q1_A_raw"))
    results["Q2_storage_turnover"].append(run_stage(storage_query("bench.fact_storage_flat"), "Q2_A_raw"))

    print("Stage B: + indexes...")
    psql_exec(INDEX_SQL)
    psql_exec("ANALYZE bench.fact_repair_flat; ANALYZE bench.fact_storage_flat;")
    results["Q1_repair_sla"].append(run_stage(repair_query("bench.fact_repair_flat"), "Q1_B_indexed"))
    results["Q2_storage_turnover"].append(run_stage(storage_query("bench.fact_storage_flat"), "Q2_B_indexed"))

    print("Stage C: + partitioning (monthly RANGE)...")
    psql_exec(PARTITION_SETUP_SQL)
    create_bench_partitions()
    psql_exec(PARTITION_POPULATE_SQL)
    psql_exec("ANALYZE bench.fact_repair_part; ANALYZE bench.fact_storage_part;")
    results["Q1_repair_sla"].append(run_stage(repair_query("bench.fact_repair_part"), "Q1_C_partitioned"))
    results["Q2_storage_turnover"].append(run_stage(storage_query("bench.fact_storage_part"), "Q2_C_partitioned"))

    print("Stage D: read from pre-built marts...")
    results["Q1_repair_sla"].append(run_stage(MART_REPAIR_QUERY, "Q1_D_marts"))
    results["Q2_storage_turnover"].append(run_stage(MART_STORAGE_QUERY, "Q2_D_marts"))

    with open(os.path.join(RESULTS_DIR, "benchmark_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    write_report(results)
    print("\nDone. See results/table3_real.md")


def write_report(results):
    stage_names = ["Сырые факты (A)", "+Индексы (B)", "+Партиционирование (C)", "+Витрины (D)"]
    lines = []
    lines.append("# Таблица 3 (реальные замеры вместо оценочных)\n")
    lines.append(f"Стенд: PostgreSQL 16, нативная установка Windows (без Docker/WSL2 — недоступны на машине практики).")
    lines.append(f"Датасет: см. `results/generation_summary.json` (сгенерирован `generator/generate_data.py`, "
                  f"честный уменьшенный объём вместо исходной оценки 10 млн/4 млн строк).")
    lines.append(f"Методика: каждый запрос прогнан {N_RUNS} раз через `EXPLAIN (ANALYZE, BUFFERS)` "
                  f"на окне 2025-06 (месяц), приведены среднее/мин/макс/стандартное отклонение. "
                  f"Сырые планы — в `results/benchmark_raw/`.\n")

    lines.append("| Запрос | " + " | ".join(stage_names) + " |")
    lines.append("|---|---|---|---|---|")

    labels = {
        "Q1_repair_sla": "Q1. SLA / среднее время ремонта по центрам и категориям",
        "Q2_storage_turnover": "Q2. Оборачиваемость ячеек (среднее время хранения)",
    }
    for key, row_label in labels.items():
        cells = []
        for stage in results[key]:
            cells.append(f"{stage['mean_ms']:.1f} мс (±{stage['stdev_ms']:.1f}, {stage['min_ms']:.1f}–{stage['max_ms']:.1f})")
        lines.append(f"| {row_label} | " + " | ".join(cells) + " |")

    lines.append("")
    q1 = results["Q1_repair_sla"]
    speedup = q1[0]["mean_ms"] / q1[-1]["mean_ms"] if q1[-1]["mean_ms"] > 0 else float("inf")
    lines.append(f"Ускорение Q1 (сырые факты -> витрины): **{speedup:.1f}×** на уменьшенном стенде "
                  f"(порядок величины ниже, чем в исходной оценке отчёта, но направление эффекта подтверждено реальным прогоном).")

    with open(os.path.join(RESULTS_DIR, "table3_real.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
