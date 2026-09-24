import asyncio
import json
import os
import re
import smtplib
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache
from pathlib import Path

import boto3
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
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
    selectinload,
    sessionmaker,
)

# =====================================================================
# Configuration & Cloud Settings
# =====================================================================

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://voicecrm:voicecrm@localhost:5432/voicecrm"
    aws_region: str = "us-east-2"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-5"
    max_audio_mb: int = 25
    cors_origins: str = "http://localhost:5173,http://localhost:8000,http://127.0.0.1:8000"

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
    primary_address: Mapped[str | None] = mapped_column(String(255))
    secondary_address: Mapped[str | None] = mapped_column(String(255))
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
    primary_address: Mapped[str | None] = mapped_column(String(255))
    secondary_address: Mapped[str | None] = mapped_column(String(255))
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
    secondary_mobile: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    primary_address: Mapped[str | None] = mapped_column(String(255))
    secondary_address: Mapped[str | None] = mapped_column(String(255))
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
    disqualification_reason: Mapped[str | None] = mapped_column(String(255))
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
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("lead.id"), nullable=True)
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
    opportunity_type: Mapped[str | None] = mapped_column(String(100), default="New")
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
    lead: Mapped[Lead | None] = relationship(back_populates="opportunities")
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
    po_reason: Mapped[str | None] = mapped_column(String(255))
    value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str | None] = mapped_column(String(10), default="AED")
    start_date: Mapped[date | None] = mapped_column(Date)
    close_date: Mapped[date | None] = mapped_column(Date)
    technology: Mapped[str | None] = mapped_column(String(150))
    service: Mapped[str | None] = mapped_column(String(150))
    notes: Mapped[str | None] = mapped_column(Text)
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
    activity_outcome: Mapped[str | None] = mapped_column(String(50))
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
# Parsing, Sanitation & Normalization Helpers
# =====================================================================

def resolve_relative_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    if not isinstance(v, str):
        return None

    cleaned = v.strip()
    if not cleaned or cleaned.lower() in ("null", "none"):
        return None

    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass

    lower = cleaned.lower()
    today = date.today()

    if "tomorrow" in lower:
        return today + timedelta(days=1)
    if "today" in lower:
        return today

    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for idx, day in enumerate(days):
        if day in lower:
            current_day = today.weekday()
            days_ahead = (idx - current_day) % 7
            if days_ahead == 0 or "next" in lower:
                days_ahead += 7
            return today + timedelta(days=days_ahead)

    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    return None


def sanitize_numeric_deal_size(v) -> Decimal | None:
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


def deterministic_transcript_fallback(payload, transcript: str):
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

    if not payload.account.region:
        reg_match = re.search(r"\b(dubai|abu\s+dhabi|sharjah|ksa|riyadh|qatar|gcc)\b", transcript, re.IGNORECASE)
        if reg_match:
            payload.account.region = reg_match.group(1).title()
        elif re.search(r"\bthe\s+by\s+region\b", transcript, re.I):
            payload.account.region = "Dubai"

    if not payload.account.industry:
        ind_match = re.search(r"(?:under|in)\s+(?:a\s+)?([A-Za-z\s]+?)\s+industry", transcript, re.IGNORECASE)
        if ind_match:
            payload.account.industry = ind_match.group(1).strip().title()

    if payload.contact.contact_name and not payload.contact.designation:
        desig_match = re.search(
            r"(?:who\s+is\s+(?:the\s+)?|designation\s+(?:is\s+)?)([A-Za-z\s]+?)(?:\.|\bthe\s+scope\b|\bscope\b|\bwith\b|\band\b|$)",
            transcript,
            re.IGNORECASE,
        )
        if desig_match:
            payload.contact.designation = desig_match.group(1).strip()

    if not payload.lead.next_steps:
        next_step_match = re.search(r"next\s+step\s+is\s+([^.]+)", transcript, re.IGNORECASE)
        if next_step_match:
            extracted_step = next_step_match.group(1).strip()
            payload.lead.next_steps = extracted_step
            if payload.opportunity:
                payload.opportunity.next_steps = extracted_step


def evaluate_mandatory_fields(payload, transcript: str) -> tuple[list[str], str]:
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
            questions.append("Which enterprise company does this contact stakeholder belong to?")
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
            f"Portal Link: http://localhost:8000/page/voice?draft_id={draft_id}\n\n"
            f"— DataPhi CRM"
        )
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(settings().smtp_host, settings().smtp_port) as server:
            server.starttls()
            server.login(settings().smtp_user, settings().smtp_password)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to dispatch email alert: {e}")


# =====================================================================
# Pydantic Schemas for AI Entity Extraction
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
        return {} if (v is None or not isinstance(v, dict)) else v


class ConfirmedCommitPayload(BaseModel):
    draft_id: int | None = None
    intent: str = "create_lead"
    user_id: int | None = None
    account: ExtractedAccount = Field(default_factory=ExtractedAccount)
    subsidiary: ExtractedSubsidiary = Field(default_factory=ExtractedSubsidiary)
    contact: ExtractedContact = Field(default_factory=ExtractedContact)
    lead: ExtractedLead = Field(default_factory=ExtractedLead)
    opportunity: ExtractedOpportunity = Field(default_factory=ExtractedOpportunity)

    @field_validator("account", "subsidiary", "contact", "lead", "opportunity", mode="before")
    @classmethod
    def coerce_null_to_empty_dict(cls, v):
        return {} if (v is None or not isinstance(v, dict)) else v


# =====================================================================
# Manual Form Pydantic Schemas
# =====================================================================

class AccountFormIn(BaseModel):
    account_name: str
    account_manager: str | None = None
    region: str | None = None
    industry: str | None = None
    primary_address: str | None = None
    secondary_address: str | None = None


class SubsidiaryFormIn(BaseModel):
    account_id: int
    subsidiary_name: str
    region: str | None = None
    industry: str | None = None
    primary_address: str | None = None
    secondary_address: str | None = None


class ContactFormIn(BaseModel):
    account_id: int
    subsidiary_id: int | None = None
    contact_name: str
    designation: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    mobile: str | None = None
    secondary_mobile: str | None = None
    primary_address: str | None = None
    secondary_address: str | None = None
    notes: str | None = None


class LeadFormIn(BaseModel):
    account_id: int
    subsidiary_id: int | None = None
    contact_id: int
    lead_name: str
    account_manager: str | None = None
    deal_size: Decimal | None = None
    currency: str | None = "AED"
    stage: str | None = "Qualified"
    disqualification_reason: str | None = None
    type: str | None = "Warm"
    lead_source: str | None = None
    service: str | None = None
    technology: str | None = None
    next_steps: str | None = None
    next_action_date: date | None = None
    notes: str | None = None

    @field_validator("deal_size", mode="before")
    @classmethod
    def parse_deal(cls, v):
        return sanitize_numeric_deal_size(v)

    @field_validator("next_action_date", mode="before")
    @classmethod
    def parse_date(cls, v):
        return resolve_relative_date(v)


class OpportunityFormIn(BaseModel):
    opportunity_id: int | None = None
    lead_id: int | None = None
    account_id: int
    subsidiary_id: int | None = None
    contact_id: int
    opportunity_name: str
    deal_size: Decimal | None = None
    currency: str | None = "AED"
    project_type: str | None = "Fixed Cost"
    service: str | None = None
    stage: str
    probability: int = 60
    reason: str | None = None
    opportunity_type: str | None = "New"
    funded_by: str | None = "Client"
    opportunity_source: str | None = None
    next_steps: str | None = None
    next_action_date: date | None = None
    notes: str | None = None

    @field_validator("deal_size", mode="before")
    @classmethod
    def parse_deal(cls, v):
        return sanitize_numeric_deal_size(v)

    @field_validator("next_action_date", mode="before")
    @classmethod
    def parse_date(cls, v):
        return resolve_relative_date(v)


