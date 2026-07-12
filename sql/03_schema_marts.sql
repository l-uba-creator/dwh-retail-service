-- Слой Marts: предрассчитанные агрегаты под операционные отчёты.
CREATE SCHEMA IF NOT EXISTS dm;

-- mart_repair_sla — SLA ремонта по центру, категории и дню
CREATE TABLE IF NOT EXISTS dm.mart_repair_sla (
    date_sk         DATE   NOT NULL,
    center_sk       BIGINT NOT NULL,
    category        VARCHAR(100) NOT NULL,
    tickets_total   INTEGER NOT NULL,
    tickets_overdue INTEGER NOT NULL,
    avg_repair_min  INTEGER NOT NULL,
    sla_share       NUMERIC(5,3) NOT NULL,
    load_ts         TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (date_sk, center_sk, category)
);

-- mart_center_load — загрузка сервисных центров
CREATE TABLE IF NOT EXISTS dm.mart_center_load (
    date_sk          DATE   NOT NULL,
    center_sk        BIGINT NOT NULL,
    active_repairs   INTEGER NOT NULL,
    incoming_tickets INTEGER NOT NULL,
    issued_tickets   INTEGER NOT NULL,
    utilization      NUMERIC(5,2) NOT NULL,
    load_ts          TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (date_sk, center_sk)
);

-- mart_storage_turnover — оборачиваемость склада
CREATE TABLE IF NOT EXISTS dm.mart_storage_turnover (
    date_sk          DATE   NOT NULL,
    store_sk         BIGINT NOT NULL,
    cell_id          VARCHAR(50) NOT NULL,
    devices_in       INTEGER NOT NULL,
    devices_out      INTEGER NOT NULL,
    avg_stay_days    NUMERIC(6,2) NOT NULL,
    cell_utilization NUMERIC(5,2) NOT NULL,
    load_ts          TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (date_sk, store_sk, cell_id)
);
