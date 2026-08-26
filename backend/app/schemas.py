from datetime import date
from typing import Literal
from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    login_id: str = Field(min_length=1,max_length=120)
    password: str = Field(min_length=6,max_length=200)
class MembershipOut(BaseModel):
    tenant_id: str; tenant_name: str; role: str; project_ids: list[str]
class MeOut(BaseModel):
    user_id: str; login_id: str; display_name: str; memberships: list[MembershipOut]
class TokenOut(BaseModel):
    access_token: str; token_type: str="bearer"; expires_in: int
class SuccessEnvelope(BaseModel):
    data: dict; meta: dict

class SheetsPreviewRequest(BaseModel):
    spreadsheet_url: str = Field(min_length=20,max_length=500)

class WorkerLoginRequest(BaseModel):
    tenant_code: str = Field(min_length=1,max_length=50)
    worker_code: str = Field(min_length=1,max_length=50)
    pin: str = Field(min_length=4,max_length=32)
class EnrollmentRequest(BaseModel):
    challenge_id: str
    public_key_jwk: dict
    signature: str
    device_label: str = Field(min_length=1,max_length=120)
class EnrollmentDecision(BaseModel):
    reason: str | None = Field(default=None,max_length=300)
class AttendanceChallengeRequest(BaseModel):
    event_type: Literal["CHECK_IN","CHECK_OUT"]
    project_id: str | None = None
class AttendanceSubmitRequest(BaseModel):
    challenge_id: str
    challenge: str
    event_type: Literal["CHECK_IN","CHECK_OUT"]
    project_id: str
    latitude: float = Field(ge=-90,le=90)
    longitude: float = Field(ge=-180,le=180)
    accuracy_m: float = Field(gt=0,le=10000)
    captured_at_client: str = Field(min_length=20,max_length=40)
    signature: str
    site_note: str | None = Field(default=None,max_length=200)
class AttendanceReviewDecision(BaseModel):
    approve: bool
    reason: str = Field(min_length=3,max_length=300)
class AttendancePolicyUpdate(BaseModel):
    late_grace_minutes: int = Field(ge=0,le=180)
    early_leave_grace_minutes: int = Field(ge=0,le=180)
class TimesheetCloseRequest(BaseModel):
    date_from: date
    date_to: date
    project_id: str | None = None
