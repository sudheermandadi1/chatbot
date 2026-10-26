
import logging
import re
import uuid
from typing import Any

from sqlalchemy import (
    MetaData,
    Table,
    and_,
    delete,
    select,
    text,
    update,
)

from backend.database import engine
from backend.dataset_services import get_dataset
from backend.sql_validator import validate_dataset_select


logger = logging.getLogger(__name__)

MAX_QUERY_ROWS = 100
MAX_MUTATION_ROWS = 100

TABLE_PATTERN = re.compile(r"dataset_[a-f0-9]{32}\Z")


_pending_mutations: dict[str, dict[str, Any]] = {}


def _get_dataset_and_table(dataset_id: str, db):
    dataset = get_dataset(dataset_id, db)
    table_name = dataset.table_name

    if not TABLE_PATTERN.fullmatch(table_name):
        raise ValueError("Invalid dataset table name.")

    return dataset, table_name


def get_dataset_table(connection, table_name: str) -> Table:
    if not TABLE_PATTERN.fullmatch(table_name):
        raise ValueError("Invalid dataset table.")

    return Table(
        table_name,
        MetaData(),
        autoload_with=connection,
    )


def build_conditions(table: Table, conditions: list[dict]):
    if not isinstance(conditions, list) or not conditions:
        raise ValueError(
            "At least one filter condition is required."
        )

    expressions = []

    for item in conditions:
        if not isinstance(item, dict):
            raise ValueError("Each condition must be an object.")

        name = item.get("column")
        operator = item.get("operator")
        value = item.get("value")

        if not isinstance(name, str) or name not in table.c:
            raise ValueError(f"Unknown column: {name}")

        col = table.c[name]

        if operator == "=":
            expr = col.is_(None) if value is None else col == value

        elif operator == "!=":
            expr = col.is_not(None) if value is None else col != value

        elif operator == ">":
            expr = col > value

        elif operator == ">=":
            expr = col >= value

        elif operator == "<":
            expr = col < value

        elif operator == "<=":
            expr = col <= value

        elif operator == "in":
            if not isinstance(value, list) or not value:
                raise ValueError("'in' requires a non-empty list.")
            expr = col.in_(value)

        elif operator == "not_in":
            if not isinstance(value, list) or not value:
                raise ValueError("'not_in' requires a non-empty list.")
            expr = col.not_in(value)

        elif operator == "is_null":
            expr = col.is_(None)

        elif operator == "is_not_null":
            expr = col.is_not(None)

        else:
            raise ValueError(f"Unsupported operator: {operator}")

        expressions.append(expr)

    return and_(*expressions)


def query_dataset(
    dataset_id: str,
    sql: str,
    db,
) -> dict[str, Any]:
    _, table_name = _get_dataset_and_table(dataset_id, db)

    logger.info("Query requested for dataset table: %s", table_name)

    safe_sql = validate_dataset_select(sql, table_name)

    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(
                text("SET LOCAL statement_timeout = '10s'")
            )

            result = connection.execute(text(safe_sql))
            rows = result.fetchmany(MAX_QUERY_ROWS + 1)

            if len(rows) > MAX_QUERY_ROWS:
                raise ValueError(
                    f"Query returned more than {MAX_QUERY_ROWS} rows. "
                    "Add a LIMIT or a narrower filter."
                )

            data = [
                dict(row._mapping)
                for row in rows
            ]

    return {
        "success": True,
        "sql": safe_sql,
        "row_count": len(data),
        "results": data,
    }


