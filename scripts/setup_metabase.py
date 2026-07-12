"""
Headless-настройка Metabase через REST API: первичная настройка инстанса,
подключение БД PostgreSQL с витринами, создание карточек и дашборда
"Операционная аналитика розничного сервиса", включение Public Sharing.

Требует переменные окружения (или значения по умолчанию ниже):
  MB_URL (http://localhost:3000), MB_ADMIN_EMAIL, MB_ADMIN_PASSWORD,
  PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD (для подключаемой БД витрин).
"""
import os
import sys
import time
import json
import requests

MB_URL = os.environ.get("MB_URL", "http://localhost:3000")
MB_ADMIN_EMAIL = os.environ.get("MB_ADMIN_EMAIL", "admin@dwh-practice.local")
MB_ADMIN_PASSWORD = os.environ.get("MB_ADMIN_PASSWORD", "DwhPractice2026!")

PGHOST = os.environ.get("PGHOST", "localhost")
PGPORT = os.environ.get("PGPORT", "5432")
PGDATABASE = os.environ.get("PGDATABASE", "dwh_practice")
PGUSER = os.environ.get("PGUSER", "dwh_practice")
PGPASSWORD = os.environ.get("PGPASSWORD")

RESULTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results"))


def wait_for_metabase(timeout=180):
    print("Waiting for Metabase to become ready...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{MB_URL}/api/health", timeout=5)
            if r.status_code == 200:
                print("Metabase is up.")
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(3)
    raise RuntimeError("Metabase did not become ready in time")


def get_setup_token():
    r = requests.get(f"{MB_URL}/api/session/properties", timeout=10)
    r.raise_for_status()
    props = r.json()
    return props.get("setup-token")


def is_already_setup():
    return get_setup_token() is None


