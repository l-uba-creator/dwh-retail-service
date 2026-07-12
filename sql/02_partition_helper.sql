-- Утилита для DAG: создаёт недостающие месячные партиции родительской таблицы
-- на диапазон [run_date; run_date + days_ahead]. Идемпотентна: повторный вызов
-- ничего не ломает, если партиции уже существуют.
CREATE OR REPLACE FUNCTION dds.create_missing_partitions(
    p_parent_table text,
    p_run_date     date,
    p_days_ahead   int
) RETURNS void AS $$
DECLARE
    v_end          date;
    v_month_start  date;
    v_month_end    date;
    v_partition    text;
    v_schema       text;
    v_table        text;
BEGIN
    v_schema := split_part(p_parent_table, '.', 1);
    v_table  := split_part(p_parent_table, '.', 2);
    v_month_start := date_trunc('month', p_run_date)::date;
    v_end := date_trunc('month', p_run_date + make_interval(days => p_days_ahead))::date;

    WHILE v_month_start <= v_end LOOP
        v_month_end := (v_month_start + interval '1 month')::date;
        v_partition := v_table || '_' || to_char(v_month_start, 'YYYY_MM');

        IF NOT EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = v_schema AND c.relname = v_partition
        ) THEN
            EXECUTE format(
                'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                v_schema, v_partition, v_schema, v_table, v_month_start, v_month_end
            );
        END IF;

        v_month_start := v_month_end;
    END LOOP;
END;
$$ LANGUAGE plpgsql;