class ProjectFormIn(BaseModel):
    project_id: int | None = None
    opportunity_id: int
    account_id: int
    subsidiary_id: int | None = None
    contact_id: int
    project_name: str
    technology: str | None = None
    service: str | None = None
    value: Decimal | None = None
    currency: str | None = "AED"
    start_date: date | None = None
    close_date: date | None = None
    po_status: str | None = "Awaited"
    po_number: str | None = None
    po_reason: str | None = None
    notes: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def parse_val(cls, v):
        return sanitize_numeric_deal_size(v)

    @field_validator("start_date", "close_date", mode="before")
    @classmethod
    def parse_dates(cls, v):
        return resolve_relative_date(v)


class ActivityFormIn(BaseModel):
    activity_name: str
    record_type: str
    linked_record_id: int
    record_action: str
    activity_outcome: str | None = None
    activity_date: str | None = None
    next_step: str | None = None
    next_action_date: str | None = None
    notes: str | None = None


# =====================================================================
# AI System Prompt with Few-Shot Examples & Escape Hatch
# =====================================================================

SYSTEM_PROMPT = """### ROLE & CONTEXT
You are the DataPhi CRM Enterprise Intelligence & Entity Extraction Engine.
Your duty is to transform spoken sales notes, meeting transcripts, and partner calls into complete, valid JSON adhering to our multi-entity relational CRM structure.

### STRICT ESCAPE HATCH & EXTRACTION RULES:
1. MAXIMIZE CAPTURE ACCURACY:
   - "account manager is [Name]" -> account.account_manager
   - "operating in [Region]" or "located in [Region]" -> account.region
   - "industry is [Industry]" or "under [Industry]" -> account.industry
   - "[Name], who is [Title]" -> contact.contact_name, contact.designation
   - "next step is [Action]" -> lead.next_steps, opportunity.next_steps
2. NOTES CONSTRAINT: Keep "notes" null or under 6 words. NEVER copy, repeat, or echo the raw spoken transcript back into any notes field.
3. OPPORTUNITY TO LEAD BACKFILLING: When intent is "create_opportunity", always populate BOTH opportunity and lead objects with identical deal scope, and populate account metadata.
4. ZERO HALLUCINATION / ESCAPE HATCH: If a parameter is NOT spoken, return null. Never invent dummy data like 'John Doe', 'Company Name', or arbitrary numerical figures. Output valid JSON only, without Markdown prose.

### SCHEMA (JSON ONLY)
{
  "intent": "create_opportunity" | "create_lead" | "create_account" | "create_contact",
  "account": {"account_name": null, "region": null, "industry": null, "account_manager": null},
  "subsidiary": {"subsidiary_name": null},
  "contact": {"contact_name": null, "designation": null, "email": null, "mobile": null, "notes": null},
  "lead": {"lead_name": null, "deal_size": null, "currency": "AED", "type": "Hot", "service": null, "technology": null, "next_steps": null, "next_action_date": null, "closure_date": null, "notes": null},
  "opportunity": {"opportunity_name": null, "deal_size": null, "currency": "AED", "project_type": "T&M", "service": null, "stage": "Discovery (40%)", "probability": 40, "technology": null, "next_steps": null, "notes": null}
}

### FEW-SHOT EXAMPLES

#### Example 1: Direct Enterprise Opportunity (UAE Region)
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

#### Example 2: Incomplete Lead (Escape Hatch Demonstration)
Transcript: "Spoke with Budur at DAS yesterday regarding their migration. No budget finalized yet, but schedule demo next Wednesday."
Output:
{
  "intent": "create_lead",
  "account": {"account_name": "DAS", "region": null, "industry": null, "account_manager": null},
  "subsidiary": null,
  "contact": {"contact_name": "Budur", "designation": null, "email": null, "mobile": null, "notes": null},
  "lead": {"lead_name": "DAS - Cloud Migration", "deal_size": null, "currency": "AED", "type": "Warm", "service": "Cloud Migration", "technology": null, "next_steps": "Schedule demo", "next_action_date": null, "closure_date": null, "notes": null},
  "opportunity": null
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

    quotes = len(re.findall(r'(?<!\\)"', text))
    if quotes % 2 != 0:
        text += '"'

    text = re.sub(r',\s*$', '', text)
    text = re.sub(r',\s*"\w+":\s*"?$', '', text)

    open_braces = text.count("{")
    close_braces = text.count("}")
    if open_braces > close_braces:
        text += "}" * (open_braces - close_braces)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(cleaned)


# =====================================================================
# AWS Transcribe Streaming Implementation
# =====================================================================

class _TranscriptStreamAccumulator(TranscriptResultStreamHandler):
    def __init__(self, transcript_result_stream):
        super().__init__(transcript_result_stream)
        self.text_parts = []

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        for result in transcript_event.transcript.results:
            if not result.is_partial:
                for alt in result.alternatives:
                    self.text_parts.append(alt.transcript)


class AWSTranscribeService:
    def __init__(self, region: str):
        self.region = region
        self.client = TranscribeStreamingClient(region=region)

    async def transcribe_pcm_bytes(self, pcm_bytes: bytes) -> str:
        stream = await self.client.start_stream_transcription(
            language_code="en-US",
            media_sample_rate_hz=16000,
            media_encoding="pcm",
        )
        handler = _TranscriptStreamAccumulator(stream.output_stream)

        async def write_chunks():
            chunk_size = 1024 * 8
            for i in range(0, len(pcm_bytes), chunk_size):
                await stream.input_stream.send_audio_event(audio_chunk=pcm_bytes[i:i + chunk_size])
            await stream.input_stream.end_stream()

        await asyncio.gather(write_chunks(), handler.handle_events())
        transcript = " ".join(handler.text_parts).strip()
        if not transcript:
            raise ValueError("Amazon Transcribe completed, but detected no speech in the recording.")
        return transcript


# =====================================================================
# AWS Bedrock Claude Sonnet 5 Implementation
# =====================================================================

class AWSBedrockService:
    def __init__(self, region: str, model_id: str):
        self.region = region
        self.model_id = model_id
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def extract(self, transcript: str):
        response = self.client.converse(
            modelId=self.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": f"Reference date: {date.today().isoformat()}\nTranscript: {transcript}\nOutput JSON strictly according to instructions:"
                        }
                    ],
                }
            ],
            system=[{"text": SYSTEM_PROMPT}],
            inferenceConfig={"maxTokens": 4096},  # Increased token headroom for longer clips
        )

        # Safely extract text across all content blocks (handles reasoningContent blocks)
        content_blocks = response.get("output", {}).get("message", {}).get("content", [])
        raw_text = ""
        for block in content_blocks:
            if isinstance(block, dict) and "text" in block:
                raw_text += block["text"]

        # Fallback: if no direct text block was returned, check reasoningContent
        if not raw_text:
            for block in content_blocks:
                if isinstance(block, dict) and "reasoningContent" in block:
                    raw_text += block.get("reasoningContent", {}).get("reasoningText", {}).get("text", "")

        if not raw_text:
            stop_reason = response.get("stopReason", "unknown")
            raise ValueError(f"Bedrock returned no text content (stopReason: {stop_reason}).")

        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        parsed_data = parse_json(raw_text)
        payload = FullLifecycleVoicePayload.model_validate(parsed_data)
        return payload, input_tokens, output_tokens

# =====================================================================
# Telemetry Logging to Daily JSON
# =====================================================================

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
TEMPLATES_DIR = ROOT_DIR / "templates"
STATIC_DIR = BACKEND_DIR / "static"
LOGS_DIR = BACKEND_DIR / "logs"

def log_telemetry_entry(entry: dict):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"crm_telemetry_{today_str}.json"

    logs = []
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
                if not isinstance(logs, list):
                    logs = [logs]
        except Exception:
            logs = []

    logs.append(entry)
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, default=str)


def convert_to_pcm_16k(input_path: str) -> tuple[bytes, float]:
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_bin = "ffmpeg"

    out_path = input_path + ".pcm"
    cmd = [
        ffmpeg_bin, "-y", "-i", input_path,
        "-f", "s16le", "-ac", "1", "-ar", "16000",
        out_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    with open(out_path, "rb") as f:
        pcm_bytes = f.read()

    try:
        os.remove(out_path)
    except FileNotFoundError:
        pass

    duration_sec = len(pcm_bytes) / 32000.0
    return pcm_bytes, duration_sec


def execute_full_hierarchy_commit(db: Session, payload: ConfirmedCommitPayload) -> dict:
    user_id = payload.user_id

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

    if payload.intent == "create_contact":
        activity = Activity(
            activity_name=f"Contact Created: {contact.contact_name}",
            account_id=account.id,
            subsidiary_id=subsidiary.id if subsidiary else None,
            contact_id=contact.id,
            record_type="Contact",
            record_action="System Action",
            account_name=account.account_name,
            contact_name=contact.contact_name,
            notes=f"Contact added to {account.account_name}. Role: {contact.designation or 'N/A'}",
            created_by=user_id,
        )
        db.add(activity)
        db.commit()

        return {
            "entity_type": "Contact",
            "account_id": account.id,
            "account_name": account.account_name,
            "contact_id": contact.id,
            "contact_name": contact.contact_name,
            "designation": contact.designation,
            "email": contact.email,
            "mobile": contact.mobile,
            "activity_id": activity.id,
        }

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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =====================================================================
# FastAPI Application & Startup Initialization
# =====================================================================

app = FastAPI(title="DataPhi CRM Voice Engine (AWS Sonnet 5)", version="8.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

PAGE_MAPPING = {
    "account": TEMPLATES_DIR / "DataPhi_Page_01_New_Account_updated.html",
    "subsidiary": TEMPLATES_DIR / "DataPhi_Page_02_New_Subsidiary 1.html",
    "contact": TEMPLATES_DIR / "DataPhi_Page_03_New_Contact 1.html",
    "lead": TEMPLATES_DIR / "DataPhi_Page_04_Create_Lead 1.html",
    "opportunity": TEMPLATES_DIR / "DataPhi_Page_05_Opportunity_Details_updated.html",
    "project": TEMPLATES_DIR / "DataPhi_Page_06_Project_Details 1.html",
    "activity": TEMPLATES_DIR / "DataPhi_Page_07_Log_Activity.html",
    "voice": TEMPLATES_DIR / "DataPhi_Page_Voice_Station.html",
}

_transcribe_service = None
_bedrock_service = None


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    global _transcribe_service, _bedrock_service
    _transcribe_service = AWSTranscribeService(region=settings().aws_region)
    _bedrock_service = AWSBedrockService(region=settings().aws_region, model_id=settings().bedrock_model_id)


# =====================================================================
# HTML Navigation Endpoints with Auto-Fallback
# =====================================================================

@app.get("/")
def home():
    file_path = PAGE_MAPPING["voice"]
    if file_path.exists():
        return FileResponse(str(file_path))
    return FileResponse(str(PAGE_MAPPING["account"]))


@app.get("/page/{name}")
def get_page(name: str):
    file_path = PAGE_MAPPING.get(name)
    if file_path and file_path.exists():
        return FileResponse(str(file_path))

    if name == "voice":
        return HTMLResponse(content=EMBEDDED_VOICE_STATION_HTML)

    raise HTTPException(status_code=404, detail=f"Page template '{name}' not found at {file_path}")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider": "AWS Managed",
        "transcribe_region": settings().aws_region,
        "bedrock_model": settings().bedrock_model_id,
        "timestamp": datetime.now().isoformat(),
    }


# =====================================================================
# Accounts Overview / Registry Query Endpoint
# =====================================================================

@app.get("/api/accounts/overview")
def get_accounts_overview(db: Session = Depends(get_db)):
    accounts = db.scalars(
        select(Account)
        .options(selectinload(Account.subsidiaries), selectinload(Account.contacts))
        .order_by(Account.id.desc())
    ).all()

    result = []
    for acc in accounts:
        result.append({
            "id": acc.id,
            "account_name": acc.account_name,
            "account_manager": acc.account_manager or "Unassigned",
            "region": acc.region or "N/A",
            "industry": acc.industry or "N/A",
            "primary_address": acc.primary_address or "N/A",
            "secondary_address": acc.secondary_address or "",
            "creation_date": acc.creation_date.strftime("%Y-%m-%d %H:%M") if acc.creation_date else "N/A",
            "subsidiaries": [
                {
                    "id": sub.id,
                    "subsidiary_name": sub.subsidiary_name,
                    "region": sub.region or "N/A",
                    "industry": sub.industry or "N/A",
                    "primary_address": sub.primary_address or "N/A",
                }
                for sub in acc.subsidiaries
            ],
            "contacts": [
                {
                    "id": con.id,
                    "contact_name": con.contact_name,
                    "designation": con.designation or "N/A",
                    "email": con.email or "N/A",
                    "mobile": con.mobile or "N/A",
                }
                for con in acc.contacts
            ],
        })
    return result


# =====================================================================
# Cross-Entity Lookup Endpoint (feeds all dropdowns + FK cross-refs)
# =====================================================================

@app.get("/api/lookups")
def get_lookups(db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).order_by(Account.account_name)).all()
    subsidiaries = db.scalars(select(Subsidiary).order_by(Subsidiary.subsidiary_name)).all()
    contacts = db.scalars(select(Contact).order_by(Contact.contact_name)).all()
    leads = db.scalars(select(Lead).order_by(Lead.id.desc())).all()
    opportunities = db.scalars(select(Opportunity).order_by(Opportunity.id.desc())).all()
    projects = db.scalars(select(Project).order_by(Project.id.desc())).all()

    acc_name = {a.id: a.account_name for a in accounts}
    sub_name = {s.id: s.subsidiary_name for s in subsidiaries}
    con_name = {c.id: c.contact_name for c in contacts}

    return {
        "accounts": [
            {"id": a.id, "account_name": a.account_name, "account_manager": a.account_manager,
             "region": a.region, "industry": a.industry}
            for a in accounts
        ],
        "subsidiaries": [
            {"id": s.id, "subsidiary_name": s.subsidiary_name, "account_id": s.account_id,
             "account_name": acc_name.get(s.account_id), "region": s.region, "industry": s.industry}
            for s in subsidiaries
        ],
        "contacts": [
            {"id": c.id, "contact_name": c.contact_name, "account_id": c.account_id,
             "account_name": acc_name.get(c.account_id), "subsidiary_id": c.subsidiary_id,
             "subsidiary_name": sub_name.get(c.subsidiary_id), "designation": c.designation,
             "email": c.email, "mobile": c.mobile}
            for c in contacts
        ],
        "leads": [
            {"id": l.id, "lead_name": l.lead_name, "account_id": l.account_id,
             "account_name": acc_name.get(l.account_id), "contact_id": l.contact_id,
             "contact_name": con_name.get(l.contact_id), "stage": l.stage, "type": l.type,
             "deal_size": float(l.deal_size) if l.deal_size else None, "currency": l.currency,
             "service": l.service}
            for l in leads
        ],
        "opportunities": [
            {"id": o.id, "opportunity_name": o.opportunity_name, "account_id": o.account_id,
             "account_name": acc_name.get(o.account_id), "contact_id": o.contact_id,
             "contact_name": con_name.get(o.contact_id), "lead_id": o.lead_id, "stage": o.stage,
             "deal_size": float(o.deal_size) if o.deal_size else None, "currency": o.currency}
            for o in opportunities
        ],
        "projects": [
            {"id": p.id, "project_name": p.project_name, "opportunity_id": p.opportunity_id,
             "account_id": p.account_id, "account_name": acc_name.get(p.account_id),
             "contact_id": p.contact_id, "stage": p.stage}
            for p in projects
        ],
    }


# =====================================================================
# Registry Overview Endpoints (feed the hierarchical registry tables)
# =====================================================================

@app.get("/api/subsidiaries/overview")
def get_subsidiaries_overview(db: Session = Depends(get_db)):
    subs = db.scalars(
        select(Subsidiary)
        .options(selectinload(Subsidiary.account), selectinload(Subsidiary.contacts))
        .order_by(Subsidiary.id.desc())
    ).all()

    return [
        {
            "id": s.id,
            "subsidiary_name": s.subsidiary_name,
            "region": s.region or "N/A",
            "industry": s.industry or "N/A",
            "primary_address": s.primary_address or "N/A",
            "account_id": s.account_id,
            "account_name": s.account.account_name if s.account else "N/A",
            "account_manager": s.account.account_manager if s.account else "N/A",
            "creation_date": s.creation_date.strftime("%Y-%m-%d %H:%M") if s.creation_date else "N/A",
            "contacts": [
                {
                    "id": c.id, "contact_name": c.contact_name,
                    "designation": c.designation or "N/A",
                    "email": c.email or "N/A", "mobile": c.mobile or "N/A",
                }
                for c in s.contacts
            ],
        }
        for s in subs
    ]


@app.get("/api/contacts/overview")
def get_contacts_overview(db: Session = Depends(get_db)):
    contacts = db.scalars(
        select(Contact)
        .options(
            selectinload(Contact.account), selectinload(Contact.subsidiary),
            selectinload(Contact.leads), selectinload(Contact.opportunities),
        )
        .order_by(Contact.id.desc())
    ).all()

    return [
        {
            "id": c.id,
            "contact_name": c.contact_name,
            "designation": c.designation or "N/A",
            "email": c.email or "N/A",
            "mobile": c.mobile or "N/A",
            "linkedin_url": c.linkedin_url or "N/A",
            "account_id": c.account_id,
            "account_name": c.account.account_name if c.account else "N/A",
            "subsidiary_id": c.subsidiary_id,
            "subsidiary_name": c.subsidiary.subsidiary_name if c.subsidiary else None,
            "creation_date": c.creation_date.strftime("%Y-%m-%d %H:%M") if c.creation_date else "N/A",
            "leads": [
                {"id": ld.id, "lead_name": ld.lead_name, "stage": ld.stage,
                 "deal_size": float(ld.deal_size) if ld.deal_size else 0, "currency": ld.currency}
                for ld in c.leads
            ],
            "opportunities": [
                {"id": op.id, "opportunity_name": op.opportunity_name, "stage": op.stage,
                 "deal_size": float(op.deal_size) if op.deal_size else 0, "currency": op.currency}
                for op in c.opportunities
            ],
        }
        for c in contacts
    ]


@app.get("/api/leads/overview")
def get_leads_overview(db: Session = Depends(get_db)):
    leads = db.scalars(
        select(Lead)
        .options(selectinload(Lead.account), selectinload(Lead.contact), selectinload(Lead.opportunities))
        .order_by(Lead.id.desc())
    ).all()

    lead_ids = [l.id for l in leads]
    activities = []
    if lead_ids:
        activities = db.scalars(select(Activity).where(Activity.lead_id.in_(lead_ids)).order_by(Activity.id.desc())).all()
    acts_by_lead: dict[int, list] = {}
    for a in activities:
        acts_by_lead.setdefault(a.lead_id, []).append(a)

    return [
        {
            "id": l.id,
            "lead_name": l.lead_name,
            "account_id": l.account_id,
            "account_name": l.account.account_name if l.account else "N/A",
            "contact_id": l.contact_id,
            "contact_name": l.contact.contact_name if l.contact else "N/A",
            "account_manager": l.account_manager or "N/A",
            "deal_size": float(l.deal_size) if l.deal_size else 0,
            "currency": l.currency or "AED",
            "stage": l.stage or "N/A",
            "type": l.type or "N/A",
            "service": l.service or "N/A",
            "technology": l.technology or "N/A",
            "next_steps": l.next_steps or "N/A",
            "next_action_date": l.next_action_date.isoformat() if l.next_action_date else None,
            "creation_date": l.creation_date.strftime("%Y-%m-%d %H:%M") if l.creation_date else "N/A",
            "opportunities": [
                {"id": o.id, "opportunity_name": o.opportunity_name, "stage": o.stage}
                for o in l.opportunities
            ],
            "activities": [
                {"id": a.id, "activity_name": a.activity_name, "record_action": a.record_action,
                 "activity_date": a.activity_date.strftime("%Y-%m-%d %H:%M") if a.activity_date else "N/A"}
                for a in acts_by_lead.get(l.id, [])
            ],
        }
        for l in leads
    ]


@app.get("/api/opportunities/overview")
def get_opportunities_overview(db: Session = Depends(get_db)):
    opps = db.scalars(
        select(Opportunity)
        .options(selectinload(Opportunity.account), selectinload(Opportunity.contact), selectinload(Opportunity.lead))
        .order_by(Opportunity.id.desc())
    ).all()

    opp_ids = [o.id for o in opps]
    activities = []
    if opp_ids:
        activities = db.scalars(select(Activity).where(Activity.opportunity_id.in_(opp_ids)).order_by(Activity.id.desc())).all()
    acts_by_opp: dict[int, list] = {}
    for a in activities:
        acts_by_opp.setdefault(a.opportunity_id, []).append(a)

    return [
        {
            "id": o.id,
            "opportunity_name": o.opportunity_name,
            "account_id": o.account_id,
            "account_name": o.account.account_name if o.account else "N/A",
            "contact_id": o.contact_id,
            "contact_name": o.contact.contact_name if o.contact else "N/A",
            "lead_id": o.lead_id,
            "lead_name": o.lead.lead_name if o.lead else None,
            "account_manager": o.account_manager or "N/A",
            "deal_size": float(o.deal_size) if o.deal_size else 0,
            "currency": o.currency or "AED",
            "stage": o.stage or "N/A",
            "probability": o.probability or 0,
            "service": o.service or "N/A",
            "technology": o.technology or "N/A",
            "next_steps": o.next_steps or "N/A",
            "next_action_date": o.next_action_date.isoformat() if o.next_action_date else None,
            "creation_date": o.creation_date.strftime("%Y-%m-%d %H:%M") if o.creation_date else "N/A",
            "activities": [
                {"id": a.id, "activity_name": a.activity_name, "record_action": a.record_action,
                 "activity_date": a.activity_date.strftime("%Y-%m-%d %H:%M") if a.activity_date else "N/A"}
                for a in acts_by_opp.get(o.id, [])
            ],
        }
        for o in opps
    ]


@app.get("/api/projects/overview")
def get_projects_overview(db: Session = Depends(get_db)):
    projects = db.scalars(
        select(Project)
        .options(selectinload(Project.account), selectinload(Project.opportunity))
        .order_by(Project.id.desc())
    ).all()

    return [
        {
            "id": p.id,
            "project_name": p.project_name,
            "opportunity_id": p.opportunity_id,
            "opportunity_name": p.opportunity.opportunity_name if p.opportunity else "N/A",
            "account_id": p.account_id,
            "account_name": p.account.account_name if p.account else "N/A",
            "contact_id": p.contact_id,
            "stage": p.stage or "N/A",
            "po_number": p.po_number or "N/A",
            "value": float(p.value) if p.value else 0,
            "currency": p.currency or "AED",
            "start_date": p.start_date.isoformat() if p.start_date else None,
            "close_date": p.close_date.isoformat() if p.close_date else None,
            "technology": p.technology or "N/A",
            "service": p.service or "N/A",
            "creation_date": p.creation_date.strftime("%Y-%m-%d %H:%M") if p.creation_date else "N/A",
        }
        for p in projects
    ]


@app.get("/api/activities/overview")
def get_activities_overview(db: Session = Depends(get_db)):
    activities = db.scalars(select(Activity).order_by(Activity.id.desc()).limit(200)).all()

    lead_ids = {a.lead_id for a in activities if a.lead_id}
    opp_ids = {a.opportunity_id for a in activities if a.opportunity_id}
    proj_ids = {a.project_id for a in activities if a.project_id}

    lead_name = {}
    if lead_ids:
        for l in db.scalars(select(Lead).where(Lead.id.in_(lead_ids))).all():
            lead_name[l.id] = l.lead_name
    opp_name = {}
    if opp_ids:
        for o in db.scalars(select(Opportunity).where(Opportunity.id.in_(opp_ids))).all():
            opp_name[o.id] = o.opportunity_name
    proj_name = {}
    if proj_ids:
        for p in db.scalars(select(Project).where(Project.id.in_(proj_ids))).all():
            proj_name[p.id] = p.project_name

    def linked_label(a: Activity) -> str:
        rtype = (a.record_type or "").lower()
        if rtype == "account":
            return a.account_name or "N/A"
        if rtype == "subsidiary":
            return a.subsidiary_name or "N/A"
        if rtype == "contact":
            return a.contact_name or "N/A"
        if rtype == "lead":
            return lead_name.get(a.lead_id, "N/A")
        if rtype == "opportunity":
            return opp_name.get(a.opportunity_id, "N/A")
        if rtype == "project":
            return proj_name.get(a.project_id, "N/A")
        return "N/A"

    def linked_id(a: Activity):
        rtype = (a.record_type or "").lower()
        return {
            "account": a.account_id, "subsidiary": a.subsidiary_id, "contact": a.contact_id,
            "lead": a.lead_id, "opportunity": a.opportunity_id, "project": a.project_id,
        }.get(rtype)

    return [
        {
            "id": a.id,
            "activity_name": a.activity_name,
            "record_type": a.record_type or "N/A",
            "linked_record_id": linked_id(a),
            "linked_label": linked_label(a),
            "record_action": a.record_action or "N/A",
            "activity_outcome": a.activity_outcome or "N/A",
            "activity_date": a.activity_date.strftime("%Y-%m-%d %H:%M") if a.activity_date else "N/A",
            "next_step": a.next_step or "N/A",
            "next_action_date": a.next_action_date.isoformat() if a.next_action_date else None,
            "notes": a.notes or "",
        }
        for a in activities
    ]


# =====================================================================
# Manual Form Save Endpoints (Direct POST from HTML Pages)
# =====================================================================

@app.post("/api/account/save")
def save_account_form(data: AccountFormIn, db: Session = Depends(get_db)):
    account = Account(
        account_name=data.account_name,
        account_manager=data.account_manager,
        region=data.region,
        industry=data.industry,
        primary_address=data.primary_address,
        secondary_address=data.secondary_address,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"status": "success", "account_id": account.id}


@app.post("/api/subsidiary/save")
def save_subsidiary_form(data: SubsidiaryFormIn, db: Session = Depends(get_db)):
    sub = Subsidiary(
        account_id=data.account_id,
        subsidiary_name=data.subsidiary_name,
        region=data.region,
        industry=data.industry,
        primary_address=data.primary_address,
        secondary_address=data.secondary_address,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"status": "success", "subsidiary_id": sub.id}


@app.post("/api/contact/save")
def save_contact_form(data: ContactFormIn, db: Session = Depends(get_db)):
    contact = Contact(
        account_id=data.account_id,
        subsidiary_id=data.subsidiary_id,
        contact_name=data.contact_name,
        designation=data.designation,
        email=data.email,
        mobile=data.mobile,
        secondary_mobile=data.secondary_mobile,
        linkedin_url=data.linkedin_url,
        primary_address=data.primary_address,
        secondary_address=data.secondary_address,
        notes=data.notes,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return {"status": "success", "contact_id": contact.id}


@app.post("/api/lead/save")
def save_lead_form(data: LeadFormIn, db: Session = Depends(get_db)):
    lead = Lead(
        account_id=data.account_id,
        subsidiary_id=data.subsidiary_id,
        contact_id=data.contact_id,
        lead_name=data.lead_name,
        account_manager=data.account_manager,
        deal_size=data.deal_size,
        currency=data.currency,
        stage=data.stage,
        disqualification_reason=data.disqualification_reason,
        type=data.type,
        lead_source=data.lead_source,
        service=data.service,
        technology=data.technology,
        next_steps=data.next_steps,
        next_action_date=data.next_action_date,
        notes=data.notes,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return {"status": "success", "lead_id": lead.id}


@app.post("/api/opportunity/save")
def save_opportunity_form(data: OpportunityFormIn, db: Session = Depends(get_db)):
    opp = None
    if data.opportunity_id:
        opp = db.scalar(select(Opportunity).where(Opportunity.id == data.opportunity_id))

    if not opp:
        opp = Opportunity(
            opportunity_name=data.opportunity_name,
            lead_id=data.lead_id,
            account_id=data.account_id,
            subsidiary_id=data.subsidiary_id,
            contact_id=data.contact_id,
            deal_size=data.deal_size,
            currency=data.currency,
            project_type=data.project_type,
            service=data.service,
            stage=data.stage,
            probability=data.probability,
            reason=data.reason,
            opportunity_type=data.opportunity_type,
            funded_by=data.funded_by,
            opportunity_source=data.opportunity_source,
            next_steps=data.next_steps,
            next_action_date=data.next_action_date,
            notes=data.notes,
        )
        db.add(opp)
    else:
        opp.opportunity_name = data.opportunity_name
        opp.deal_size = data.deal_size
        opp.currency = data.currency
        opp.project_type = data.project_type
        opp.service = data.service
        opp.stage = data.stage
        opp.probability = data.probability
        opp.reason = data.reason
        opp.opportunity_type = data.opportunity_type
        opp.funded_by = data.funded_by
        opp.opportunity_source = data.opportunity_source
        opp.next_steps = data.next_steps
        opp.next_action_date = data.next_action_date
        opp.notes = data.notes

    db.flush()

    project_id = None
    if "Closed Won" in opp.stage:
        existing_proj = db.scalar(select(Project).where(Project.opportunity_id == opp.id))
        if not existing_proj:
            proj = Project(
                opportunity_id=opp.id,
                account_id=opp.account_id,
                subsidiary_id=opp.subsidiary_id,
                contact_id=opp.contact_id,
                project_name=f"{opp.opportunity_name} — Delivery",
                technology=opp.technology,
                service=opp.service,
                value=opp.deal_size,
                currency=opp.currency,
                stage="PO Awaited",
            )
            db.add(proj)
            db.flush()
            project_id = proj.id
        else:
            project_id = existing_proj.id

    db.commit()
    return {"status": "success", "opportunity_id": opp.id, "project_id": project_id}


@app.post("/api/project/save")
def save_project_form(data: ProjectFormIn, db: Session = Depends(get_db)):
    proj = None
    if data.project_id:
        proj = db.scalar(select(Project).where(Project.id == data.project_id))

    if not proj:
        proj = Project(
            opportunity_id=data.opportunity_id,
            account_id=data.account_id,
            subsidiary_id=data.subsidiary_id,
            contact_id=data.contact_id,
            project_name=data.project_name,
            technology=data.technology,
            service=data.service,
            value=data.value,
            currency=data.currency,
            start_date=data.start_date,
            close_date=data.close_date,
            stage=data.po_status or "PO Awaited",
            po_number=data.po_number,
            po_reason=data.po_reason,
            notes=data.notes,
        )
        db.add(proj)
    else:
        proj.project_name = data.project_name
        proj.technology = data.technology
        proj.service = data.service
        proj.value = data.value
        proj.currency = data.currency
        proj.start_date = data.start_date
        proj.close_date = data.close_date
        proj.stage = data.po_status or "PO Awaited"
        proj.po_number = data.po_number
        proj.po_reason = data.po_reason
        proj.notes = data.notes

    db.commit()
    db.refresh(proj)
    return {"status": "success", "project_id": proj.id}


@app.post("/api/activity/save")
def save_activity_form(data: ActivityFormIn, db: Session = Depends(get_db)):
    act = Activity(
        activity_name=data.activity_name,
        record_type=data.record_type,
        record_action=data.record_action,
        activity_outcome=data.activity_outcome,
        notes=data.notes,
        next_step=data.next_step,
    )

    rtype = data.record_type.lower()
    if rtype == "account":
        act.account_id = data.linked_record_id
    elif rtype == "subsidiary":
        act.subsidiary_id = data.linked_record_id
    elif rtype == "contact":
        act.contact_id = data.linked_record_id
    elif rtype == "lead":
        act.lead_id = data.linked_record_id
    elif rtype == "opportunity":
        act.opportunity_id = data.linked_record_id
    elif rtype == "project":
        act.project_id = data.linked_record_id

    db.add(act)
    db.commit()
    db.refresh(act)
    return {"status": "success", "activity_id": act.id}


# =====================================================================
# Telemetry Retrieval Endpoint
# =====================================================================

@app.get("/api/voice/telemetry")
def get_telemetry(date_str: str | None = Query(None, alias="date")):
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"crm_telemetry_{target_date}.json"

    if not log_file.exists():
        return {
            "date": target_date,
            "total_calls": 0,
            "total_cost_usd": 0.0,
            "total_tokens": 0,
            "sessions": [],
        }

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            sessions = json.load(f)
            if not isinstance(sessions, list):
                sessions = [sessions]
    except Exception:
        sessions = []

    total_cost = sum(s.get("telemetry", {}).get("costs_usd", {}).get("total_cost", 0.0) for s in sessions)
    total_tokens = sum(s.get("telemetry", {}).get("tokens", {}).get("total_tokens", 0) for s in sessions)

    return {
        "date": target_date,
        "total_calls": len(sessions),
        "total_cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
        "sessions": sessions[-25:],
    }


@app.get("/api/voice/drafts")
def get_voice_drafts(db: Session = Depends(get_db)):
    drafts = db.scalars(
        select(VoiceDraft).order_by(VoiceDraft.id.desc()).limit(30)
    ).all()

    res = []
    for d in drafts:
        raw_ext = {}
        if d.extracted_json:
            try:
                raw_ext = json.loads(d.extracted_json)
            except Exception:
                raw_ext = {}
        if not isinstance(raw_ext, dict):
            raw_ext = {}
        for key in ["account", "subsidiary", "contact", "lead", "opportunity"]:
            if key not in raw_ext or not isinstance(raw_ext.get(key), dict):
                raw_ext[key] = {}

        res.append({
            "id": d.id,
            "target_entity": d.target_entity,
            "raw_transcript": d.raw_transcript,
            "extracted_data": raw_ext,
            "missing_fields": d.missing_fields or [],
            "clarification_prompt": d.clarification_prompt,
            "status": d.status,
            "creation_date": d.creation_date.strftime("%Y-%m-%d %H:%M") if d.creation_date else "N/A",
        })
    return res


# =====================================================================
# Voice Ingestion Endpoint (AWS Transcribe + Bedrock Sonnet 5)
# =====================================================================

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
        raise HTTPException(400, "Empty audio recording buffer.")
    if len(data) > settings().max_audio_mb * 1024 * 1024:
        raise HTTPException(413, f"Audio file exceeds {settings().max_audio_mb} MB limit.")

    fd, path = tempfile.mkstemp(prefix="aws_voice_", suffix=os.path.splitext(audio.filename or ".webm")[1])
    os.close(fd)
    start_total = time.perf_counter()

    try:
        with open(path, "wb") as f:
            f.write(data)

        pcm_bytes, duration_sec = convert_to_pcm_16k(path)

        start_transcribe = time.perf_counter()
        transcript = await _transcribe_service.transcribe_pcm_bytes(pcm_bytes)
        transcribe_ms = int((time.perf_counter() - start_transcribe) * 1000)

        start_bedrock = time.perf_counter()
        payload, input_tokens, output_tokens = _bedrock_service.extract(transcript)
        bedrock_ms = int((time.perf_counter() - start_bedrock) * 1000)
        total_ms = int((time.perf_counter() - start_total) * 1000)

        deterministic_transcript_fallback(payload, transcript)
        missing_fields, clarification_prompt = evaluate_mandatory_fields(payload, transcript)

        # AWS Transcribe: $0.024/minute, 15-second minimum billing increment
        billable_seconds = max(15.0, duration_sec)
        transcribe_cost = (billable_seconds / 60.0) * 0.024

        # AWS Bedrock Claude Sonnet 5: $2.00/1M input tokens, $10.00/1M output tokens
        bedrock_input_cost = (input_tokens / 1_000_000.0) * 2.0
        bedrock_output_cost = (output_tokens / 1_000_000.0) * 10.0
        bedrock_cost = bedrock_input_cost + bedrock_output_cost
        total_cost = transcribe_cost + bedrock_cost

        extracted_dict = payload.model_dump(mode="json")
        for key in ["account", "subsidiary", "contact", "lead", "opportunity"]:
            if key not in extracted_dict or not isinstance(extracted_dict.get(key), dict):
                extracted_dict[key] = {}

        draft = VoiceDraft(
            user_id=user_id,
            user_email=user_email,
            user_phone=user_phone,
            raw_transcript=transcript,
            target_entity=payload.intent,
            extracted_json=json.dumps(extracted_dict),
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

        telemetry = {
            "tokens": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            "costs_usd": {
                "transcribe_cost": round(transcribe_cost, 6),
                "bedrock_cost": round(bedrock_cost, 6),
                "total_cost": round(total_cost, 6),
                "details": {
                    "audio_duration_sec": round(duration_sec, 2),
                    "billable_seconds": round(billable_seconds, 2),
                },
            },
            "latency": {
                "transcribe_ms": transcribe_ms,
                "bedrock_ms": bedrock_ms,
                "backend_total_ms": total_ms,
            },
        }

        log_telemetry_entry({
            "timestamp": datetime.now().isoformat(),
            "draft_id": draft.id,
            "intent": payload.intent,
            "transcript": transcript,
            "missing_fields": missing_fields,
            "telemetry": telemetry,
        })

        return {
            "status": "draft_saved_incomplete" if missing_fields else "draft_ready_for_review",
            "draft_id": draft.id,
            "intent": payload.intent,
            "transcript": transcript,
            "extracted_data": extracted_dict,
            "missing_fields": missing_fields,
            "clarification_prompt": clarification_prompt,
            "telemetry": telemetry,
        }

    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"Voice Ingestion Pipeline Error: {exc}") from exc
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

    started = time.perf_counter()
    supplementary_text = ""
    transcribe_ms = 0
    duration_sec = 0.0

    if additional_audio:
        fd, path = tempfile.mkstemp(prefix="crm_draft_patch_", suffix=".webm")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(await additional_audio.read())
            pcm_bytes, duration_sec = convert_to_pcm_16k(path)
            t_t0 = time.perf_counter()
            supplementary_text = await _transcribe_service.transcribe_pcm_bytes(pcm_bytes)
            transcribe_ms = int((time.perf_counter() - t_t0) * 1000)
        finally:
            if os.path.exists(path):
                os.remove(path)
    elif additional_text:
        supplementary_text = additional_text.strip()
    else:
        raise HTTPException(400, "Provide supplementary audio or text input.")

    merged_transcript = f"{draft.raw_transcript}. Follow-up details: {supplementary_text}"
    t_bed0 = time.perf_counter()
    updated_payload, input_tokens, output_tokens = _bedrock_service.extract(merged_transcript)
    bedrock_ms = int((time.perf_counter() - t_bed0) * 1000)

    deterministic_transcript_fallback(updated_payload, merged_transcript)
    missing_fields, clarification_prompt = evaluate_mandatory_fields(updated_payload, merged_transcript)
    total_backend_ms = int((time.perf_counter() - started) * 1000)

    billable_seconds = max(15.0, duration_sec) if duration_sec > 0 else 0.0
    transcribe_cost = (billable_seconds / 60.0) * 0.024 if billable_seconds > 0 else 0.0

    bedrock_cost = ((input_tokens / 1_000_000.0) * 2.0) + ((output_tokens / 1_000_000.0) * 10.0)
    total_cost = transcribe_cost + bedrock_cost

    extracted_dict = updated_payload.model_dump(mode="json")
    for key in ["account", "subsidiary", "contact", "lead", "opportunity"]:
        if key not in extracted_dict or not isinstance(extracted_dict.get(key), dict):
            extracted_dict[key] = {}

    draft.raw_transcript = merged_transcript
    draft.extracted_json = json.dumps(extracted_dict)
    draft.missing_fields = missing_fields
    draft.clarification_prompt = clarification_prompt
    draft.status = "INCOMPLETE" if missing_fields else "STAGED_READY"
    db.commit()

    telemetry = {
        "tokens": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "costs_usd": {
            "transcribe_cost": round(transcribe_cost, 6),
            "bedrock_cost": round(bedrock_cost, 6),
            "total_cost": round(total_cost, 6),
        },
        "latency": {
            "transcribe_ms": transcribe_ms,
            "bedrock_ms": bedrock_ms,
            "backend_total_ms": total_backend_ms,
        },
    }

    log_telemetry_entry({
        "timestamp": datetime.now().isoformat(),
        "draft_id": draft.id,
        "action": "resumed_draft",
        "transcript": merged_transcript,
        "missing_fields": missing_fields,
        "telemetry": telemetry,
    })

    return {
        "status": "draft_saved_incomplete" if missing_fields else "draft_ready_for_review",
        "draft_id": draft.id,
        "intent": updated_payload.intent,
        "transcript": merged_transcript,
        "extracted_data": extracted_dict,
        "missing_fields": missing_fields,
        "clarification_prompt": clarification_prompt,
        "telemetry": telemetry,
    }


@app.post("/api/voice/commit")
async def commit_voice_records(payload: ConfirmedCommitPayload, db: Session = Depends(get_db)):
    started = time.perf_counter()

    if payload.intent in ["create_lead", "create_opportunity"]:
        if not payload.account.account_name:
            raise HTTPException(422, "Cannot commit without enterprise Account Name.")
        if not payload.contact.contact_name:
            raise HTTPException(422, "Cannot commit without Contact Stakeholder Name.")
    elif payload.intent == "create_account":
        if not payload.account.account_name:
            raise HTTPException(422, "Cannot commit without Account Name.")
    elif payload.intent == "create_contact":
        if not payload.account.account_name:
            raise HTTPException(422, "Cannot commit contact without associated Account Name.")
        if not payload.contact.contact_name:
            raise HTTPException(422, "Cannot commit without Contact Name.")

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
            "commit_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"Database transaction failed: {exc}") from exc


# =====================================================================
# Embedded Voice Station Interface
# =====================================================================

EMBEDDED_VOICE_STATION_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>DataPhi CRM — AWS Voice & Telemetry Station</title>
<style>
  :root { --p: #6C5CE7; --pd: #4B3FC4; --pl: #F3F1FD; --ink: #1E2430; --m: #64748B; --b: #E2E8F0; --bg: #F8FAFC; --warn: #FEF3C7; --warn-ink: #92400E; --danger: #EF4444; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--ink); }
  .topnav { height: 60px; background: #fff; border-bottom: 1px solid var(--b); display: flex; align-items: center; justify-content: space-between; padding: 0 28px; }
  .navlinks a { text-decoration: none; color: var(--m); font-size: 13px; font-weight: 700; padding: 8px 12px; border-radius: 8px; }
  .navlinks a.active { color: var(--pd); background: var(--pl); }
  .wrap { max-width: 1380px; margin: 24px auto; padding: 0 24px; display: grid; grid-template-columns: 440px 1fr; gap: 24px; }
  .card { background: #fff; border: 1px solid var(--b); border-radius: 14px; padding: 22px; box-shadow: 0 2px 10px rgba(0,0,0,0.02); }
  .mic-stage { background: linear-gradient(180deg, var(--pl), #fff); border: 1.5px dashed #D2CCFB; border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 18px; }
  .mic-btn { width: 64px; height: 64px; border-radius: 50%; border: none; background: var(--p); color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; margin: 0 auto 10px; transition: transform 0.2s; box-shadow: 0 0 0 6px rgba(108,92,231,0.15); }
  .mic-btn.recording { background: var(--danger); box-shadow: 0 0 0 10px rgba(239,68,68,0.25); }
  .t-header { font-size: 11px; font-weight: 800; text-transform: uppercase; color: var(--pd); letter-spacing: 0.05em; margin: 12px 0 6px; display: flex; justify-content: space-between; }
  .t-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 12px; }
  .metric { background: var(--bg); border: 1px solid var(--b); border-radius: 8px; padding: 8px; text-align: center; }
  .metric span { display: block; font-size: 9.5px; color: var(--m); font-weight: 700; text-transform: uppercase; }
  .metric strong { font-size: 13px; font-weight: 800; font-family: monospace; }
  .form-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px; }
  .field-group label { display: block; font-size: 10.5px; font-weight: 700; color: var(--m); margin-bottom: 4px; text-transform: uppercase; }
  .field-group input { width: 100%; padding: 8px; font-size: 12.5px; border: 1.2px solid var(--b); border-radius: 6px; }
  .btn-commit { width: 100%; background: var(--p); color: #fff; padding: 12px; border: none; border-radius: 8px; font-size: 13.5px; font-weight: 800; cursor: pointer; }
  .btn-commit:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
</head>
<body>
<div class="topnav">
  <div style="font-weight: 800; font-size: 15px;">DataPhi CRM — Cloud Voice Station</div>
  <div class="navlinks">
    <a href="/page/voice" class="active">🎙 Voice Station</a>
    <a href="/page/account">Accounts</a>
    <a href="/page/subsidiary">Subsidiaries</a>
    <a href="/page/contact">Contacts</a>
    <a href="/page/lead">Leads</a>
    <a href="/page/opportunity">Opportunities</a>
    <a href="/page/project">Projects</a>
    <a href="/page/activity">Activity</a>
  </div>
</div>
<div class="wrap">
  <div class="card">
    <div class="mic-stage">
      <button class="mic-btn" id="micBtn"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0"/><line x1="12" y1="19" x2="12" y2="22"/></svg></button>
      <div id="micStatus" style="font-weight: 700; font-size: 13px;">Click to Speak (AWS Cloud)</div>
      <div id="micTimer" style="font-family: monospace; font-size: 12px; color: var(--m); margin-top: 4px;">00:00</div>
    </div>
    <div id="clarBox" style="display:none; background: var(--warn); border: 1px solid #FCD34D; border-radius: 8px; padding: 12px; margin-bottom: 14px; font-size: 12px; color: var(--warn-ink); line-height: 1.4;"></div>
    <div class="t-header"><span>1. Token Telemetry</span><span>Claude Sonnet 5</span></div>
    <div class="t-grid">
      <div class="metric"><span>Input</span><strong id="tokIn">0</strong></div>
      <div class="metric"><span>Output</span><strong id="tokOut">0</strong></div>
      <div class="metric"><span>Total</span><strong id="tokTot">0</strong></div>
    </div>
    <div class="t-header"><span>2. Cost ($ USD)</span><span>Transcribe + Bedrock</span></div>
    <div class="t-grid">
      <div class="metric"><span>Transcribe</span><strong id="cTrans">$0.000000</strong></div>
      <div class="metric"><span>Sonnet 5</span><strong id="cBed">$0.000000</strong></div>
      <div class="metric"><span>Total</span><strong id="cTot">$0.000000</strong></div>
    </div>
    <div class="t-header"><span>3. Latency Benchmarks</span><span>Real-Time</span></div>
    <div class="t-grid">
      <div class="metric"><span>Transcribe</span><strong id="lTrans">0 ms</strong></div>
      <div class="metric"><span>Bedrock</span><strong id="lBed">0 ms</strong></div>
      <div class="metric"><span>Total</span><strong id="lTot">0 ms</strong></div>
    </div>
  </div>
  <div class="card">
    <div style="font-size: 14px; font-weight: 800; margin-bottom: 8px;">Extracted Transcript & Entities</div>
    <div id="transView" style="background: var(--bg); border: 1px solid var(--b); border-radius: 8px; padding: 10px; font-size: 12.5px; color: var(--m); font-style: italic; margin-bottom: 14px;">Spoken transcript will appear here...</div>
    <div style="font-size: 12px; font-weight: 800; color: var(--pd); margin-bottom: 8px;">1. Account</div>
    <div class="form-grid">
      <div class="field-group"><label>Account Name*</label><input type="text" id="acc_name"></div>
      <div class="field-group"><label>Account Manager</label><input type="text" id="acc_manager"></div>
      <div class="field-group"><label>Region</label><input type="text" id="acc_region"></div>
    </div>
    <div style="font-size: 12px; font-weight: 800; color: var(--pd); margin-bottom: 8px;">2. Contact</div>
    <div class="form-grid">
      <div class="field-group"><label>Contact Name*</label><input type="text" id="con_name"></div>
      <div class="field-group"><label>Designation</label><input type="text" id="con_desig"></div>
      <div class="field-group"><label>Email</label><input type="text" id="con_email"></div>
    </div>
    <div style="font-size: 12px; font-weight: 800; color: var(--pd); margin-bottom: 8px;">3. Opportunity & Lead</div>
    <div class="form-grid">
      <div class="field-group"><label>Deal Title</label><input type="text" id="opp_title"></div>
      <div class="field-group"><label>Value (AED)</label><input type="number" id="opp_val"></div>
      <div class="field-group"><label>Technology</label><input type="text" id="opp_tech"></div>
    </div>
    <button class="btn-commit" id="commitBtn" disabled>Commit to PostgreSQL Database</button>
  </div>
</div>
<script>
let mr=null, chunks=[], tInt=null, staged=null;
const btn = document.getElementById("micBtn"), stat = document.getElementById("micStatus"), timer = document.getElementById("micTimer"), cBtn = document.getElementById("commitBtn");
btn.onclick = async () => {
  if (mr && mr.state === "recording") {
    mr.stop(); btn.classList.remove("recording"); stat.textContent = "Processing via AWS..."; clearInterval(tInt);
  } else {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunks = []; mr = new MediaRecorder(stream);
      mr.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const form = new FormData();
        form.append("audio", new Blob(chunks, { type: "audio/webm" }), "voice.webm");
        const res = await fetch("/api/voice/process", { method: "POST", body: form });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || "Failed");
        render(d);
      };
      mr.start(); btn.classList.add("recording"); stat.textContent = "Listening... Click to Finish";
      let s = 0; tInt = setInterval(() => { s++; timer.textContent = `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`; }, 1000);
    } catch(err) { alert("Mic error: " + err.message); }
  }
};
function render(d) {
  staged = d;
  const tel = d.telemetry || {}, tok = tel.tokens || {}, c = tel.costs_usd || {}, l = tel.latency || {};
  document.getElementById("tokIn").textContent = tok.input_tokens || 0;
  document.getElementById("tokOut").textContent = tok.output_tokens || 0;
  document.getElementById("tokTot").textContent = tok.total_tokens || 0;
  document.getElementById("cTrans").textContent = `$${(c.transcribe_cost || 0).toFixed(6)}`;
  document.getElementById("cBed").textContent = `$${(c.bedrock_cost || 0).toFixed(6)}`;
  document.getElementById("cTot").textContent = `$${(c.total_cost || 0).toFixed(6)}`;
  document.getElementById("lTrans").textContent = `${l.transcribe_ms || 0} ms`;
  document.getElementById("lBed").textContent = `${l.bedrock_ms || 0} ms`;
  document.getElementById("lTot").textContent = `${l.backend_total_ms || 0} ms`;
  document.getElementById("transView").textContent = `"${d.transcript || ''}"`;
  const ext = d.extracted_data || {}, a = ext.account || {}, co = ext.contact || {}, o = ext.opportunity || {}, le = ext.lead || {};
  document.getElementById("acc_name").value = a.account_name || "";
  document.getElementById("acc_manager").value = a.account_manager || "";
  document.getElementById("acc_region").value = a.region || "";
  document.getElementById("con_name").value = co.contact_name || "";
  document.getElementById("con_desig").value = co.designation || "";
  document.getElementById("con_email").value = co.email || "";
  document.getElementById("opp_title").value = o.opportunity_name || le.lead_name || "";
  document.getElementById("opp_val").value = o.deal_size || le.deal_size || "";
  document.getElementById("opp_tech").value = o.technology || le.technology || "";
  const box = document.getElementById("clarBox");
  if (d.missing_fields && d.missing_fields.length > 0) {
    box.style.display = "block"; box.textContent = "⚠️ Incomplete Draft: " + d.clarification_prompt;
    cBtn.disabled = true;
  } else {
    box.style.display = "none"; cBtn.disabled = false;
  }
  stat.textContent = "Click to Speak (AWS Cloud)";
}
cBtn.onclick = async () => {
  if (!staged) return;
  const payload = {
    draft_id: staged.draft_id, intent: staged.intent,
    account: { account_name: document.getElementById("acc_name").value.trim(), account_manager: document.getElementById("acc_manager").value.trim(), region: document.getElementById("acc_region").value.trim() },
    contact: { contact_name: document.getElementById("con_name").value.trim(), designation: document.getElementById("con_desig").value.trim(), email: document.getElementById("con_email").value.trim() },
    lead: { lead_name: document.getElementById("opp_title").value.trim(), deal_size: document.getElementById("opp_val").value || null, technology: document.getElementById("opp_tech").value.trim() },
    opportunity: { opportunity_name: document.getElementById("opp_title").value.trim(), deal_size: document.getElementById("opp_val").value || null, technology: document.getElementById("opp_tech").value.trim() }
  };
  const res = await fetch("/api/voice/commit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const d = await res.json();
  if (!res.ok) alert("Commit failed: " + (d.detail || "Error"));
  else { alert("Committed successfully to PostgreSQL!"); cBtn.disabled = true; }
};
</script>
</body>
</html>
"""