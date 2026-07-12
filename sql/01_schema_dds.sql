-- Слой DDS: модель "факт/измерение", суррогатные ключи, SCD Type 2 для справочников.
CREATE SCHEMA IF NOT EXISTS dds;

-- DimDate — календарное измерение
CREATE TABLE IF NOT EXISTS dds.dim_date (
    date_sk     DATE PRIMARY KEY,
    full_date   DATE NOT NULL,
    day_of_week SMALLINT NOT NULL,      -- 1=Пн ... 7=Вс
    day_num     SMALLINT NOT NULL,
    week_num    SMALLINT NOT NULL,      -- ISO-неделя
    month_num   SMALLINT NOT NULL,
    quarter_num SMALLINT NOT NULL,
    year_num    SMALLINT NOT NULL,
    is_weekend  BOOLEAN NOT NULL,
    is_holiday  BOOLEAN NOT NULL DEFAULT FALSE
);

-- DimDevice — справочник техники (SCD Type 2)
CREATE TABLE IF NOT EXISTS dds.dim_device (
    device_sk     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id     VARCHAR(64) NOT NULL,
    category      VARCHAR(100),
    brand         VARCHAR(100),
    model         VARCHAR(200),
    serial_number VARCHAR(200),
    warranty_flag BOOLEAN,
    valid_from    TIMESTAMP NOT NULL DEFAULT '-infinity',
    valid_to      TIMESTAMP NOT NULL DEFAULT 'infinity',
    is_current    BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (device_id, valid_from)
);
CREATE INDEX IF NOT EXISTS idx_dim_device_bk ON dds.dim_device (device_id) WHERE is_current;

-- DimCustomer — справочник клиентов (SCD Type 2)
CREATE TABLE IF NOT EXISTS dds.dim_customer (
    customer_sk BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id VARCHAR(64) NOT NULL,
    full_name   VARCHAR(300),
    phone       VARCHAR(30),
    email       VARCHAR(200),
    city        VARCHAR(100),
    segment     VARCHAR(50),
    valid_from  TIMESTAMP NOT NULL DEFAULT '-infinity',
    valid_to    TIMESTAMP NOT NULL DEFAULT 'infinity',
    is_current  BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (customer_id, valid_from)
);
CREATE INDEX IF NOT EXISTS idx_dim_customer_bk ON dds.dim_customer (customer_id) WHERE is_current;

-- DimServiceCenter — сервисные центры (SCD Type 2)
CREATE TABLE IF NOT EXISTS dds.dim_service_center (
    center_sk   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    center_code VARCHAR(64) NOT NULL,
    name        VARCHAR(300),
    city        VARCHAR(100),
    address     VARCHAR(500),
    capacity    INTEGER,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from  TIMESTAMP NOT NULL DEFAULT '-infinity',
    valid_to    TIMESTAMP NOT NULL DEFAULT 'infinity',
    is_current  BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (center_code, valid_from)
);
CREATE INDEX IF NOT EXISTS idx_dim_service_center_bk ON dds.dim_service_center (center_code) WHERE is_current;

-- DimStore — склады хранения (SCD Type 2)
CREATE TABLE IF NOT EXISTS dds.dim_store (
    store_sk    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_code  VARCHAR(64) NOT NULL,
    name        VARCHAR(300),
    city        VARCHAR(100),
    address     VARCHAR(500),
    total_cells INTEGER,
    valid_from  TIMESTAMP NOT NULL DEFAULT '-infinity',
    valid_to    TIMESTAMP NOT NULL DEFAULT 'infinity',
    is_current  BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (store_code, valid_from)
);
CREATE INDEX IF NOT EXISTS idx_dim_store_bk ON dds.dim_store (store_code) WHERE is_current;

-- DimStatus — справочник статусов ремонта
CREATE TABLE IF NOT EXISTS dds.dim_status (
    status_sk   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status_code VARCHAR(50) NOT NULL UNIQUE,
    status_name VARCHAR(200) NOT NULL,
    stage_order SMALLINT NOT NULL,
    is_terminal BOOLEAN NOT NULL DEFAULT FALSE
);

-- FactRepair — события ремонта (партиционирование по месяцам)
CREATE TABLE IF NOT EXISTS dds.fact_repair (
    repair_sk               BIGINT GENERATED ALWAYS AS IDENTITY,
    repair_ticket_id        VARCHAR(64) NOT NULL,
    device_sk               BIGINT NOT NULL REFERENCES dds.dim_device(device_sk),
    customer_sk             BIGINT NOT NULL REFERENCES dds.dim_customer(customer_sk),
    center_sk               BIGINT NOT NULL REFERENCES dds.dim_service_center(center_sk),
    status_sk               BIGINT NOT NULL REFERENCES dds.dim_status(status_sk),
    date_sk                 DATE   NOT NULL REFERENCES dds.dim_date(date_sk),
    event_ts                TIMESTAMP NOT NULL,
    duration_since_prev_min INTEGER,
    load_ts                 TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (repair_sk, event_ts),
    UNIQUE (repair_ticket_id, status_sk, event_ts)
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS idx_fact_repair_date   ON dds.fact_repair (date_sk);
CREATE INDEX IF NOT EXISTS idx_fact_repair_center ON dds.fact_repair (center_sk, date_sk);
CREATE INDEX IF NOT EXISTS idx_fact_repair_ticket ON dds.fact_repair (repair_ticket_id);

-- FactStorage — события хранения/перемещения техники (партиционирование по месяцам)
CREATE TABLE IF NOT EXISTS dds.fact_storage (
    storage_sk         BIGINT GENERATED ALWAYS AS IDENTITY,
    device_sk          BIGINT NOT NULL REFERENCES dds.dim_device(device_sk),
    store_sk           BIGINT NOT NULL REFERENCES dds.dim_store(store_sk),
    date_sk            DATE   NOT NULL REFERENCES dds.dim_date(date_sk),
    device_id          VARCHAR(64) NOT NULL,
    cell_id            VARCHAR(50),
    storage_event_type VARCHAR(30) NOT NULL,
    event_ts           TIMESTAMP NOT NULL,
    qty                INTEGER NOT NULL DEFAULT 1,
    load_ts            TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (storage_sk, event_ts),
    UNIQUE (device_id, storage_event_type, event_ts, cell_id)
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS idx_fact_storage_date   ON dds.fact_storage (date_sk);
CREATE INDEX IF NOT EXISTS idx_fact_storage_store  ON dds.fact_storage (store_sk, date_sk);
CREATE INDEX IF NOT EXISTS idx_fact_storage_device ON dds.fact_storage (device_id, event_ts);

-- Карантин: факты с неразрешёнными ссылочными ключами или нарушенной логикой статусов.
-- UNIQUE по (ticket, event_ts, reason) — повторный прогон того же окна не плодит
-- дубликаты записей карантина (та же идемпотентность, что и у самих фактов).
CREATE TABLE IF NOT EXISTS dds.fact_repair_quarantine (
    repair_ticket_id VARCHAR(64),
    device_id        VARCHAR(64),
    customer_id      VARCHAR(64),
    center_code      VARCHAR(64),
    status_code      VARCHAR(50),
    event_ts         TIMESTAMP,
    reason           VARCHAR(100),
    load_ts          TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (repair_ticket_id, event_ts, reason)
);
