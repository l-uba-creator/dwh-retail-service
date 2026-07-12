-- ИСПРАВЛЕНО (см. критическое замечание к Приложению ПА.7):
-- исходный вариант вызывал LAG() прямо в WHERE, что в PostgreSQL не выполняется —
-- оконные функции запрещены на этапе WHERE, потому что WHERE фильтрует строки ДО
-- вычисления оконных функций. Правильный порядок: сначала посчитать LAG(stage_order)
-- в CTE, затем отфильтровать аномалии во внешнем запросе.
WITH ordered AS (
    SELECT
        fr.repair_ticket_id,
        ds.status_code,
        fr.event_ts,
        ds.stage_order,
        LAG(ds.stage_order) OVER (PARTITION BY fr.repair_ticket_id ORDER BY fr.event_ts) AS prev_stage_order
    FROM dds.fact_repair fr
    JOIN dds.dim_status ds ON ds.status_sk = fr.status_sk
)
INSERT INTO dds.fact_repair_quarantine (repair_ticket_id, status_code, event_ts, reason)
SELECT repair_ticket_id, status_code, event_ts, 'STATUS_ORDER_ANOMALY'
FROM ordered
WHERE prev_stage_order IS NOT NULL
  AND stage_order < prev_stage_order
ON CONFLICT (repair_ticket_id, event_ts, reason) DO NOTHING;
