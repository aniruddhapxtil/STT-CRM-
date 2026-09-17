import json
import os
import re
import smtplib
import tempfile
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache

import torch
import whisper
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from transformers import AutoModelForCausalLM, AutoTokenizer


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://voicecrm:voicecrm@localhost:5432/voicecrm"
    whisper_model: str = "base"
    lfm_model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    max_audio_mb: int = 20
    cors_origins: str = "http://localhost:5173"

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "alerts@dataphi.ai"
    smtp_password: str = "your-app-password"
    enable_notifications: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self):
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def settings():
    return Settings()


engine = create_engine(settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# =====================================================================
# Database Models
# =====================================================================

class Role(Base):
    __tablename__ = "role"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, nullable=False)
    user_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email_id: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    designation: Mapped[str | None] = mapped_column(String(150))
    region: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(50))
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    account_manager: Mapped[str | None] = mapped_column(String(150))
    region: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(100))
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    subsidiaries: Mapped[list["Subsidiary"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="account")
    leads: Mapped[list["Lead"]] = relationship(back_populates="account")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="account")
    projects: Mapped[list["Project"]] = relationship(back_populates="account")


class Subsidiary(Base):
    __tablename__ = "subsidiary"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)
    subsidiary_name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(100))
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    account: Mapped[Account] = relationship(back_populates="subsidiaries")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="subsidiary")


class Contact(Base):
    __tablename__ = "contact"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)
    subsidiary_id: Mapped[int | None] = mapped_column(ForeignKey("subsidiary.id"), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    designation: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(320))
    mobile: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    account: Mapped[Account] = relationship(back_populates="contacts")
    subsidiary: Mapped[Subsidiary | None] = relationship(back_populates="contacts")
    leads: Mapped[list["Lead"]] = relationship(back_populates="contact")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="contact")


class Lead(Base):
    __tablename__ = "lead"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lead_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)
    subsidiary_id: Mapped[int | None] = mapped_column(ForeignKey("subsidiary.id"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id"), nullable=True)
    account_manager: Mapped[str | None] = mapped_column(String(150))
    deal_size: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str | None] = mapped_column(String(10), default="AED")
    type: Mapped[str | None] = mapped_column(String(50), default="Warm")
    stage: Mapped[str | None] = mapped_column(String(50), default="Non-Qualified")
    lead_source: Mapped[str | None] = mapped_column(String(150), default="Voice Capture")
    service: Mapped[str | None] = mapped_column(String(150))
    technology: Mapped[str | None] = mapped_column(String(150))
    next_steps: Mapped[str | None] = mapped_column(String(255))
    next_action_date: Mapped[date | None] = mapped_column(Date)
    closure_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    account: Mapped[Account] = relationship(back_populates="leads")
    contact: Mapped[Contact | None] = relationship(back_populates="leads")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="lead")


class Opportunity(Base):
    __tablename__ = "opportunity"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    opportunity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lead.id"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)
    subsidiary_id: Mapped[int | None] = mapped_column(ForeignKey("subsidiary.id"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id"), nullable=True)
    account_manager: Mapped[str | None] = mapped_column(String(150))
    deal_size: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str | None] = mapped_column(String(10), default="AED")
    project_type: Mapped[str | None] = mapped_column(String(50), default="T&M")
    service: Mapped[str | None] = mapped_column(String(150))
    stage: Mapped[str] = mapped_column(String(50), default="Discovery (40%)")
    probability: Mapped[int | None] = mapped_column(Integer, default=40)
    reason: Mapped[str | None] = mapped_column(String(255))
    opportunity_type: Mapped[str | None] = mapped_column(String(100), default="New Business")
    funded_by: Mapped[str | None] = mapped_column(String(100))
    opportunity_source: Mapped[str | None] = mapped_column(String(150))
    next_steps: Mapped[str | None] = mapped_column(String(255))
    next_action_date: Mapped[date | None] = mapped_column(Date)
    closure_date: Mapped[date | None] = mapped_column(Date)
    technology: Mapped[str | None] = mapped_column(String(150))
    notes: Mapped[str | None] = mapped_column(Text)
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    account: Mapped[Account] = relationship(back_populates="opportunities")
    contact: Mapped[Contact | None] = relationship(back_populates="opportunities")
    lead: Mapped[Lead] = relationship(back_populates="opportunities")
    project: Mapped["Project | None"] = relationship(back_populates="opportunity")


class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunity.id"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)
    subsidiary_id: Mapped[int | None] = mapped_column(ForeignKey("subsidiary.id"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id"), nullable=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), default="PO Awaited")
    po_number: Mapped[str | None] = mapped_column(String(150))
    value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str | None] = mapped_column(String(10), default="AED")
    start_date: Mapped[date | None] = mapped_column(Date)
    close_date: Mapped[date | None] = mapped_column(Date)
    technology: Mapped[str | None] = mapped_column(String(150))
    service: Mapped[str | None] = mapped_column(String(150))
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    account: Mapped[Account] = relationship(back_populates="projects")
    opportunity: Mapped[Opportunity] = relationship(back_populates="project")


class Activity(Base):
    __tablename__ = "activity"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    activity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("account.id"), nullable=True)
    subsidiary_id: Mapped[int | None] = mapped_column(ForeignKey("subsidiary.id"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id"), nullable=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("lead.id"), nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), nullable=True)
    record_type: Mapped[str | None] = mapped_column(String(50))
    record_action: Mapped[str | None] = mapped_column(String(50))
    account_name: Mapped[str | None] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    subsidiary_name: Mapped[str | None] = mapped_column(String(255))
    activity_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text)
    next_step: Mapped[str | None] = mapped_column(String(255))
    next_action_date: Mapped[date | None] = mapped_column(Date)
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)


class VoiceDraft(Base):
    __tablename__ = "voice_draft"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(320))
    user_phone: Mapped[str | None] = mapped_column(String(50))
    raw_transcript: Mapped[str] = mapped_column(Text, nullable=False)
    target_entity: Mapped[str] = mapped_column(String(50), default="lead")
    extracted_json: Mapped[str] = mapped_column(Text, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    clarification_prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="INCOMPLETE")
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VoiceInteraction(Base):
    __tablename__ = "voice_interactions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transcript: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(100))
    extracted_json: Mapped[str] = mapped_column(Text)
    processing_ms: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# =====================================================================
