from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)
    tenant_name: str = Field(min_length=1, max_length=100)


class TokenRefresh(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class DateRange(StrictModel):
    start_date: datetime | None = None
    end_date: datetime | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def aware_dates(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Dates must include a timezone")
        return value

    @model_validator(mode="after")
    def valid_range(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must precede end_date")
        return self


class TransactionFilters(DateRange):
    account_id: str | None = None
    transaction_status: str | None = None
    search: str | None = Field(default=None, max_length=150)
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort: Literal["newest", "oldest", "amount"] = "newest"


class RunRequest(StrictModel):
    request: str = Field(min_length=5, max_length=4000)


class DecisionInput(StrictModel):
    reason: str = Field(min_length=5, max_length=1000)
    version: int = Field(ge=1)


class ReconcileInput(DateRange):
    bank_statement_id: str
    account_id: str


class Plan(DateRange):
    transactions: bool = True
    risk: bool = True
    reconciliation: bool = True
    account_id: str | None = None
    transaction_id: str | None = None
    min_amount: Decimal | None = None
    compare_months: bool = False
    inspect_approvals: bool = False
    reason: str = "Daily financial operations review"


class GroundedNarrative(StrictModel):
    summary: str = Field(max_length=2000)
    evidence_ids: list[str] = Field(max_length=30)


class ImportRow(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    account_id: str
    amount: Decimal = Field(max_digits=18, decimal_places=2)
    currency: Literal["GBP", "USD", "EUR", "JPY", "CHF", "CAD", "AUD"]
    reference: str = Field(min_length=1, max_length=150)
    booked_at: datetime
    description: str = Field(default="", max_length=1000)

    @field_validator("amount")
    @classmethod
    def finite(cls, value):
        if not value.is_finite():
            raise ValueError("Amount must be finite")
        return value

    @field_validator("booked_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value


class RiskPolicyInput(StrictModel):
    high_value: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    velocity: int = Field(ge=2, le=10000)


class ApprovalPolicyInput(StrictModel):
    two_person_threshold: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    expires_hours: int = Field(ge=1, le=168)
