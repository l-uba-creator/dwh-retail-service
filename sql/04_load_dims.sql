-- Первичная загрузка справочников из staging в DDS (SCD Type 2: вставка текущей версии).
INSERT INTO dds.dim_device (device_id, category, brand, model, serial_number, warranty_flag)
SELECT s.device_id, s.category, s.brand, s.model, s.serial_number, s.warranty_flag
FROM stg.stg_devices s
WHERE NOT EXISTS (
    SELECT 1 FROM dds.dim_device d WHERE d.device_id = s.device_id AND d.is_current
);

INSERT INTO dds.dim_customer (customer_id, full_name, phone, email, city, segment)
SELECT s.customer_id, s.full_name, s.phone, s.email, s.city, s.segment
FROM stg.stg_customers s
WHERE NOT EXISTS (
    SELECT 1 FROM dds.dim_customer d WHERE d.customer_id = s.customer_id AND d.is_current
);

INSERT INTO dds.dim_service_center (center_code, name, city, address, capacity, is_active)
SELECT s.center_code, s.name, s.city, s.address, s.capacity, s.is_active
FROM stg.stg_service_centers s
WHERE NOT EXISTS (
    SELECT 1 FROM dds.dim_service_center d WHERE d.center_code = s.center_code AND d.is_current
);

INSERT INTO dds.dim_store (store_code, name, city, address, total_cells)
SELECT s.store_code, s.name, s.city, s.address, s.total_cells
FROM stg.stg_stores s
WHERE NOT EXISTS (
    SELECT 1 FROM dds.dim_store d WHERE d.store_code = s.store_code AND d.is_current
);

INSERT INTO dds.dim_status (status_code, status_name, stage_order, is_terminal)
SELECT s.status_code, s.status_name, s.stage_order, s.is_terminal
FROM stg.stg_statuses s
ON CONFLICT (status_code) DO NOTHING;

-- DimDate — генерируется на весь диапазон данных (2025-01-01 .. 2025-12-31).
INSERT INTO dds.dim_date (date_sk, full_date, day_of_week, day_num, week_num, month_num, quarter_num, year_num, is_weekend, is_holiday)
SELECT
    d::date,
    d::date,
    EXTRACT(ISODOW FROM d)::smallint,
    EXTRACT(DAY FROM d)::smallint,
    EXTRACT(WEEK FROM d)::smallint,
    EXTRACT(MONTH FROM d)::smallint,
    EXTRACT(QUARTER FROM d)::smallint,
    EXTRACT(YEAR FROM d)::smallint,
    EXTRACT(ISODOW FROM d) IN (6, 7),
    FALSE
FROM generate_series('2025-01-01'::date, '2025-12-31'::date, interval '1 day') d
ON CONFLICT (date_sk) DO NOTHING;