def preview_dataset_mutation(
    dataset_id: str,
    operation: str,
    conditions: list[dict],
    db,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operation = operation.lower()

    if operation not in {"update", "delete"}:
        raise ValueError("Only UPDATE and DELETE are supported.")

    if operation == "update" and not values:
        raise ValueError("Update values cannot be empty.")

    if values is not None and not isinstance(values, dict):
        raise ValueError("Update values must be an object.")

    dataset, table_name = _get_dataset_and_table(dataset_id, db)

    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))

            table = get_dataset_table(connection, table_name)
            where_clause = build_conditions(table, conditions)

            if values and any(name not in table.c for name in values):
                raise ValueError("Unknown column in update values.")

            count = connection.execute(
                select(text("count(*)"))
                .select_from(table)
                .where(where_clause)
            ).scalar_one()

            sample_result = connection.execute(
                select(*list(table.c))
                .where(where_clause)
                .limit(10)
            )

            samples = [
                dict(row._mapping)
                for row in sample_result.fetchall()
            ]

    if count == 0:
        return {
            "success": True,
            "operation": operation,
            "matching_rows": 0,
            "sample_rows": [],
            "requires_confirmation": False,
            "message": "No records match these conditions.",
        }

    if count > MAX_MUTATION_ROWS:
        raise ValueError(
            f"Operation matches {count} rows; maximum is "
            f"{MAX_MUTATION_ROWS}. Narrow the conditions."
        )

    pending_id = str(uuid.uuid4())

    _pending_mutations[pending_id] = {
        "dataset_id": str(dataset.id),
        "table_name": table_name,
        "operation": operation,
        "conditions": conditions,
        "values": values or {},
        "expected_rows": count,
    }

    return {
        "success": True,
        "pending_id": pending_id,
        "operation": operation,
        "matching_rows": count,
        "sample_rows": samples,
        "requires_confirmation": True,
        "message": (
            "Preview completed. No changes were made. "
            "Explicit user confirmation is required."
        ),
    }


def confirm_dataset_mutation(
    pending_id: str,
    dataset_id: str,
    db,
) -> dict[str, Any]:
    pending = _pending_mutations.get(pending_id)

    if pending is None:
        raise ValueError("Pending operation not found or expired.")

    if pending["dataset_id"] != str(dataset_id):
        raise ValueError("This operation belongs to another dataset.")

    dataset, table_name = _get_dataset_and_table(dataset_id, db)

    if table_name != pending["table_name"]:
        raise ValueError("Dataset metadata has changed.")

    expected_rows = pending["expected_rows"]

    with engine.begin() as connection:
        table = get_dataset_table(connection, table_name)
        where_clause = build_conditions(
            table,
            pending["conditions"],
        )

        current_count = connection.execute(
            select(text("count(*)"))
            .select_from(table)
            .where(where_clause)
        ).scalar_one()

        if current_count != expected_rows:
            raise ValueError(
                "Matching rows changed since preview. Preview again."
            )

        if current_count > MAX_MUTATION_ROWS:
            raise ValueError("Mutation row limit exceeded.")

        if pending["operation"] == "update":
            values = pending["values"]

            if not values or any(name not in table.c for name in values):
                raise ValueError("Invalid update values.")

            result = connection.execute(
                update(table)
                .where(where_clause)
                .values(**values)
            )

        elif pending["operation"] == "delete":
            result = connection.execute(
                delete(table).where(where_clause)
            )

        else:
            raise ValueError("Invalid pending operation.")

        if result.rowcount != expected_rows:
            raise ValueError(
                "Affected row count changed; transaction rolled back."
            )

        rows_affected = result.rowcount

    _pending_mutations.pop(pending_id, None)

    return {
        "success": True,
        "operation": pending["operation"],
        "rows_affected": rows_affected,
        "message": "Dataset mutation completed successfully.",
    }


def cancel_dataset_mutation(
    pending_id: str,
    dataset_id: str,
) -> dict[str, Any]:
    pending = _pending_mutations.get(pending_id)

    if pending is None:
        raise ValueError("Pending operation not found or expired.")

    if pending["dataset_id"] != str(dataset_id):
        raise ValueError("This operation belongs to another dataset.")

    _pending_mutations.pop(pending_id, None)

    return {
        "success": True,
        "cancelled": True,
        "message": "Pending mutation cancelled. No changes were made.",
    }
