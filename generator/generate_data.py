"""
Генератор синтетических данных для практического примера DWH.

Масштаб намеренно уменьшен относительно исходных оценок в отчёте (10 млн/4 млн
строк), чтобы прогон был честно воспроизводим на обычном ноутбуке за разумное
время. Порядок величин: ~300 тыс. заявок в ремонт (~1.6 млн событий) и
~150 тыс. потоков хранения (~450 тыс. событий) за 12 месяцев 2025 года.

В данные намеренно подмешан брак:
  - часть заявок ссылается на несуществующий device_id/customer_id/center_code
    (должны попасть в карантин по причине UNRESOLVED_REFERENCE);
  - часть заявок имеет нарушенный порядок статусов (должны попасть в карантин
    по причине STATUS_ORDER_ANOMALY).
Это позволяет реально проверить работу контроля качества, а не только
продекларировать его.
"""
import csv
import os
import random
import datetime
import argparse
import json

random.seed(42)

YEAR_START = datetime.date(2025, 1, 1)
YEAR_END = datetime.date(2025, 12, 31)
YEAR_DAYS = (YEAR_END - YEAR_START).days + 1

CATEGORIES = [
    "Смартфон", "Ноутбук", "Телевизор", "Стиральная машина",
    "Холодильник", "Планшет", "Пылесос", "Микроволновая печь",
]
BRANDS = ["Samsung", "LG", "Bosch", "Xiaomi", "Apple", "Haier", "Indesit", "Sony"]
CITIES = ["Москва", "Санкт-Петербург", "Обнинск", "Калуга", "Тула", "Казань", "Воронеж", "Самара"]
SEGMENTS = ["Розница", "VIP", "Корпоративный"]

STATUSES = [
    ("RECEIVED", "Принято в ремонт", 1, False),
    ("DIAGNOSTICS", "Диагностика", 2, False),
    ("AWAIT_PARTS", "Ожидание запчастей", 3, False),
    ("IN_REPAIR", "В ремонте", 4, False),
    ("READY", "Готово к выдаче", 5, False),
    ("ISSUED", "Выдано клиенту", 6, True),
    ("REJECTED", "Отказ", 6, True),
]

STORAGE_TYPES = ["INCOMING", "PLACED", "MOVED", "ISSUED", "WRITTEN_OFF"]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    p.add_argument("--n-devices", type=int, default=150_000)
    p.add_argument("--n-customers", type=int, default=100_000)
    p.add_argument("--n-tickets", type=int, default=300_000)
    p.add_argument("--n-storage-flows", type=int, default=150_000)
    p.add_argument("--bad-fk-rate", type=float, default=0.002)
    p.add_argument("--bad-order-rate", type=float, default=0.003)
    return p.parse_args()


def random_ts(start_date, max_offset_days):
    day_offset = random.randint(0, max_offset_days - 1)
    base = start_date + datetime.timedelta(days=day_offset)
    seconds = random.randint(0, 86399)
    return datetime.datetime.combine(base, datetime.time()) + datetime.timedelta(seconds=seconds)


def lognormal_minutes(mean_minutes, sigma=0.6):
    mu = (mean_minutes ** 0.5)  # placeholder not used directly
    val = random.lognormvariate(0, sigma) * mean_minutes
    return max(1, int(val))


def gen_devices(n):
    rows = []
    for i in range(1, n + 1):
        device_id = f"DEV{i:07d}"
        category = random.choice(CATEGORIES)
        brand = random.choice(BRANDS)
        model = f"{brand[:2].upper()}-{random.randint(100,999)}"
        serial = f"SN{random.randint(10**9, 10**10 - 1)}"
        warranty = random.random() < 0.35
        rows.append([device_id, category, brand, model, serial, warranty])
    return rows


