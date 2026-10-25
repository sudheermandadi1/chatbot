
import logging

import httpx
import streamlit as st

from api_client import (
    API_URL,
    send_message,
    cancel_mutation,
    confirm_mutation,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024


st.set_page_config(
    page_title="AI Assistant",
    page_icon="🤖",
    layout="centered",
)

st.title("AI Assistant")
st.caption("Core agent with REST, MCP, and CSV dataset tools")


DEFAULT_STATE = {
    "conversation_id": None,
    "messages": [],
    "activate_dataset_table": None,
    "dataset_id": None,
    "dataset_filename": None,
    "pending_mutation": None,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


with st.sidebar:
    st.header("Chat settings")

    if st.button("+ New chat", use_container_width=True):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.session_state.pending_mutation = None
        st.rerun()

    st.divider()
    st.subheader("📁 CSV dataset")

    st.caption(
        "Upload a CSV file to PostgreSQL and use it in your conversations."
    )

    uploaded_file = st.file_uploader(
        "Choose a CSV file",
        type=["csv"],
        accept_multiple_files=False,
        help="Maximum file size: 20 MB.",
    )

    if st.button("Upload CSV file", use_container_width=True):
        if uploaded_file is None:
            st.warning("Please select a CSV file first.")
        else:
            try:
                file_bytes = uploaded_file.getvalue()

                if not file_bytes:
                    st.error("The selected CSV file is empty.")

                elif len(file_bytes) > MAX_FILE_SIZE:
                    st.error("The CSV file must be 20 MB or smaller.")

                else:
                    with st.spinner("Uploading CSV to the backend..."):
                        response = httpx.post(
                            f"{API_URL}/datasets/upload",
                            files={
                                "file": (
                                    uploaded_file.name,
                                    file_bytes,
                                    "text/csv",
                                )
                            },
                            timeout=120.0,
                        )
                        response.raise_for_status()
                        data = response.json()

                    if not isinstance(data, dict):
                        raise ValueError(
                            "The backend returned an invalid upload response."
                        )

                    dataset_id = data.get("dataset_id") or data.get("id")
                    table_name = data.get("table_name")

                    if not dataset_id:
                        raise ValueError(
                            "Upload response is missing dataset_id."
                        )

                    if not table_name:
                        raise ValueError(
                            "Upload response is missing table_name."
                        )

                    st.session_state.dataset_id = str(dataset_id)
                    st.session_state.activate_dataset_table = str(table_name)
                    st.session_state.dataset_filename = (
                        data.get("filename") or uploaded_file.name
                    )
                    st.session_state.pending_mutation = None

                    st.success("CSV uploaded successfully!")

                    st.write(
                        f"**File:** {st.session_state.dataset_filename}"
                    )

                    if data.get("row_count") is not None:
                        st.write(f"**Rows:** {data['row_count']:,}")

                    st.caption("Dataset ID")
                    st.code(st.session_state.dataset_id)

                    st.caption("SQL table name")
                    st.code(st.session_state.activate_dataset_table)

                    st.info(
                        "This dataset will be included in subsequent chat requests."
                    )

            except httpx.HTTPStatusError as exc:
                logger.exception("CSV upload failed")

                try:
                    detail = exc.response.json().get(
                        "detail",
                        "The backend rejected the upload.",
                    )
                except (ValueError, AttributeError):
                    detail = "The backend rejected the upload."

                st.error(
                    f"Upload failed ({exc.response.status_code}): {detail}"
                )

            except httpx.RequestError:
                logger.exception("Could not connect to the backend")
                st.error(
                    "Could not connect to FastAPI. Check that the backend is running."
                )

            except ValueError as exc:
                logger.exception("Invalid upload response")
                st.error(str(exc))

            except Exception:
                logger.exception("Unexpected CSV upload error")
                st.error("An unexpected error occurred during CSV upload.")

    st.divider()
    st.subheader("Active CSV dataset")

    if st.session_state.dataset_id:
        st.success("Dataset selected")

        st.write(f"**File:** {st.session_state.dataset_filename}")

        st.caption("Dataset ID")
        st.code(st.session_state.dataset_id)

        st.caption("SQL table name")
        st.code(st.session_state.activate_dataset_table)

        if st.button("Deactivate dataset", use_container_width=True):
            st.session_state.dataset_id = None
            st.session_state.activate_dataset_table = None
            st.session_state.dataset_filename = None
            st.session_state.pending_mutation = None
            st.rerun()
    else:
        st.info("No dataset selected.")
        st.caption("Upload a CSV file to enable dataset tools.")


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


prompt = st.chat_input("Ask me anything...")

if prompt:
    st.session_state.messages.append(
        {
            "role": "user",
            "content": prompt,
        }
    )

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                result = send_message(
                    message=prompt,
                    conversation_id=st.session_state.conversation_id,
                    active_dataset_id=st.session_state.dataset_id,
                    active_dataset_table=(
                        st.session_state.activate_dataset_table
                    ),
                )

                if not isinstance(result, dict):
                    raise ValueError("Invalid backend response.")

                reply = result.get("reply")
                conversation_id = result.get("conversation_id")

                if not isinstance(reply, str) or not reply.strip():
                    raise ValueError(
                        "The backend did not return a valid reply."
                    )

                if not conversation_id:
                    raise ValueError(
                        "The backend did not return a conversation ID."
                    )

                st.session_state.conversation_id = str(conversation_id)

                if "pending_mutations" in result:
                    st.session_state.pending_mutation = (
                        result["pending_mutations"]
                    )
                elif "pending_mutation" in result:
                    st.session_state.pending_mutation = (
                        result["pending_mutation"]
                    )
                else:
                    st.session_state.pending_mutation = None

                st.markdown(reply)

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": reply,
                    }
                )

            except httpx.HTTPStatusError as exc:
                logger.exception(
                    "Backend returned HTTP %s",
                    exc.response.status_code,
                )

                try:
                    detail = exc.response.json().get(
                        "detail",
                        "The backend returned an error.",
                    )
                except (ValueError, AttributeError):
                    detail = "The backend returned an error."

                st.error(
                    f"Backend error ({exc.response.status_code}): {detail}"
                )

            except httpx.RequestError:
                logger.exception("Could not connect to FastAPI")
                st.error(
                    "Could not connect to the backend. Check that FastAPI is running."
                )

            except (ValueError, KeyError):
                logger.exception("Invalid backend response")
                st.error(
                    "The backend returned an invalid response. "
                    "Check the FastAPI logs and response schema."
                )

            except Exception:
                logger.exception("Unexpected frontend chat error")
                st.error(
                    "An unexpected error occurred. Check the application logs."
                )


