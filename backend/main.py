
import logging

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db, Base, engine
from backend.schemas import ChatRequest, ChatResponse
from backend.services import generate_response
from backend.models import Message, Conversation
from backend.dataset_services import import_csv_file, get_dataset
from backend.dataset_tools import (
    confirm_dataset_mutation,
    cancel_dataset_mutation,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Multimodal Agent API")

Base.metadata.create_all(bind=engine)

MAX_FILE_SIZE = 20 * 1024 * 1024


class MutationActionRequest(BaseModel):
    pending_id: str
    dataset_id: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/datasets/upload")
async def upload_dataset(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file.",
        )

    try:
        file_bytes = await file.read(MAX_FILE_SIZE + 1)

        if not file_bytes:
            raise HTTPException(
                status_code=400,
                detail="The uploaded CSV file is empty.",
            )

        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail="CSV file exceeds the 20 MB limit.",
            )

        dataset = import_csv_file(
            file_bytes=file_bytes,
            filename=file.filename,
            db=db,
        )

        return {
            "dataset_id": str(dataset.id),
            "filename": dataset.original_filename,
            "row_count": dataset.row_count,
            "table_name": dataset.table_name,
        }

    except HTTPException:
        raise

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        db.rollback()
        logger.exception("CSV upload failed")
        raise HTTPException(
            status_code=500,
            detail="Could not import the CSV dataset.",
        ) from exc

    finally:
        await file.close()


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
):
    try:
        # Find or create the conversation.
        if request.conversation_id is not None:
            conversation = db.get(
                Conversation,
                request.conversation_id,
            )

            if conversation is None:
                raise HTTPException(
                    status_code=404,
                    detail="Conversation not found.",
                )
        else:
            conversation = Conversation(
                title=request.message[:30],
            )
            db.add(conversation)
            db.commit()
            db.refresh(conversation)

        # Resolve the selected dataset using server-side metadata.
        dataset_id = (
            str(request.dataset_id)
            if getattr(request, "dataset_id", None)
            else None
        )

        active_dataset_table = None

        if dataset_id:
            dataset = get_dataset(dataset_id, db)
            active_dataset_table = dataset.table_name

        # Save the user's message.
        user_msg = Message(
            conversation_id=conversation.id,
            role="user",
            content=request.message,
        )
        db.add(user_msg)
        db.commit()

        # Load the complete conversation history.
        messages = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation.id
            )
            .order_by(Message.id.asc())
            .all()
        )

        history = [
            {
                "role": msg.role,
                "content": msg.content,
            }
            for msg in messages
        ]

        generated = generate_response(
            history=history,
            active_dataset_id=dataset_id,
            active_dataset_table=active_dataset_table,
            db=db,
        )

        reply = generated["response"]
        pending_mutation = generated.get("pending_mutation")

        # Save the assistant's reply.
        assistant_msg = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
        )
        db.add(assistant_msg)
        db.commit()

        return ChatResponse(
            conversation_id=conversation.id,
            reply=reply,
            pending_mutations=pending_mutation,
        )

    except HTTPException:
        db.rollback()
        raise

    except ValueError as exc:
        db.rollback()
        logger.exception("Invalid chat or dataset request")
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        db.rollback()
        logger.exception("Chat request failed")
        raise HTTPException(
            status_code=502,
            detail="Chat generation or a connected tool failed.",
        ) from exc


@app.post("/datasets/mutations/confirmation")
def confirm_mutation(
    request: MutationActionRequest,
    db: Session = Depends(get_db),
):
    try:
        return confirm_dataset_mutation(
            pending_id=request.pending_id,
            dataset_id=request.dataset_id,
            db=db,
        )

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        db.rollback()
        logger.exception("Mutation confirmation failed")
        raise HTTPException(
            status_code=500,
            detail="Could not confirm the dataset mutation.",
        ) from exc


@app.post("/datasets/mutations/cancel")
def cancel_mutation(
    request: MutationActionRequest,
):
    try:
        return cancel_dataset_mutation(
            pending_id=request.pending_id,
            dataset_id=request.dataset_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception("Mutation cancellation failed")
        raise HTTPException(
            status_code=500,
            detail="Could not cancel the dataset mutation.",
        ) from exc