# Parsing & Normalization Helpers
# =====================================================================

def resolve_relative_date(v) -> date | None:
    """Safely converts natural language relative date strings or ISO formats into a datetime.date."""
    if not v:
        return None
    if isinstance(v, date):
        return v
    if not isinstance(v, str):
        return None

    cleaned = v.strip()
    if not cleaned or cleaned.lower() in ("null", "none"):
        return None

    # 1. Direct ISO format (YYYY-MM-DD)
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass

    # 2. Parse relative natural language keywords
    lower = cleaned.lower()
    today = date.today()

    if "tomorrow" in lower:
        return today + timedelta(days=1)
    if "today" in lower:
        return today

    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for idx, day in enumerate(days):
        if day in lower:
            current_day = today.weekday()  # Monday = 0, Sunday = 6
            days_ahead = (idx - current_day) % 7
            if days_ahead == 0 or "next" in lower:
                days_ahead += 7
            return today + timedelta(days=days_ahead)

    # 3. Common alternative string date formats
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    # Graceful fallback: return None instead of throwing a validation error
    return None


def sanitize_numeric_deal_size(v) -> Decimal | None:
    """Handles deal sizes containing commas, currency tags, or strings (e.g. '250,000 AED')."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float, Decimal)):
        try:
            return Decimal(str(v))
        except Exception:
            return None
    if isinstance(v, str):
        cleaned = re.sub(r"[^\d.]", "", v)
        if cleaned:
            try:
                return Decimal(cleaned)
            except Exception:
                return None
    return None


# =====================================================================
# Pydantic Schemas
# =====================================================================

class ExtractedAccount(BaseModel):
    account_name: str | None = None
    account_manager: str | None = None
    region: str | None = None
    industry: str | None = None


class ExtractedSubsidiary(BaseModel):
    subsidiary_name: str | None = None
    region: str | None = None
    industry: str | None = None


class ExtractedContact(BaseModel):
    contact_name: str | None = None
    designation: str | None = None
    email: str | None = None
    mobile: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None


class ExtractedLead(BaseModel):
    lead_name: str | None = None
    deal_size: Decimal | None = Field(default=None, ge=0)
    currency: str | None = "AED"
    type: str | None = "Warm"
    stage: str | None = "Non-Qualified"
    lead_source: str | None = "Voice Note"
    service: str | None = None
    technology: str | None = None
    next_steps: str | None = None
    next_action_date: date | None = None
    closure_date: date | None = None
    notes: str | None = None

    @field_validator("deal_size", mode="before")
    @classmethod
    def clean_deal_size(cls, v):
        return sanitize_numeric_deal_size(v)

    @field_validator("next_action_date", "closure_date", mode="before")
    @classmethod
    def clean_date_fields(cls, v):
        return resolve_relative_date(v)


class ExtractedOpportunity(BaseModel):
    opportunity_name: str | None = None
    deal_size: Decimal | None = Field(default=None, ge=0)
    currency: str | None = "AED"
    project_type: str | None = "T&M"
    service: str | None = None
    stage: str | None = "Discovery (40%)"
    probability: int | None = 40
    technology: str | None = None
    next_steps: str | None = None
    next_action_date: date | None = None
    closure_date: date | None = None
    notes: str | None = None

    @field_validator("deal_size", mode="before")
    @classmethod
    def clean_deal_size(cls, v):
        return sanitize_numeric_deal_size(v)

    @field_validator("next_action_date", "closure_date", mode="before")
    @classmethod
    def clean_date_fields(cls, v):
        return resolve_relative_date(v)


class FullLifecycleVoicePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: str = "create_lead"
    account: ExtractedAccount = Field(default_factory=ExtractedAccount)
    subsidiary: ExtractedSubsidiary = Field(default_factory=ExtractedSubsidiary)
    contact: ExtractedContact = Field(default_factory=ExtractedContact)
    lead: ExtractedLead = Field(default_factory=ExtractedLead)
    opportunity: ExtractedOpportunity = Field(default_factory=ExtractedOpportunity)

    @field_validator("account", "subsidiary", "contact", "lead", "opportunity", mode="before")
    @classmethod
    def coerce_null_to_empty_dict(cls, v):
        return {} if v is None else v


class ConfirmedCommitPayload(BaseModel):
    draft_id: int | None = None
    intent: str = "create_lead"
    user_id: int | None = None
    account: ExtractedAccount
    subsidiary: ExtractedSubsidiary = Field(default_factory=ExtractedSubsidiary)
    contact: ExtractedContact = Field(default_factory=ExtractedContact)
    lead: ExtractedLead = Field(default_factory=ExtractedLead)
    opportunity: ExtractedOpportunity = Field(default_factory=ExtractedOpportunity)


SYSTEM_PROMPT = """### ROLE
You are the DataPhi CRM Extraction Engine. Your job is to convert spoken sales notes into structured JSON without missing any spoken detail.

