-- Реконсиляция: сумма tickets_total по витрине SLA должна совпадать с числом
-- завершённых заявок в fact_repair. Расхождение — сигнал ошибки в пайплайне,
-- поэтому проверка оформлена как блок, который реально проваливает задачу.
DO $$
DECLARE
    v_mart_total bigint;
    v_fact_total bigint;
BEGIN
    SELECT COALESCE(SUM(tickets_total), 0) INTO v_mart_total FROM dm.mart_repair_sla;

    SELECT COUNT(DISTINCT fr.repair_ticket_id) INTO v_fact_total
    FROM dds.fact_repair fr
    JOIN dds.dim_status ds ON ds.status_sk = fr.status_sk
    WHERE ds.is_terminal;

    IF v_mart_total <> v_fact_total THEN
        RAISE EXCEPTION 'Reconciliation failed: mart_repair_sla.tickets_total sum (%) != fact_repair terminal ticket count (%)',
            v_mart_total, v_fact_total;
    END IF;

    RAISE NOTICE 'Reconciliation OK: % завершённых заявок совпадает между fact_repair и mart_repair_sla', v_fact_total;
END $$;
