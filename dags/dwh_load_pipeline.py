"""DAG загрузки DWH: задачи генерируются из конфигурации-словаря.
 Здесь задачи явно сохраняются в списки и группируются через TaskGroup, 
 а группы связаны цепочкой зависимостей.
"""
from datetime import datetime, timedelta

from airflow.decorators import dag
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.utils.task_group import TaskGroup

FACT_CONFIGS = [
    {
        "name": "fact_repair",
        "load_sql": "sql/05_load_fact_repair.sql",
        "partition_table": "dds.fact_repair",
    },
    {
        "name": "fact_storage",
        "load_sql": "sql/05_load_fact_storage.sql",
        "partition_table": "dds.fact_storage",
    },
]

DEFAULT_ARGS = {
    "owner": "data_team",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "depends_on_past": False,
}


@dag(
    dag_id="dwh_load_pipeline",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2025, 1, 1),
    schedule="0 2 * * *",
    catchup=False,
    tags=["dwh", "dds", "marts"],
)
def dwh_load_pipeline():

    with TaskGroup(group_id="ensure_partitions") as ensure_partitions_group:
        partition_tasks = [
            PostgresOperator(
                task_id=f"ensure_partition_{cfg['name']}",
                postgres_conn_id="postgres_default",
                sql="SELECT dds.create_missing_partitions(%(table)s, %(run_date)s::date, 30);",
                parameters={"table": cfg["partition_table"], "run_date": "{{ ds }}"},
            )
            for cfg in FACT_CONFIGS
        ]

    with TaskGroup(group_id="load_facts") as load_facts_group:
        load_tasks = [
            PostgresOperator(
                task_id=f"load_{cfg['name']}",
                postgres_conn_id="postgres_default",
                sql=cfg["load_sql"],
                parameters={
                    "load_from": "{{ data_interval_start }}",
                    "load_to": "{{ data_interval_end }}",
                },
            )
            for cfg in FACT_CONFIGS
        ]

    dq_status_order_check = PostgresOperator(
        task_id="dq_status_order_check",
        postgres_conn_id="postgres_default",
        sql="sql/07_dq_status_order_check.sql",
    )

    build_marts = PostgresOperator(
        task_id="build_marts",
        postgres_conn_id="postgres_default",
        sql="sql/06_build_marts.sql",
    )

    reconciliation = PostgresOperator(
        task_id="reconciliation",
        postgres_conn_id="postgres_default",
        sql="sql/08_reconciliation_check.sql",
    )

    # Явная цепочка зависимостей, отсутствовавшая в исходном варианте:
    # партиции -> загрузка фактов -> контроль качества -> витрины -> реконсиляция.
    ensure_partitions_group >> load_facts_group >> dq_status_order_check >> build_marts >> reconciliation


dwh_load_pipeline()
