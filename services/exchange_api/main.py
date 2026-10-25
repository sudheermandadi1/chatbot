
import re

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="Exchange Rate REST API")

FRANKFURTER_URL = "https://api.frankfurter.dev/v2"


class ConversionRequest(BaseModel):
    base_currency: str
    quote_currency: str
    amount: float = Field(gt=0, le=1_000_000_000)

    @field_validator("base_currency", "quote_currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.upper()

        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError(
                "Use a proper three-letter currency code"
            )

        return value


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "exchange-rate",
    }


@app.post("/convert")
async def convert_currency(request: ConversionRequest):
    if request.base_currency == request.quote_currency:
        return {
            "base_currency": request.base_currency,
            "quote_currency": request.quote_currency,
            "amount": request.amount,
            "rate": 1.0,
            "converted_amount": request.amount,
            "rate_date": None,
            "note": "Same currency; no conversion required.",
        }

    url = (
        f"{FRANKFURTER_URL}/rate/"
        f"{request.base_currency.lower()}/"
        f"{request.quote_currency.lower()}"
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404, 422):
            raise HTTPException(
                status_code=422,
                detail="Unsupported currency pair or unavailable rate.",
            ) from e

        raise HTTPException(
            status_code=502,
            detail="The exchange-rate provider failed.",
        ) from e

    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail="Could not reach the exchange-rate provider.",
        ) from e

    rate = float(data["rate"])

    return {
        "base_currency": request.base_currency,
        "quote_currency": request.quote_currency,
        "amount": request.amount,
        "rate": rate,
        "converted_amount": round(request.amount * rate, 2),
        "rate_date": data.get("date"),
        "source": "Frankfurter",
    }
