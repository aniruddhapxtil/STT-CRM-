from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, Date, DateTime, 
    ForeignKey, Boolean, JSON
)
from sqlalchemy.orm import relationship
from backend.database import Base

class User(Base):
    __tablename__ = "user"
    user_id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String(100), nullable=False)
    email_id = Column(String(120), unique=True, nullable=False)
    designation = Column(String(100))
    region = Column(String(50))  # Dubai, Abu Dhabi, Sharjah, KSA, Other
    phone = Column(String(30))

class Account(Base):
    __tablename__ = "account"
    account_id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String(200), nullable=False)
    account_manager = Column(String(150))
    region = Column(String(50))  # Dubai, Abu Dhabi, Sharjah, KSA, Other
    industry = Column(String(100))  # Retail, Real Estate..., BFSI, etc.
    primary_address = Column(String(255))
    secondary_address = Column(String(255))
    creation_date = Column(DateTime, default=datetime.utcnow)
    last_update_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)

class Subsidiary(Base):
    __tablename__ = "subsidiary"
    subsidiary_id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=False)
    subsidiary_name = Column(String(200), nullable=False)
    region = Column(String(50))
    industry = Column(String(100))
    primary_address = Column(String(255))
    secondary_address = Column(String(255))
    creation_date = Column(DateTime, default=datetime.utcnow)
    last_update_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)

class Contact(Base):
    __tablename__ = "contact"
    contact_id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=True)
    subsidiary_id = Column(Integer, ForeignKey("subsidiary.subsidiary_id"), nullable=True)
    contact_name = Column(String(150), nullable=False)
    designation = Column(String(100))
    linkedin_url = Column(String(255))
    email = Column(String(120))
    mobile = Column(String(40))
    secondary_mobile = Column(String(40))
    primary_address = Column(String(255))
    secondary_address = Column(String(255))
    notes = Column(Text)
    creation_date = Column(DateTime, default=datetime.utcnow)
    last_update_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)

class Lead(Base):
    __tablename__ = "lead"
    lead_id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=True)
    subsidiary_id = Column(Integer, ForeignKey("subsidiary.subsidiary_id"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contact.contact_id"), nullable=True)
    lead_name = Column(String(255), nullable=False)
    account_manager = Column(String(150))
    deal_size = Column(Numeric(12, 2))
    currency = Column(String(10), default="AED")  # AED, USD, SAR
    stage = Column(String(50), default="Qualified")  # Qualified, Disqualified
    disqualification_reason = Column(String(100))  # No requirement, No budget, etc.
    type = Column(String(20), default="Warm")  # Cold, Warm, Hot
    lead_source = Column(String(50))  # Outbound, Inbound, Website, Event, Referral, Partner
    service = Column(String(50))  # DE & DV, Phi AI, Governance, Migration
    technology = Column(String(50))  # Azure, AWS, Databricks, Other
    next_steps = Column(String(255))
    next_action_date = Column(Date)
    closure_date = Column(Date)
    notes = Column(Text)
    creation_date = Column(DateTime, default=datetime.utcnow)
    last_update_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)

class Opportunity(Base):
    __tablename__ = "opportunity"
    opportunity_id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("lead.lead_id"), nullable=False)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=False)
    subsidiary_id = Column(Integer, ForeignKey("subsidiary.subsidiary_id"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contact.contact_id"), nullable=True)
    opportunity_name = Column(String(255), nullable=False)
    deal_size = Column(Numeric(12, 2))
    currency = Column(String(10), default="AED")  # AED, USD, SAR
    project_type = Column(String(50), default="T&M")  # T&M, Fixed Cost
    service = Column(String(50))  # DE & DV, Phi AI, Governance, Migration
    opportunity_type = Column(String(50))  # New, Upsell, Cross Sell, Renewal, Support
    funded_by = Column(String(50))  # Client, MS, AWS, Snowflake
    technology = Column(String(50))  # Azure, AWS, Databricks, Other
    opportunity_source = Column(String(255))
    stage = Column(String(50), default="Discovery — 40%")
    probability = Column(Integer, default=40)
    reason = Column(String(100))  # Commercial / Price, Competitor, etc.
    next_steps = Column(String(255))
    next_action_date = Column(Date)
    closure_date = Column(Date)
    notes = Column(Text)
    creation_date = Column(DateTime, default=datetime.utcnow)
    last_update_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)

class Project(Base):
    __tablename__ = "project"
    project_id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("opportunity.opportunity_id"), nullable=False)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=False)
    subsidiary_id = Column(Integer, ForeignKey("subsidiary.subsidiary_id"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contact.contact_id"), nullable=True)
    project_name = Column(String(255), nullable=False)
    technology = Column(String(50))
    service = Column(String(50))
    value = Column(Numeric(12, 2))
    currency = Column(String(10), default="AED")
    start_date = Column(Date)
    close_date = Column(Date)
    po_status = Column(String(50), default="Awaited")  # Awaited, Received, Validated, Closed, Cancelled
    po_number = Column(String(100))
    po_reason = Column(Text)
    notes = Column(Text)
    creation_date = Column(DateTime, default=datetime.utcnow)
    last_update_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)

class Activity(Base):
    __tablename__ = "activity"
    activity_id = Column(Integer, primary_key=True, index=True)
    activity_name = Column(String(255), nullable=False)
    record_type = Column(String(50), nullable=False)  # Account, Subsidiary, Contact, Lead, Opportunity, Project
    record_action = Column(String(50), nullable=False)  # Email, LinkedIn, Phone Call, Meeting, Demo, Note, Proposal Sent, PO Received
    activity_outcome = Column(String(50))  # Connected, Interested, Meeting Scheduled, etc.
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=True)
    subsidiary_id = Column(Integer, ForeignKey("subsidiary.subsidiary_id"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contact.contact_id"), nullable=True)
    lead_id = Column(Integer, ForeignKey("lead.lead_id"), nullable=True)
    opportunity_id = Column(Integer, ForeignKey("opportunity.opportunity_id"), nullable=True)
    project_id = Column(Integer, ForeignKey("project.project_id"), nullable=True)
    activity_date = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)
    next_step = Column(String(255))
    next_action_date = Column(Date)
    creation_date = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)

class VoiceDraft(Base):
    """Saves raw voice transcripts and intermediate extraction drafts."""
    __tablename__ = "voice_draft"
    draft_id = Column(Integer, primary_key=True, index=True)
    transcript = Column(Text, nullable=False)
    extracted_json = Column(JSON, nullable=False)
    target_entity = Column(String(50), nullable=False)  # account, lead, opportunity, etc.
    status = Column(String(30), default="draft")        # draft, confirmed, discarded
    committed_id = Column(Integer, nullable=True)       # ID of created record once saved
    created_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("user.user_id"), nullable=True)