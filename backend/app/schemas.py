from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class DiaryEntryRequest(BaseModel):
    text: Optional[str] = Field(None, description="Text entry")
    audio_data: Optional[str] = Field(None, description="Base64 encoded audio data")
    entry_type: str = Field(..., description="Type: 'chronic_condition', 'genetic_condition', 'allergy', 'vitals', 'lifestyle_risk', 'past_illness', 'medication', or 'family_history'")
    gender: Optional[str] = Field(None, description="Gender: 'male' or 'female'")
    family_history: Optional[str] = Field(None, description="Family medical history, especially parental diseases")
    timestamp: Optional[datetime] = Field(None, description="Entry timestamp")


class DiaryEntryResponse(BaseModel):
    id: str
    text: str
    entry_type: str
    timestamp: datetime
    gender: Optional[str] = None
    family_history: Optional[str] = None
    genetic_risk_assessment: Optional[str] = None
    sentiment: Optional[str] = None
    summary: Optional[str] = None
    suggestions: List[str] = []


class DiarySummaryResponse(BaseModel):
    total_entries: int
    date_range: Dict[str, str]
    sentiment_trend: List[Dict[str, Any]]
    common_diseases: List[Dict[str, Any]]
    mood_patterns: List[Dict[str, Any]]
    suggestions: List[str]
    visualization_data: Dict[str, Any]


class AIInsightsResponse(BaseModel):
    insights: str = Field(..., description="AI-generated health insights based on diary entries")
    life_choices: List[str] = Field(..., description="Recommendations for better life choices")
    awareness_alerts: List[str] = Field(..., description="Things to be aware of (not diagnoses)")


class ClinicalNoteRequest(BaseModel):
    audio_data: str = Field(..., description="Base64 encoded audio data")
    language: str = Field("en-US", description="Speech language code")


class SOAPNote(BaseModel):
    subjective: str = Field(..., description="Patient's subjective description")
    objective: str = Field(..., description="Objective observations and findings")
    assessment: str = Field(..., description="Clinical assessment and diagnosis")
    plan: str = Field(..., description="Treatment plan and next steps")


class ClinicalNoteResponse(BaseModel):
    transcription: str
    soap_note: SOAPNote


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
