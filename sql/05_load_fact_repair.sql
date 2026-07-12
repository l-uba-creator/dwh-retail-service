-- Идемпотентная загрузка событий ремонта: повторный запуск на том же окне
-- не создаёт дубликатов (ON CONFLICT DO NOTHING по бизнес-ключу события).
-- Параметры :load_from / :load_to задаются при вызове psql -v.
INSERT INTO dds.fact_repair (
    repair_ticket_id, device_sk, customer_sk, center_sk,
    status_sk, date_sk, event_ts, duration_since_prev_min
)
SELECT
    s.repair_ticket_id, d.device_sk, c.customer_sk, sc.center_sk,
    st.status_sk, s.event_ts::date, s.event_ts, s.duration_since_prev_min
FROM stg.stg_repair_events s
JOIN dds.dim_device         d  ON d.device_id    = s.device_id   AND d.is_current
JOIN dds.dim_customer       c  ON c.customer_id  = s.customer_id AND c.is_current
JOIN dds.dim_service_center sc ON sc.center_code  = s.center_code AND sc.is_current
JOIN dds.dim_status         st ON st.status_code  = s.status_code
WHERE s.event_ts >= :'load_from' AND s.event_ts < :'load_to'
ON CONFLICT (repair_ticket_id, status_sk, event_ts) DO NOTHING;

-- Записи, у которых не нашлись ссылки на измерения, уходят в карантин, а не теряются.
INSERT INTO dds.fact_repair_quarantine (repair_ticket_id, device_id, customer_id, center_code, status_code, event_ts, reason)
SELECT s.repair_ticket_id, s.device_id, s.customer_id, s.center_code, s.status_code, s.event_ts, 'UNRESOLVED_REFERENCE'
FROM stg.stg_repair_events s
WHERE s.event_ts >= :'load_from' AND s.event_ts < :'load_to'
  AND (
        NOT EXISTS (SELECT 1 FROM dds.dim_device d WHERE d.device_id = s.device_id AND d.is_current)
        OR NOT EXISTS (SELECT 1 FROM dds.dim_customer c WHERE c.customer_id = s.customer_id AND c.is_current)
        OR NOT EXISTS (SELECT 1 FROM dds.dim_service_center sc WHERE sc.center_code = s.center_code AND sc.is_current)
      )
ON CONFLICT (repair_ticket_id, event_ts, reason) DO NOTHING;