def gen_customers(n):
    rows = []
    for i in range(1, n + 1):
        customer_id = f"CUST{i:07d}"
        full_name = f"Клиент {i}"
        phone = f"+7900{random.randint(1000000,9999999)}"
        email = f"client{i}@example.test"
        city = random.choice(CITIES)
        segment = random.choices(SEGMENTS, weights=[0.75, 0.15, 0.10])[0]
        rows.append([customer_id, full_name, phone, email, city, segment])
    return rows


def gen_centers(n=15):
    rows = []
    for i in range(1, n + 1):
        center_code = f"CTR{i:03d}"
        name = f"Сервисный центр №{i}"
        city = random.choice(CITIES)
        address = f"ул. Ремонтная, {i}"
        capacity = random.randint(20, 90)
        rows.append([center_code, name, city, address, capacity, True])
    return rows


def gen_stores(n=8):
    rows = []
    for i in range(1, n + 1):
        store_code = f"STORE{i:02d}"
        name = f"Склад №{i}"
        city = random.choice(CITIES)
        address = f"ул. Складская, {i}"
        total_cells = random.randint(300, 1500)
        rows.append([store_code, name, city, address, total_cells])
    return rows


def gen_repair_events(n_tickets, device_ids, customer_ids, center_codes, bad_fk_rate, bad_order_rate):
    """Возвращает (rows, stats) — построчно события ремонта + статистика брака."""
    rows = []
    bad_fk_count = 0
    bad_order_count = 0

    path_templates = [
        (["RECEIVED", "DIAGNOSTICS", "AWAIT_PARTS", "IN_REPAIR", "READY", "ISSUED"], 0.45),
        (["RECEIVED", "DIAGNOSTICS", "IN_REPAIR", "READY", "ISSUED"], 0.40),
        (["RECEIVED", "DIAGNOSTICS", "REJECTED"], 0.10),
        (["RECEIVED", "DIAGNOSTICS", "AWAIT_PARTS", "REJECTED"], 0.05),
    ]
    templates, weights = zip(*path_templates)

    gap_minutes = {
        "DIAGNOSTICS": 180, "AWAIT_PARTS": 2880, "IN_REPAIR": 720,
        "READY": 1440, "ISSUED": 300, "REJECTED": 200,
    }

    for t in range(1, n_tickets + 1):
        ticket_id = f"TCK{t:07d}"
        device_id = random.choice(device_ids)
        customer_id = random.choice(customer_ids)
        center_code = random.choice(center_codes)

        is_bad_fk = random.random() < bad_fk_rate
        if is_bad_fk:
            bad_fk_count += 1
            corrupt_field = random.choice(["device", "customer", "center"])
            if corrupt_field == "device":
                device_id = "DEV9999999"
            elif corrupt_field == "customer":
                customer_id = "CUST9999999"
            else:
                center_code = "CTR999"

        path = list(random.choices(templates, weights=weights)[0])

        start_ts = random_ts(YEAR_START, YEAR_DAYS - 10)
        ts = start_ts
        prev_ts = None
        ticket_events = []
        for idx, status_code in enumerate(path):
            if idx > 0:
                gap = lognormal_minutes(gap_minutes[status_code])
                ts = ts + datetime.timedelta(minutes=gap)
            duration_since_prev = None
            if prev_ts is not None:
                duration_since_prev = int((ts - prev_ts).total_seconds() // 60)
            ticket_events.append([ticket_id, device_id, customer_id, center_code, status_code, ts, duration_since_prev])
            prev_ts = ts

        is_bad_order = (not is_bad_fk) and len(ticket_events) >= 3 and random.random() < bad_order_rate
        if is_bad_order:
            bad_order_count += 1
            i, j = sorted(random.sample(range(len(ticket_events)), 2))
            ticket_events[i][5], ticket_events[j][5] = ticket_events[j][5], ticket_events[i][5]

        rows.extend(ticket_events)

    stats = {"bad_fk_count": bad_fk_count, "bad_order_count": bad_order_count, "total_events": len(rows)}
    return rows, stats


def gen_storage_events(n_flows, device_ids, store_codes):
    rows = []
    for f in range(1, n_flows + 1):
        device_id = random.choice(device_ids)
        store_code = random.choice(store_codes)
        cell_id = f"C{random.randint(1, 500):04d}"

        in_ts = random_ts(YEAR_START, YEAR_DAYS - 20)
        ts = in_ts
        rows.append([device_id, store_code, cell_id, "INCOMING", ts, 1])
        ts = ts + datetime.timedelta(minutes=lognormal_minutes(60))
        rows.append([device_id, store_code, cell_id, "PLACED", ts, 1])

        n_moves = random.choices([0, 1, 2], weights=[0.6, 0.3, 0.1])[0]
        for _ in range(n_moves):
            ts = ts + datetime.timedelta(minutes=lognormal_minutes(4320))
            rows.append([device_id, store_code, cell_id, "MOVED", ts, 1])

        ts = ts + datetime.timedelta(minutes=lognormal_minutes(7200))
        final_type = random.choices(["ISSUED", "WRITTEN_OFF"], weights=[0.9, 0.1])[0]
        rows.append([device_id, store_code, cell_id, final_type, ts, 1])
    return rows


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    args = parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    print("Generating dimensions...")
    devices = gen_devices(args.n_devices)
    customers = gen_customers(args.n_customers)
    centers = gen_centers()
    stores = gen_stores()

    write_csv(os.path.join(out_dir, "stg_devices.csv"),
              ["device_id", "category", "brand", "model", "serial_number", "warranty_flag"], devices)
    write_csv(os.path.join(out_dir, "stg_customers.csv"),
              ["customer_id", "full_name", "phone", "email", "city", "segment"], customers)
    write_csv(os.path.join(out_dir, "stg_service_centers.csv"),
              ["center_code", "name", "city", "address", "capacity", "is_active"], centers)
    write_csv(os.path.join(out_dir, "stg_stores.csv"),
              ["store_code", "name", "city", "address", "total_cells"], stores)
    write_csv(os.path.join(out_dir, "stg_statuses.csv"),
              ["status_code", "status_name", "stage_order", "is_terminal"], STATUSES)

    device_ids = [r[0] for r in devices]
    customer_ids = [r[0] for r in customers]
    center_codes = [r[0] for r in centers]
    store_codes = [r[0] for r in stores]

    print(f"Generating {args.n_tickets} repair tickets...")
    repair_rows, repair_stats = gen_repair_events(
        args.n_tickets, device_ids, customer_ids, center_codes,
        args.bad_fk_rate, args.bad_order_rate,
    )
    write_csv(os.path.join(out_dir, "stg_repair_events.csv"),
              ["repair_ticket_id", "device_id", "customer_id", "center_code", "status_code", "event_ts", "duration_since_prev_min"],
              repair_rows)

    print(f"Generating {args.n_storage_flows} storage flows...")
    storage_rows = gen_storage_events(args.n_storage_flows, device_ids, store_codes)
    write_csv(os.path.join(out_dir, "stg_storage_events.csv"),
              ["device_id", "store_code", "cell_id", "storage_event_type", "event_ts", "qty"],
              storage_rows)

    summary = {
        "n_devices": args.n_devices,
        "n_customers": args.n_customers,
        "n_centers": len(centers),
        "n_stores": len(stores),
        "n_tickets": args.n_tickets,
        "n_repair_events": repair_stats["total_events"],
        "n_storage_flows": args.n_storage_flows,
        "n_storage_events": len(storage_rows),
        "injected_bad_fk_tickets": repair_stats["bad_fk_count"],
        "injected_bad_order_tickets": repair_stats["bad_order_count"],
    }
    summary_path = os.path.join(out_dir, "..", "results", "generation_summary.json")
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Done.")


if __name__ == "__main__":
    main()
