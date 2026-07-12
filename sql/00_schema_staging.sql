-- Слой Staging: точные копии источников без преобразований.
CREATE SCHEMA IF NOT EXISTS stg;

CREATE TABLE IF NOT EXISTS stg.stg_repair_events (
    repair_ticket_id  VARCHAR(64) NOT NULL,
    device_id         VARCHAR(64) NOT NULL,
    customer_id       VARCHAR(64) NOT NULL,
    center_code       VARCHAR(64) NOT NULL,
    status_code       VARCHAR(50) NOT NULL,
    event_ts          TIMESTAMP NOT NULL,
    duration_since_prev_min INTEGER
);

CREATE TABLE IF NOT EXISTS stg.stg_storage_events (
    device_id          VARCHAR(64) NOT NULL,
    store_code         VARCHAR(64) NOT NULL,
    cell_id            VARCHAR(50),
    storage_event_type VARCHAR(30) NOT NULL,
    event_ts           TIMESTAMP NOT NULL,
    qty                INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stg.stg_devices (
    device_id     VARCHAR(64) NOT NULL,
    category      VARCHAR(100),
    brand         VARCHAR(100),
    model         VARCHAR(200),
    serial_number VARCHAR(200),
    warranty_flag BOOLEAN
);

CREATE TABLE IF NOT EXISTS stg.stg_customers (
    customer_id VARCHAR(64) NOT NULL,
    full_name   VARCHAR(300),
    phone       VARCHAR(30),
    email       VARCHAR(200),
    city        VARCHAR(100),
    segment     VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS stg.stg_service_centers (
    center_code VARCHAR(64) NOT NULL,
    name        VARCHAR(300),
    city        VARCHAR(100),
    address     VARCHAR(500),
    capacity    INTEGER,
    is_active   BOOLEAN
);

CREATE TABLE IF NOT EXISTS stg.stg_stores (
    store_code  VARCHAR(64) NOT NULL,
    name        VARCHAR(300),
    city        VARCHAR(100),
    address     VARCHAR(500),
    total_cells INTEGER
);

CREATE TABLE IF NOT EXISTS stg.stg_statuses (
    status_code VARCHAR(50) NOT NULL,
    status_name VARCHAR(200) NOT NULL,
    stage_order SMALLINT NOT NULL,
    is_terminal BOOLEAN NOT NULL DEFAULT FALSE
);
