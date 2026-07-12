-- Пересчёт агрегированных витрин из текущего состояния DDS.
-- SLA считается по нормативу 4320 минут (3 суток) от приёмки до терминального статуса.

-- mart_repair_sla
WITH ticket_bounds AS (
    SELECT
        fr.repair_ticket_id,
        MIN(fr.event_ts) FILTER (WHERE ds.stage_order = 1) AS received_ts,
        MAX(fr.event_ts) FILTER (WHERE ds.is_terminal)     AS terminal_ts,
        MAX(fr.center_sk) AS center_sk,
        MAX(fr.device_sk) AS device_sk
    FROM dds.fact_repair fr
    JOIN dds.dim_status ds ON ds.status_sk = fr.status_sk
    GROUP BY fr.repair_ticket_id
),
ticket_metrics AS (
    SELECT
        tb.center_sk,
        dd.category,
        tb.terminal_ts::date AS date_sk,
        EXTRACT(EPOCH FROM (tb.terminal_ts - tb.received_ts)) / 60 AS repair_min
    FROM ticket_bounds tb
    JOIN dds.dim_device dd ON dd.device_sk = tb.device_sk
    WHERE tb.received_ts IS NOT NULL AND tb.terminal_ts IS NOT NULL
)
INSERT INTO dm.mart_repair_sla (date_sk, center_sk, category, tickets_total, tickets_overdue, avg_repair_min, sla_share, load_ts)
SELECT
    date_sk, center_sk, category,
    COUNT(*) AS tickets_total,
    COUNT(*) FILTER (WHERE repair_min > 4320) AS tickets_overdue,
    ROUND(AVG(repair_min))::int AS avg_repair_min,
    ROUND(1 - COUNT(*) FILTER (WHERE repair_min > 4320)::numeric / COUNT(*), 3) AS sla_share,
    now()
FROM ticket_metrics
GROUP BY date_sk, center_sk, category
ON CONFLICT (date_sk, center_sk, category) DO UPDATE SET
    tickets_total   = EXCLUDED.tickets_total,
    tickets_overdue = EXCLUDED.tickets_overdue,
    avg_repair_min  = EXCLUDED.avg_repair_min,
    sla_share       = EXCLUDED.sla_share,
    load_ts         = EXCLUDED.load_ts;

-- mart_center_load
WITH daily AS (
    SELECT
        fr.date_sk, fr.center_sk,
        COUNT(DISTINCT fr.repair_ticket_id) FILTER (WHERE NOT ds.is_terminal) AS active_repairs,
        COUNT(DISTINCT fr.repair_ticket_id) FILTER (WHERE ds.stage_order = 1) AS incoming_tickets,
        COUNT(DISTINCT fr.repair_ticket_id) FILTER (WHERE ds.is_terminal)     AS issued_tickets
    FROM dds.fact_repair fr
    JOIN dds.dim_status ds ON ds.status_sk = fr.status_sk
    GROUP BY fr.date_sk, fr.center_sk
)
INSERT INTO dm.mart_center_load (date_sk, center_sk, active_repairs, incoming_tickets, issued_tickets, utilization, load_ts)
SELECT
    d.date_sk, d.center_sk, d.active_repairs, d.incoming_tickets, d.issued_tickets,
    ROUND(d.active_repairs::numeric / NULLIF(cc.capacity, 0), 2),
    now()
FROM daily d
JOIN dds.dim_service_center cc ON cc.center_sk = d.center_sk AND cc.is_current
ON CONFLICT (date_sk, center_sk) DO UPDATE SET
    active_repairs   = EXCLUDED.active_repairs,
    incoming_tickets = EXCLUDED.incoming_tickets,
    issued_tickets   = EXCLUDED.issued_tickets,
    utilization      = EXCLUDED.utilization,
    load_ts          = EXCLUDED.load_ts;

-- mart_storage_turnover
WITH stays AS (
    SELECT
        fs.device_id, fs.cell_id, fs.store_sk,
        MIN(fs.event_ts) FILTER (WHERE fs.storage_event_type = 'INCOMING')                    AS in_ts,
        MIN(fs.event_ts) FILTER (WHERE fs.storage_event_type IN ('ISSUED', 'WRITTEN_OFF'))    AS out_ts
    FROM dds.fact_storage fs
    GROUP BY fs.device_id, fs.cell_id, fs.store_sk
),
daily_counts AS (
    SELECT
        fs.date_sk, fs.store_sk, fs.cell_id,
        COUNT(*) FILTER (WHERE fs.storage_event_type = 'INCOMING')                 AS devices_in,
        COUNT(*) FILTER (WHERE fs.storage_event_type IN ('ISSUED', 'WRITTEN_OFF')) AS devices_out
    FROM dds.fact_storage fs
    GROUP BY fs.date_sk, fs.store_sk, fs.cell_id
),
stay_avg AS (
    SELECT store_sk, cell_id, AVG(EXTRACT(EPOCH FROM (out_ts - in_ts)) / 86400) AS avg_stay_days
    FROM stays
    WHERE in_ts IS NOT NULL AND out_ts IS NOT NULL
    GROUP BY store_sk, cell_id
)
INSERT INTO dm.mart_storage_turnover (date_sk, store_sk, cell_id, devices_in, devices_out, avg_stay_days, cell_utilization, load_ts)
SELECT
    dc.date_sk, dc.store_sk, dc.cell_id, dc.devices_in, dc.devices_out,
    COALESCE(ROUND(sa.avg_stay_days, 2), 0),
    ROUND(dc.devices_in::numeric / NULLIF(st.total_cells, 0), 2),
    now()
FROM daily_counts dc
LEFT JOIN stay_avg sa ON sa.store_sk = dc.store_sk AND sa.cell_id = dc.cell_id
JOIN dds.dim_store st ON st.store_sk = dc.store_sk AND st.is_current
ON CONFLICT (date_sk, store_sk, cell_id) DO UPDATE SET
    devices_in       = EXCLUDED.devices_in,
    devices_out      = EXCLUDED.devices_out,
    avg_stay_days    = EXCLUDED.avg_stay_days,
    cell_utilization = EXCLUDED.cell_utilization,
    load_ts          = EXCLUDED.load_ts;