pending = st.session_state.pending_mutation
dataset_id = st.session_state.dataset_id

if pending and dataset_id:
    st.divider()
    st.subheader("Review database change")

    pending_id = pending.get("pending_id")

    if not pending_id:
        st.error("The pending mutation is missing its ID.")

    else:
        operation = str(pending.get("operation", "change")).upper()
        matching_rows = pending.get("matching_rows", 0)
        sample_rows = pending.get("sample_rows", [])

        st.warning(
            f"This operation will {operation} data in "
            f"{matching_rows} matching row(s)."
        )

        if sample_rows:
            st.write("Sample of affected rows:")
            st.dataframe(sample_rows, use_container_width=True)

        st.caption(
            "No changes have been made yet. Review the preview before confirming."
        )

        confirm_col, cancel_col = st.columns(2)

        with confirm_col:
            confirm_click = st.button(
                "Confirm Change",
                type="primary",
                use_container_width=True,
                key=f"confirm_{pending_id}",
            )

        with cancel_col:
            cancel_click = st.button(
                "Cancel Change",
                use_container_width=True,
                key=f"cancel_{pending_id}",
            )

        if confirm_click:
            try:
                with st.spinner("Applying database change..."):
                    result = confirm_mutation(
                        pending_id=str(pending_id),
                        dataset_id=dataset_id,
                    )

                if result.get("success"):
                    st.session_state.pending_mutation = None

                    rows_affected = result.get("rows_affected", 0)
                    st.success(
                        f"Changes applied successfully. Rows affected: {rows_affected}"
                    )
                    st.rerun()
                else:
                    st.error(
                        result.get(
                            "error",
                            "The backend did not confirm the change.",
                        )
                    )

            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get(
                        "detail",
                        "The backend rejected the confirmation.",
                    )
                except (ValueError, AttributeError):
                    detail = "The backend rejected the confirmation."

                st.error(f"Confirmation failed: {detail}")

            except httpx.RequestError:
                logger.exception("Mutation confirmation request failed")
                st.error(
                    "Could not connect to FastAPI. Check that the backend is running."
                )

            except Exception:
                logger.exception("Unexpected mutation confirmation error")
                st.error("An unexpected error occurred during confirmation.")

        if cancel_click:
            try:
                result = cancel_mutation(
                    pending_id=str(pending_id),
                    dataset_id=dataset_id,
                )

                if result.get("success") and result.get("cancelled"):
                    st.session_state.pending_mutation = None
                    st.success(
                        "Pending change cancelled. No changes were made."
                    )
                    st.rerun()
                else:
                    st.error(
                        result.get(
                            "error",
                            "The backend did not confirm the cancellation.",
                        )
                    )

            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get(
                        "detail",
                        "The backend rejected the cancellation.",
                    )
                except (ValueError, AttributeError):
                    detail = "The backend rejected the cancellation."

                st.error(f"Cancellation failed: {detail}")

            except httpx.RequestError:
                logger.exception("Mutation cancellation request failed")
                st.error(
                    "Could not connect to FastAPI. Check that the backend is running."
                )

            except Exception:
                logger.exception("Unexpected mutation cancellation error")
                st.error("An unexpected error occurred during cancellation.")