def run_setup():
    # setup-token may remain non-null even after setup completed; the
    # authoritative check is whether the admin account can already log in.
    try:
        return login()
    except requests.exceptions.HTTPError:
        pass

    token = get_setup_token()
    payload = {
        "token": token,
        "user": {
            "first_name": "DWH",
            "last_name": "Practice",
            "email": MB_ADMIN_EMAIL,
            "password": MB_ADMIN_PASSWORD,
            "site_name": "DWH Retail Service Practice",
        },
        "prefs": {
            "site_name": "DWH Retail Service Practice",
            "allow_tracking": False,
        },
    }
    r = requests.post(f"{MB_URL}/api/setup", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    print("Setup complete.")
    return data.get("id") or login()


def login():
    r = requests.post(f"{MB_URL}/api/session", json={"username": MB_ADMIN_EMAIL, "password": MB_ADMIN_PASSWORD}, timeout=15)
    r.raise_for_status()
    return r.json()["id"]


def headers(session_id):
    return {"X-Metabase-Session": session_id, "Content-Type": "application/json"}


def add_database(session_id):
    r = requests.get(f"{MB_URL}/api/database", headers=headers(session_id), timeout=15)
    r.raise_for_status()
    existing = r.json().get("data", r.json()) if isinstance(r.json(), dict) else r.json()
    for db in existing:
        if db.get("name") == "DWH Retail Service":
            print(f"Database already exists (id={db['id']}).")
            return db["id"]

    payload = {
        "engine": "postgres",
        "name": "DWH Retail Service",
        "details": {
            "host": PGHOST,
            "port": int(PGPORT),
            "dbname": PGDATABASE,
            "user": PGUSER,
            "password": PGPASSWORD,
            "ssl": False,
            "tunnel-enabled": False,
        },
        "is_full_sync": True,
    }
    r = requests.post(f"{MB_URL}/api/database", json=payload, headers=headers(session_id), timeout=30)
    r.raise_for_status()
    db_id = r.json()["id"]
    print(f"Database added (id={db_id}). Waiting for schema sync...")
    time.sleep(15)
    requests.post(f"{MB_URL}/api/database/{db_id}/sync_schema", headers=headers(session_id), timeout=30)
    time.sleep(10)
    return db_id


def create_native_card(session_id, database_id, name, sql, display, viz_settings=None):
    """viz_settings must set graph.dimensions/graph.metrics for bar/line charts —
    otherwise Metabase renders an "which fields do you want to use" placeholder
    instead of the chart."""
    payload = {
        "name": name,
        "dataset_query": {
            "type": "native",
            "native": {"query": sql},
            "database": database_id,
        },
        "display": display,
        "visualization_settings": viz_settings or {},
        "collection_id": None,
    }
    r = requests.post(f"{MB_URL}/api/card", json=payload, headers=headers(session_id), timeout=30)
    r.raise_for_status()
    card = r.json()
    print(f"Card created: {name} (id={card['id']})")
    return card["id"]


def create_dashboard(session_id, name, description=""):
    payload = {"name": name, "description": description}
    r = requests.post(f"{MB_URL}/api/dashboard", json=payload, headers=headers(session_id), timeout=30)
    r.raise_for_status()
    dash = r.json()
    print(f"Dashboard created: {name} (id={dash['id']})")
    return dash["id"]


def add_card_to_dashboard(session_id, dashboard_id, card_id, col, row, size_x=6, size_y=4):
    r = requests.get(f"{MB_URL}/api/dashboard/{dashboard_id}", headers=headers(session_id), timeout=15)
    r.raise_for_status()
    dashboard = r.json()
    dashcards = dashboard.get("dashcards", [])
    dashcards.append({
        "id": -(len(dashcards) + 1),
        "card_id": card_id,
        "col": col, "row": row, "size_x": size_x, "size_y": size_y,
        "parameter_mappings": [],
    })
    payload = {"dashcards": dashcards}
    r = requests.put(f"{MB_URL}/api/dashboard/{dashboard_id}", json=payload, headers=headers(session_id), timeout=30)
    r.raise_for_status()
    print(f"Card {card_id} added to dashboard {dashboard_id}")


def enable_public_sharing(session_id):
    r = requests.put(f"{MB_URL}/api/setting/enable-public-sharing", json={"value": True}, headers=headers(session_id), timeout=15)
    r.raise_for_status()
    print("Public sharing enabled.")


def get_public_link(session_id, dashboard_id):
    r = requests.post(f"{MB_URL}/api/dashboard/{dashboard_id}/public_link", headers=headers(session_id), timeout=15)
    r.raise_for_status()
    uuid = r.json()["uuid"]
    return f"{MB_URL}/public/dashboard/{uuid}"


CARD_SLA = (
    "Доля просроченных ремонтов по центрам",
    """
    SELECT sc.name AS center_name, sc.city,
           SUM(m.tickets_total) AS tickets_total,
           SUM(m.tickets_overdue) AS tickets_overdue,
           ROUND(AVG(m.sla_share), 3) AS sla_share
    FROM dm.mart_repair_sla m
    JOIN dds.dim_service_center sc ON sc.center_sk = m.center_sk AND sc.is_current
    GROUP BY sc.name, sc.city
    ORDER BY sla_share ASC;
    """,
    "bar",
    {"graph.dimensions": ["center_name"], "graph.metrics": ["sla_share"]},
)

CARD_CENTER_LOAD = (
    "Загрузка сервисных центров по дням",
    """
    SELECT m.date_sk, sc.name AS center_name, m.utilization
    FROM dm.mart_center_load m
    JOIN dds.dim_service_center sc ON sc.center_sk = m.center_sk AND sc.is_current
    ORDER BY m.date_sk;
    """,
    "line",
    None,
)

# avg_stay_days (days) and cell_utilization (~0.0001 range) share nothing on the
# same scale — combining them in one bar chart makes one series invisible.
# Keep this card to avg_stay_days only.
CARD_STORAGE = (
    "Оборачиваемость ячеек хранения по складам",
    """
    SELECT st.name AS store_name, AVG(m.avg_stay_days) AS avg_stay_days
    FROM dm.mart_storage_turnover m
    JOIN dds.dim_store st ON st.store_sk = m.store_sk AND st.is_current
    GROUP BY st.name
    ORDER BY avg_stay_days DESC;
    """,
    "bar",
    {"graph.dimensions": ["store_name"], "graph.metrics": ["avg_stay_days"]},
)

# Metabase's "scalar" display only surfaces the first result column, so the two
# KPIs need to be separate cards rather than one two-column query.
CARD_KPI_TICKETS = (
    "KPI: всего заявок в ремонт",
    "SELECT SUM(tickets_total) AS tickets_total FROM dm.mart_repair_sla;",
    "scalar",
    None,
)

CARD_KPI_SLA = (
    "KPI: доля выполненных в SLA",
    """
    SELECT ROUND(1 - SUM(tickets_overdue)::numeric / NULLIF(SUM(tickets_total), 0), 3) AS sla_share_overall
    FROM dm.mart_repair_sla;
    """,
    "scalar",
    None,
)


def main():
    wait_for_metabase()
    session_id = run_setup()
    db_id = add_database(session_id)

    cards = (CARD_SLA, CARD_CENTER_LOAD, CARD_STORAGE, CARD_KPI_TICKETS, CARD_KPI_SLA)
    card_ids = [
        create_native_card(session_id, db_id, name, sql, display, viz_settings)
        for name, sql, display, viz_settings in cards
    ]

    dashboard_id = create_dashboard(
        session_id,
        "Операционная аналитика розничного сервиса",
        "SLA ремонта, загрузка сервисных центров, оборачиваемость складских ячеек",
    )

    positions = [(0, 0, 6, 4), (6, 0, 6, 4), (0, 4, 6, 4), (6, 4, 3, 2), (9, 4, 3, 2)]
    for card_id, (col, row, sx, sy) in zip(card_ids, positions):
        add_card_to_dashboard(session_id, dashboard_id, card_id, col, row, sx, sy)

    enable_public_sharing(session_id)
    public_url = get_public_link(session_id, dashboard_id)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    info = {
        "admin_url": f"{MB_URL}/dashboard/{dashboard_id}",
        "public_url": public_url,
        "dashboard_id": dashboard_id,
        "database_id": db_id,
    }
    with open(os.path.join(RESULTS_DIR, "metabase_dashboard_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