### CRITICAL RULES:
1. MAXIMIZE CAPTURE: Map every spoken detail into its appropriate field:
   - "account manager is [Name]" -> account.account_manager
   - "operating in [Region]" or "located in [Region]" -> account.region
   - "industry is [Industry]" or "under [Industry]" -> account.industry
   - "[Name], who is [Title]" -> contact.contact_name, contact.designation
   - "next step is [Action]" -> lead.next_steps, opportunity.next_steps
2. NOTES CONSTRAINT (STRICT): Keep the "notes" field null or under 6 words. NEVER repeat, copy, or echo the transcript back into notes.
3. OPPORTUNITY WITH LEAD: When creating an opportunity, populate BOTH opportunity and lead objects, and ALWAYS populate account metadata (account_name, account_manager, region, industry) if spoken.
4. ZERO FABRICATION: If a field is not spoken, return null. Never invent fake placeholder data.

### SCHEMA (JSON ONLY)
{
  "intent": "create_opportunity" | "create_lead" | "create_account" | "create_contact",
  "account": {"account_name": null, "region": null, "industry": null, "account_manager": null},
  "subsidiary": {"subsidiary_name": null},
  "contact": {"contact_name": null, "designation": null, "email": null, "mobile": null, "notes": null},
  "lead": {"lead_name": null, "deal_size": null, "currency": "AED", "type": "Hot", "service": null, "technology": null, "next_steps": null, "next_action_date": null, "closure_date": null, "notes": null},
  "opportunity": {"opportunity_name": null, "deal_size": null, "currency": "AED", "project_type": "T&M", "service": null, "stage": "Discovery (40%)", "probability": 40, "technology": null, "next_steps": null, "notes": null}
}

