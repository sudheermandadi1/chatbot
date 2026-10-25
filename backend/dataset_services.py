import uuid
import re
from io import BytesIO

import pandas as pd
from sqlalchemy import inspect

from backend.database import engine
from backend.models import Dataset


TABLE_PATTERN = re.compile(r"dataset_[a-f0-9]{32}\Z")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_ROWS = 100_000
MAX_UPLOAD_COLUMNS = 100



def import_csv_file(file_bytes: bytes, filename: str, db) -> Dataset:
    if not filename.lower().endswith(".csv"):
        raise ValueError("Upload only a CSV file.")

    if not file_bytes or len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("CSV file must be non-empty and at most 20 MB.")

    try:
        df = pd.read_csv(BytesIO(file_bytes))
    except Exception as exc:
        raise ValueError("Could not parse the CSV file.") from exc

    if df.empty:
        raise ValueError("The CSV file has no data rows.")

    if len(df) > MAX_UPLOAD_ROWS:
        raise ValueError("CSV file exceeds the row limit.")

    if len(df.columns) > MAX_UPLOAD_COLUMNS:
        raise ValueError("CSV file exceeds the column limit.")

    normalized = [
        re.sub(
            r"[^a-zA-Z0-9_]+",
            "_",
            str(col).strip().lower(),
        ).strip("_")
        for col in df.columns
    ]

    if any(not name for name in normalized):
        raise ValueError("Column names cannot be empty.")

    if len(set(normalized)) != len(normalized):
        raise ValueError(
            "CSV column names must be unique after normalization."
        )

    reserved = {
        "select", "from", "where", "table", "group", "order",
        "user", "limit", "primary", "references", "values",
    }

    if any(name in reserved for name in normalized):
        raise ValueError(
            "Rename reserved SQL column names and upload again."
        )

    df.columns = normalized

    dataset_id = uuid.uuid4()
    table_name = f"dataset_{dataset_id.hex}"

    if not TABLE_PATTERN.fullmatch(table_name):
        raise ValueError("Generated dataset table name is invalid.")

    try:
        df.to_sql(
            name=table_name,
            con=engine,
            if_exists="fail",
            index=False,
            chunksize=1000,
            method="multi",
        )

        record = Dataset(
            id=dataset_id,
            table_name=table_name,
            original_filename=filename[:255],
            row_count=len(df),
        )

        db.add(record)
        db.commit()
        db.refresh(record)

        return record

    except Exception:
        db.rollback()

        with engine.begin() as connection:
            connection.exec_driver_sql(
                f'DROP TABLE IF EXISTS "{table_name}"'
            )

        raise

def get_dataset(dataset_id: str, db) -> Dataset:
    try:
        parsed_id = uuid.UUID(str(dataset_id))
    except (ValueError, AttributeError, TypeError) as e:
        raise ValueError("invalid dataset ID.") from e

    dataset = db.get(Dataset, parsed_id)

    if dataset is None:
        raise ValueError("Dataset not found.")

    if not TABLE_PATTERN.fullmatch(dataset.table_name):
        raise ValueError("Invalid dataset table metadata.")

    return dataset


def get_dataset_schema(dataset_id: str, db) -> dict:
    dataset = get_dataset(dataset_id, db)

    inspector = inspect(engine)

    if not inspector.has_table(dataset.table_name):
        raise ValueError("Dataset table does not exist.")

    columns = inspector.get_columns(dataset.table_name)

    return {
        "dataset_id": str(dataset.id),
        "table_name": dataset.table_name,
        "columns": [
            {
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
            }
            for col in columns
        ],
    }