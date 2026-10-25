import os

import httpx


API_URL = os.getenv(
    "API_URL",
    "http://127.0.0.1:8000",
).rstrip("/")


def send_message(
    message: str,
    conversation_id: str | None = None,
    active_dataset_id: str | None = None,
    active_dataset_table: str | None = None,
) -> dict:
    payload = {
        "message": message,
        "conversation_id": str(conversation_id) if conversation_id else None,
        "dataset_id": str(active_dataset_id) if active_dataset_id else None,
        "activate_dataset_table": active_dataset_table,
    }

    response = httpx.post(
        f"{API_URL}/chat",
        json=payload,
        timeout=90.0,
    )
    response.raise_for_status()
    return response.json()


def check_backend_health() -> dict:
    with httpx.Client(timeout=5.0) as client:
        response = client.get(f"{API_URL}/health")
        response.raise_for_status()
        return response.json()


def confirm_mutation(
    pending_id: str,
    dataset_id: str,
) -> dict:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{API_URL}/datasets/mutations/confirmation",
            json={
                "pending_id": pending_id,
                "dataset_id": dataset_id,
            },
        )
        response.raise_for_status()
        return response.json()


def cancel_mutation(
    pending_id: str,
    dataset_id: str,
) -> dict:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{API_URL}/datasets/mutations/cancel",
            json={
                "pending_id": pending_id,
                "dataset_id": dataset_id,
            },
        )
        response.raise_for_status()
        return response.json()