### FEW-SHOT EXAMPLE
Transcript: "Create an opportunity for Dubai Integrated Economic Zones for 450,000 AED on Azure Databricks with Tariq Mansoor, VP of Smart Cities. The scope is IoT Telemetry Lakehouse under T&M. Account manager is Ramanj Falasi, operating in Dubai region under Semi Govt industry. Next step is architecture deck."
Output:
{
  "intent": "create_opportunity",
  "account": {"account_name": "Dubai Integrated Economic Zones", "region": "Dubai", "industry": "Semi Govt", "account_manager": "Ramanj Falasi"},
  "subsidiary": null,
  "contact": {"contact_name": "Tariq Mansoor", "designation": "VP of Smart Cities", "email": null, "mobile": null, "notes": null},
  "lead": {"lead_name": "IoT Telemetry Lakehouse", "deal_size": 450000, "currency": "AED", "type": "Hot", "service": "IoT Telemetry Lakehouse", "technology": "Azure Databricks", "next_steps": "Architecture deck", "next_action_date": null, "closure_date": null, "notes": null},
  "opportunity": {"opportunity_name": "IoT Telemetry Lakehouse", "deal_size": 450000, "currency": "AED", "project_type": "T&M", "service": "IoT Telemetry Lakehouse", "stage": "Discovery (40%)", "probability": 40, "technology": "Azure Databricks", "next_steps": "Architecture deck", "notes": null}
}
"""


def parse_json(text: str):
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    a = text.find("{")
    if a < 0:
        raise ValueError("No JSON structure found in model output.")
    text = text[a:]

    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Self-Healing: Close cut-off strings if odd quote count
    quotes = len(re.findall(r'(?<!\\)"', text))
    if quotes % 2 != 0:
        text += '"'

    # Remove dangling incomplete trailing keys
    text = re.sub(r',\s*$', '', text)
    text = re.sub(r',\s*"\w+":\s*"?$', '', text)

    # Balance unclosed curly braces
    open_braces = text.count("{")
    close_braces = text.count("}")
    if open_braces > close_braces:
        text += "}" * (open_braces - close_braces)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(cleaned)


class WhisperService:
    def __init__(self):
        self.model = whisper.load_model(settings().whisper_model)

    def transcribe(self, path):
        domain_prompt = (
            "Dubai, Dubai Integrated Economic Zones, DIEZ, DSO, DAFZA, Emaar, "
            "Majid Al Futtaim, Rashid Al-Falasi, Tariq Mansoor, Ramanj Falasi, "
            "Semi Government, AED, Databricks, Azure, Power BI, Lakehouse, Telemetry"
        )
        result = self.model.transcribe(path, fp16=False, initial_prompt=domain_prompt)
        text = (result.get("text") or "").strip()
        if not text:
            raise ValueError("No speech was detected.")
        return text


class LFMService:
    def __init__(self):
        model_id = settings().lfm_model
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32

        self.model = AutoModelForCausalLM.from_pretrained(
            model_id,
            dtype=dtype,
            low_cpu_mem_usage=True,
        ).to(self.device)
        self.model.eval()

    def extract(self, transcript: str) -> FullLifecycleVoicePayload:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Reference date: {date.today().isoformat()}\nTranscript: {transcript}\nOutput JSON strictly:",
            },
        ]
        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.device)

        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=700,
                do_sample=False,
                repetition_penalty=1.1,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        text = self.tokenizer.decode(output[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True)
        print("\n--- EXTRACTED RAW MODEL OUTPUT ---\n", text, "\n----------------------------------\n")
        return FullLifecycleVoicePayload.model_validate(parse_json(text))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sanitize_value(val: str | None) -> str | None:
    if not val:
        return None
    cleaned = val.strip()

    dummy_patterns = [
        r"^(enterprise\s+)?company(\s+name)?$",
        r"^john\s+doe$",
        r"^jane\s+doe$",
        r"^client(\s+stakeholder)?$",
        r"^sales\s+rep(resentative)?$",
        r"^not\s+specified$",
        r"^unknown$",
        r"^n/a$",
        r"^none$",
        r"^null$",
        r"^region\s+not\s+specified$",
        r"^industry\s+not\s+specified$",
        r"^account\s+manager\s+not\s+specified$",
        r"^.*@example\.com$",
        r"^.*@mobile\.com$",
    ]

    for pattern in dummy_patterns:
        if re.match(pattern, cleaned, re.IGNORECASE):
            return None

    return cleaned


def deterministic_transcript_fallback(payload: FullLifecycleVoicePayload, transcript: str):
    """
    Safety net: Extracts spoken details that small LLMs drop due to context dilution.
    """
    # 1. Catch Account Manager
    if not payload.account.account_manager:
        match = re.search(
            r"account\s+manager\s+(?:for\s+(?:this\s+)?account\s+)?(?:is\s+)?([A-Za-z\s,\.]+?)(?:,|\.|\boperating\b|\bunder\b|\bwith\b|\bnext\b|$)",
            transcript,
            re.IGNORECASE,
        )
        if match:
            extracted_am = match.group(1).strip().strip(",.")
            if len(extracted_am.split()) <= 4:
                payload.account.account_manager = extracted_am

    # 2. Catch Region if missing or misheard
    if not payload.account.region:
        reg_match = re.search(r"\b(dubai|abu\s+dhabi|sharjah|ksa|riyadh|qatar|gcc)\b", transcript, re.IGNORECASE)
        if reg_match:
            payload.account.region = reg_match.group(1).title()
        elif re.search(r"\bthe\s+by\s+region\b", transcript, re.I):
            payload.account.region = "Dubai"

    # 3. Catch Industry if missing
    if not payload.account.industry:
        ind_match = re.search(r"(?:under|in)\s+(?:a\s+)?([A-Za-z\s]+?)\s+industry", transcript, re.IGNORECASE)
        if ind_match:
            payload.account.industry = ind_match.group(1).strip().title()

    # 4. Catch Designation if missing
    if payload.contact.contact_name and not payload.contact.designation:
        desig_match = re.search(
            r"(?:who\s+is\s+(?:the\s+)?|designation\s+(?:is\s+)?)([A-Za-z\s]+?)(?:\.|\bthe\s+scope\b|\bscope\b|\bwith\b|\band\b|$)",
            transcript,
            re.IGNORECASE,
        )
        if desig_match:
            payload.contact.designation = desig_match.group(1).strip()

    # 5. Catch Next Steps if missing
    if not payload.lead.next_steps:
        next_step_match = re.search(r"next\s+step\s+is\s+([^.]+)", transcript, re.IGNORECASE)
        if next_step_match:
            extracted_step = next_step_match.group(1).strip()
            payload.lead.next_steps = extracted_step
            if payload.opportunity:
                payload.opportunity.next_steps = extracted_step


def evaluate_mandatory_fields(payload: FullLifecycleVoicePayload, transcript: str) -> tuple[list[str], str]:
    missing = []
    questions = []

    if re.search(r"\b(create|add|new)\s+(an?\s+)?(opportunity|deal|opty)\b", transcript, re.I):
        payload.intent = "create_opportunity"
    elif re.search(r"\b(create|add|new)\s+(an?\s+)?account\b", transcript, re.I):
        payload.intent = "create_account"
    elif re.search(r"\b(create|add|new)\s+(an?\s+)?contact\b", transcript, re.I):
        payload.intent = "create_contact"

    payload.account.account_name = sanitize_value(payload.account.account_name)
    payload.account.account_manager = sanitize_value(payload.account.account_manager)
    payload.contact.contact_name = sanitize_value(payload.contact.contact_name)
    payload.contact.email = sanitize_value(payload.contact.email)
    payload.contact.mobile = sanitize_value(payload.contact.mobile)

    if payload.intent == "create_account":
        if not payload.account.account_name:
            missing.append("account.account_name")
            questions.append("What is the company or enterprise organization name for this new account?")
        return missing, " ".join(questions)

    if payload.intent == "create_contact":
        if not payload.account.account_name:
            missing.append("account.account_name")
            questions.append("Which company does this contact stakeholder belong to?")
        if not payload.contact.contact_name:
            missing.append("contact.contact_name")
            questions.append("What is the full name of the contact stakeholder?")
        return missing, " ".join(questions)

    if payload.intent == "create_opportunity":
        if not payload.account.account_name:
            missing.append("account.account_name")
            questions.append("Which enterprise account is this opportunity being proposed to?")
        if not payload.contact.contact_name:
            missing.append("contact.contact_name")
            questions.append("Who is the client commercial decision maker or primary stakeholder?")
        return missing, " ".join(questions)

    # Lead Intent
    if (
        payload.account.account_name
        and payload.contact.contact_name
        and payload.account.account_name.strip().lower() == payload.contact.contact_name.strip().lower()
    ):
        payload.account.account_name = None

    if not payload.account.account_name:
        missing.append("account.account_name")
        questions.append("Which company or enterprise account is this discussion for?")

    if not payload.contact.contact_name:
        missing.append("contact.contact_name")
        questions.append("Who was the client stakeholder or decision maker you spoke with?")

    return missing, " ".join(questions)


def send_notification_alert(recipient_email: str | None, phone: str | None, draft_id: int, summary: str, questions: str):
    if not settings().enable_notifications or not recipient_email:
        print(f"\n[DRAFT NOTIFICATION - DRAFT #{draft_id}]")
        print(f"To: {recipient_email or 'Rep Phone: ' + str(phone)}")
        print(f"Captured: {summary}")
        print(f"Missing Questions: {questions}\n")
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = settings().smtp_user
        msg["To"] = recipient_email
        msg["Subject"] = f"Action Required: Incomplete CRM Voice Note (Draft #{draft_id})"

        body = (
            f"Hi,\n\n"
            f"You recorded a voice memo, but some required fields are missing:\n\n"
            f"Captured Context:\n\"{summary}\"\n\n"
            f"Please reply or open the portal to complete the following:\n"
            f"{questions}\n\n"
            f"Portal Link: http://localhost:5173/?draft_id={draft_id}\n\n"
            f"— DataPhi CRM"
        )
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(settings().smtp_host, settings().smtp_port) as server:
            server.starttls()
            server.login(settings().smtp_user, settings().smtp_password)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to dispatch email alert: {e}")


def execute_full_hierarchy_commit(
    db: Session, payload: ConfirmedCommitPayload
) -> dict:
    user_id = payload.user_id

    # 1. Upsert Account
    account_name = (payload.account.account_name or "").strip()
    if not account_name:
        raise HTTPException(400, "Account name is mandatory to commit.")

    account = db.scalar(select(Account).where(Account.account_name.ilike(account_name)))
    if not account:
        account = Account(
            account_name=account_name,
            account_manager=payload.account.account_manager,
            region=payload.account.region,
            industry=payload.account.industry,
            created_by=user_id,
        )
        db.add(account)
        db.flush()
    else:
        if payload.account.account_manager and not account.account_manager:
            account.account_manager = payload.account.account_manager
        if payload.account.region and not account.region:
            account.region = payload.account.region
        if payload.account.industry and not account.industry:
            account.industry = payload.account.industry

    if payload.intent == "create_account":
        activity = Activity(
            activity_name=f"Account Confirmed: {account.account_name}",
            account_id=account.id,
            record_type="Account",
            record_action="System Action",
            account_name=account.account_name,
            notes=f"Confirmed and committed. Manager: {account.account_manager or 'N/A'}, Region: {account.region or 'N/A'}",
            created_by=user_id,
        )
        db.add(activity)
        db.commit()
        return {
            "entity_type": "Account",
            "account_id": account.id,
            "account_name": account.account_name,
            "account_manager": account.account_manager,
            "activity_id": activity.id,
        }

    # 2. Subsidiary Setup
    subsidiary = None
    if payload.subsidiary.subsidiary_name:
        sub_name = payload.subsidiary.subsidiary_name.strip()
        subsidiary = db.scalar(
            select(Subsidiary).where(
                Subsidiary.subsidiary_name.ilike(sub_name),
                Subsidiary.account_id == account.id,
            )
        )
        if not subsidiary:
            subsidiary = Subsidiary(
                account_id=account.id,
                subsidiary_name=sub_name,
                region=payload.subsidiary.region or account.region,
                industry=payload.subsidiary.industry or account.industry,
                created_by=user_id,
            )
            db.add(subsidiary)
            db.flush()

    # 3. Upsert Contact
    contact_name = (payload.contact.contact_name or "").strip()
    if not contact_name:
        raise HTTPException(400, "Contact stakeholder name is mandatory to commit.")

    contact = db.scalar(
        select(Contact).where(
            Contact.contact_name.ilike(contact_name),
            Contact.account_id == account.id,
        )
    )
    if not contact:
        contact = Contact(
            account_id=account.id,
            subsidiary_id=subsidiary.id if subsidiary else None,
            contact_name=contact_name,
            designation=payload.contact.designation,
            email=payload.contact.email,
            mobile=payload.contact.mobile,
            linkedin_url=payload.contact.linkedin_url,
            notes=payload.contact.notes,
            created_by=user_id,
        )
        db.add(contact)
        db.flush()
    else:
        if payload.contact.designation and not contact.designation:
            contact.designation = payload.contact.designation
        if payload.contact.mobile and not contact.mobile:
            contact.mobile = payload.contact.mobile
        if payload.contact.email and not contact.email:
            contact.email = payload.contact.email

    # 4. Lead and Opportunity Generation
    if payload.intent == "create_opportunity":
        opp_title = (
            payload.opportunity.opportunity_name
            or payload.lead.lead_name
            or f"{account.account_name} - Commercial Opportunity"
        )

        backfilled_lead = Lead(
            lead_name=f"{opp_title} (Backfilled Lead)",
            account_id=account.id,
            subsidiary_id=subsidiary.id if subsidiary else None,
            contact_id=contact.id,
            account_manager=payload.account.account_manager or account.account_manager,
            deal_size=payload.opportunity.deal_size or payload.lead.deal_size,
            currency=payload.opportunity.currency or payload.lead.currency or "AED",
            type="Hot",
            stage="Qualified",
            lead_source="Opportunity Voice Backfill",
            service=payload.opportunity.service or payload.lead.service,
            technology=payload.opportunity.technology or payload.lead.technology,
            next_steps=payload.opportunity.next_steps or payload.lead.next_steps,
            next_action_date=payload.opportunity.next_action_date or payload.lead.next_action_date,
            closure_date=payload.opportunity.closure_date or payload.lead.closure_date,
            notes=payload.opportunity.notes or payload.lead.notes or "Auto-backfilled via confirmed opportunity creation",
            created_by=user_id,
        )
        db.add(backfilled_lead)
        db.flush()

        opportunity = Opportunity(
            opportunity_name=opp_title,
            lead_id=backfilled_lead.id,
            account_id=account.id,
            subsidiary_id=subsidiary.id if subsidiary else None,
            contact_id=contact.id,
            account_manager=payload.account.account_manager or account.account_manager,
            deal_size=payload.opportunity.deal_size or payload.lead.deal_size,
            currency=payload.opportunity.currency or payload.lead.currency or "AED",
            project_type=payload.opportunity.project_type or "T&M",
            service=payload.opportunity.service or payload.lead.service,
            stage=payload.opportunity.stage or "Discovery (40%)",
            probability=payload.opportunity.probability or 40,
            technology=payload.opportunity.technology or payload.lead.technology,
            next_steps=payload.opportunity.next_steps or payload.lead.next_steps,
            next_action_date=payload.opportunity.next_action_date or payload.lead.next_action_date,
            closure_date=payload.opportunity.closure_date or payload.lead.closure_date,
            notes=payload.opportunity.notes or payload.lead.notes,
            created_by=user_id,
        )
        db.add(opportunity)
        db.flush()

        activity = Activity(
            activity_name=f"Opportunity Confirmed: {opportunity.opportunity_name}",
            account_id=account.id,
            subsidiary_id=subsidiary.id if subsidiary else None,
            contact_id=contact.id,
            lead_id=backfilled_lead.id,
            opportunity_id=opportunity.id,
            record_type="Opportunity",
            record_action="System Action",
            account_name=account.account_name,
            contact_name=contact.contact_name,
            notes=f"Confirmed Opportunity #{opportunity.id} with backfilled Qualified Lead #{backfilled_lead.id}",
            created_by=user_id,
        )
        db.add(activity)
        db.commit()

        return {
            "entity_type": "Opportunity",
            "account_id": account.id,
            "account_name": account.account_name,
            "account_manager": account.account_manager,
            "contact_id": contact.id,
            "contact_name": contact.contact_name,
            "lead_id": backfilled_lead.id,
            "lead_name": backfilled_lead.lead_name,
            "lead_stage": backfilled_lead.stage,
            "lead_type": backfilled_lead.type,
            "opportunity_id": opportunity.id,
            "opportunity_name": opportunity.opportunity_name,
            "opportunity_stage": opportunity.stage,
            "deal_size": float(opportunity.deal_size) if opportunity.deal_size else 0,
            "currency": opportunity.currency,
            "activity_id": activity.id,
        }

    # Default Lead Creation
    lead_title = (
        payload.lead.lead_name
        or f"{account.account_name} - {payload.lead.service or 'Consulting'} Engagement"
    )

    lead = Lead(
        lead_name=lead_title,
        account_id=account.id,
        subsidiary_id=subsidiary.id if subsidiary else None,
        contact_id=contact.id,
        account_manager=payload.account.account_manager or account.account_manager,
        deal_size=payload.lead.deal_size,
        currency=payload.lead.currency or "AED",
        type=payload.lead.type or "Warm",
        stage=payload.lead.stage or "Non-Qualified",
        lead_source=payload.lead.lead_source or "Voice Capture",
        service=payload.lead.service,
        technology=payload.lead.technology,
        next_steps=payload.lead.next_steps,
        next_action_date=payload.lead.next_action_date,
        closure_date=payload.lead.closure_date,
        notes=payload.lead.notes,
        created_by=user_id,
    )
    db.add(lead)
    db.flush()

    activity = Activity(
        activity_name=f"Lead Confirmed: {lead.lead_name}",
        account_id=account.id,
        subsidiary_id=subsidiary.id if subsidiary else None,
        contact_id=contact.id,
        lead_id=lead.id,
        record_type="Lead",
        record_action="Call",
        account_name=account.account_name,
        contact_name=contact.contact_name,
        notes=payload.lead.notes or "Voice captured sales lead",
        created_by=user_id,
    )
    db.add(activity)
    db.commit()

    return {
        "entity_type": "Lead",
        "account_id": account.id,
        "account_name": account.account_name,
        "account_manager": account.account_manager,
        "contact_id": contact.id,
        "contact_name": contact.contact_name,
        "lead_id": lead.id,
        "lead_name": lead.lead_name,
        "lead_stage": lead.stage,
        "activity_id": activity.id,
    }


# =====================================================================
# FastAPI Application & Endpoints
# =====================================================================

app = FastAPI(title="DataPhi CRM Voice Ingestion Engine", version="5.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_whisper = None
_lfm = None


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    global _whisper, _lfm
    _whisper = WhisperService()
    _lfm = LFMService()


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/api/voice/process")
async def process_voice(
    audio: UploadFile = File(...),
    user_id: int | None = Form(None),
    user_email: str | None = Form(None),
    user_phone: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not audio.content_type or not audio.content_type.startswith("audio/"):
        raise HTTPException(400, "Please upload a valid audio recording.")

    data = await audio.read()
    if not data:
        raise HTTPException(400, "The recording buffer is empty.")
    if len(data) > settings().max_audio_mb * 1024 * 1024:
        raise HTTPException(413, f"Audio exceeds {settings().max_audio_mb} MB limit.")

    fd, path = tempfile.mkstemp(prefix="crm_voice_", suffix=os.path.splitext(audio.filename or ".webm")[1])
    os.close(fd)
    started = time.perf_counter()

    try:
        with open(path, "wb") as f:
            f.write(data)

        transcript = _whisper.transcribe(path)
        payload = _lfm.extract(transcript)
        
        # Deterministic regex recovery for any dropped fields
        deterministic_transcript_fallback(payload, transcript)
        
        missing_fields, clarification_prompt = evaluate_mandatory_fields(payload, transcript)

        draft = VoiceDraft(
            user_id=user_id,
            user_email=user_email,
            user_phone=user_phone,
            raw_transcript=transcript,
            target_entity=payload.intent,
            extracted_json=json.dumps(payload.model_dump(mode="json")),
            missing_fields=missing_fields,
            clarification_prompt=clarification_prompt,
            status="INCOMPLETE" if missing_fields else "STAGED_READY",
        )
        db.add(draft)
        db.commit()

        if missing_fields:
            send_notification_alert(
                recipient_email=user_email,
                phone=user_phone,
                draft_id=draft.id,
                summary=transcript,
                questions=clarification_prompt,
            )

        return {
            "status": "draft_saved_incomplete" if missing_fields else "draft_ready_for_review",
            "draft_id": draft.id,
            "intent": payload.intent,
            "transcript": transcript,
            "extracted_data": payload.model_dump(mode="json"),
            "missing_fields": missing_fields,
            "clarification_prompt": clarification_prompt,
            "processing_ms": int((time.perf_counter() - started) * 1000),
        }

    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"Processing failure: {exc}") from exc
    finally:
        if os.path.exists(path):
            os.remove(path)


@app.post("/api/voice/draft/{draft_id}/resume")
async def resume_voice_draft(
    draft_id: int,
    additional_audio: UploadFile | None = File(None),
    additional_text: str | None = Form(None),
    db: Session = Depends(get_db),
):
    draft = db.scalar(select(VoiceDraft).where(VoiceDraft.id == draft_id))
    if not draft:
        raise HTTPException(404, "Draft record not found.")
    if draft.status == "COMPLETED":
        raise HTTPException(400, "Draft is already finalized.")

    supplementary_text = ""
    if additional_audio:
        fd, path = tempfile.mkstemp(prefix="crm_draft_patch_", suffix=".webm")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(await additional_audio.read())
            supplementary_text = _whisper.transcribe(path)
        finally:
            if os.path.exists(path):
                os.remove(path)
    elif additional_text:
        supplementary_text = additional_text.strip()
    else:
        raise HTTPException(400, "Provide supplementary audio or text input.")

    merged_transcript = f"{draft.raw_transcript}. Follow-up details: {supplementary_text}"
    updated_payload = _lfm.extract(merged_transcript)
    deterministic_transcript_fallback(updated_payload, merged_transcript)

    missing_fields, clarification_prompt = evaluate_mandatory_fields(updated_payload, merged_transcript)
    draft.raw_transcript = merged_transcript
    draft.extracted_json = json.dumps(updated_payload.model_dump(mode="json"))
    draft.missing_fields = missing_fields
    draft.clarification_prompt = clarification_prompt
    draft.status = "INCOMPLETE" if missing_fields else "STAGED_READY"
    db.commit()

    return {
        "status": "draft_saved_incomplete" if missing_fields else "draft_ready_for_review",
        "draft_id": draft.id,
        "intent": updated_payload.intent,
        "transcript": merged_transcript,
        "extracted_data": updated_payload.model_dump(mode="json"),
        "missing_fields": missing_fields,
        "clarification_prompt": clarification_prompt,
    }


@app.post("/api/voice/commit")
async def commit_voice_records(
    payload: ConfirmedCommitPayload,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()

    if payload.intent in ["create_lead", "create_opportunity"]:
        if not payload.account.account_name:
            raise HTTPException(422, "Cannot commit without enterprise Account Name.")
        if not payload.contact.contact_name:
            raise HTTPException(422, "Cannot commit without Contact Stakeholder Name.")
    elif payload.intent == "create_account":
        if not payload.account.account_name:
            raise HTTPException(422, "Cannot commit without Account Name.")

    try:
        created_records = execute_full_hierarchy_commit(db, payload)

        if payload.draft_id:
            draft = db.scalar(select(VoiceDraft).where(VoiceDraft.id == payload.draft_id))
            if draft:
                draft.status = "COMPLETED"
                draft.missing_fields = []
                draft.clarification_prompt = None
                db.commit()

        interaction = VoiceInteraction(
            transcript=f"Confirmed commit for {payload.intent}",
            intent=payload.intent,
            extracted_json=json.dumps(payload.model_dump(mode="json")),
            status="success",
            processing_ms=int((time.perf_counter() - started) * 1000),
        )
        db.add(interaction)
        db.commit()

        return {
            "status": "committed",
            "intent": payload.intent,
            "created_records": created_records,
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"Database transaction failed: {exc}") from exc