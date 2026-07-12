-- Идемпотентная загрузка складских событий (та же схема, что и для fact_repair).
INSERT INTO dds.fact_storage (
    device_sk, store_sk, date_sk, device_id, cell_id, storage_event_type, event_ts, qty
)
SELECT
    d.device_sk, st.store_sk, s.event_ts::date, s.device_id, s.cell_id, s.storage_event_type, s.event_ts, s.qty
FROM stg.stg_storage_events s
JOIN dds.dim_device d  ON d.device_id  = s.device_id  AND d.is_current
JOIN dds.dim_store  st ON st.store_code = s.store_code AND st.is_current
WHERE s.event_ts >= :'load_from' AND s.event_ts < :'load_to'
ON CONFLICT (device_id, storage_event_type, event_ts, cell_id) DO NOTHING;
