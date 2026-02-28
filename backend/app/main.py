import os
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import threading
from dotenv import load_dotenv
import httpx

from .azure_clients import AzureClients
from .schemas import (
    DiaryEntryRequest, DiaryEntryResponse, DiarySummaryResponse,
    ClinicalNoteRequest, ClinicalNoteResponse, SOAPNote, ErrorResponse,
    AIInsightsResponse
)
from .pipeline import DiaryPipeline, SOAPPipeline
from .utils_audio import decode_audio_base64, validate_audio_format

import pathlib
try:
    backend_dir = pathlib.Path(__file__).parent.parent
    env_path = backend_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
        print(f"Loaded .env from: {env_path} (override=True)")
    else:
        load_dotenv(override=True)
        print("Loaded .env from current directory (override=True)")
except Exception as e:
    print(f"Warning: Error loading .env file: {e}")
    load_dotenv(override=True)

DOCTORS_FILE = pathlib.Path(__file__).parent / "doctors.json"

from starlette.requests import Request
from starlette.datastructures import UploadFile as StarletteUploadFile

app = FastAPI(
    title="Healthcare AI Assistant",
    description="AI-powered health diary summarizer and clinical note cleaner",
    version="1.0.0"
)

import starlette.requests
original_max_content_length = getattr(starlette.requests.Request, 'max_content_length', None)
starlette.requests.Request.max_content_length = 10 * 1024 * 1024

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    azure_clients = AzureClients()
    diary_pipeline = DiaryPipeline(azure_clients)
    soap_pipeline = SOAPPipeline(azure_clients)
    print("Azure clients initialized successfully")
except Exception as e:
    print(f"Error initializing Azure clients: {e}")
    import traceback
    traceback.print_exc()
    raise

diary_entries: List[Dict[str, Any]] = []


@app.get("/")
async def root():
    return {
        "message": "Healthcare AI Assistant API",
        "endpoints": {
            "health_diary": "/api/diary",
            "clinical_notes": "/api/clinical/transcribe"
        }
    }


@app.get("/health")
async def health_check():
    try:
        speech_available = False
        openai_available = False
        text_analytics_available = False
        
        try:
            speech_available = azure_clients.speech_config is not None
        except Exception as e:
            print(f"Speech service check failed: {e}")
        
        try:
            client = azure_clients.openai_client
            openai_available = client is not None
            if not openai_available:
                print("OpenAI client is None - checking environment variables...")
                print(f"  Endpoint set: {bool(azure_clients.openai_endpoint)}")
                print(f"  API key set: {bool(azure_clients.openai_api_key)}")
        except Exception as e:
            print(f"OpenAI service check failed: {e}")
            import traceback
            traceback.print_exc()
            openai_available = False
        
        try:
            text_analytics_available = azure_clients.text_analytics_client is not None
        except Exception as e:
            print(f"Text Analytics service check failed: {e}")
        
        return {
            "status": "healthy", 
            "services": {
                "speech": speech_available,
                "openai": openai_available,
                "text_analytics": text_analytics_available
            },
            "debug": {
                "speech_key_set": bool(azure_clients.speech_key),
                "speech_region": azure_clients.speech_region,
                "openai_endpoint_set": bool(azure_clients.openai_endpoint),
                "openai_api_key_set": bool(azure_clients.openai_api_key),
                "openai_endpoint": azure_clients.openai_endpoint if azure_clients.openai_endpoint else None,
                "text_analytics_endpoint_set": bool(azure_clients.text_analytics_endpoint)
            }
        }
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"Health check error: {error_detail}")
        return {
            "status": "error",
            "error": str(e),
            "debug": {
                "speech_key_set": bool(getattr(azure_clients, 'speech_key', None)),
                "speech_region": getattr(azure_clients, 'speech_region', 'unknown'),
            }
        }


@app.post("/api/diary/entry", response_model=DiaryEntryResponse)
async def create_diary_entry(
    text: str = Form(None),
    audio_data: str = Form(None),
    entry_type: str = Form(...),
    gender: str = Form(None),
    family_history: str = Form(None),
    timestamp: str = Form(None)
):
    try:
        transcribed_text = text
        if audio_data and not text:
            try:
                audio_bytes = decode_audio_base64(audio_data)
                is_valid, msg = validate_audio_format(audio_bytes)
                if not is_valid:
                    raise HTTPException(status_code=400, detail=f"Invalid audio format: {msg}")
                
                transcribed_text = azure_clients.transcribe_audio(audio_bytes)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Audio transcription failed: {str(e)}")
        
        if not transcribed_text:
            raise HTTPException(status_code=400, detail="Either text or audio_data must be provided")
        
        entry_timestamp = datetime.now()
        if timestamp:
            try:
                entry_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except:
                pass
        
        sentiment = diary_pipeline.analyze_sentiment(transcribed_text)
        
        # Genetic risk assessment using NCBI API if relevant
        # Also assess if this is a family history entry or if family history is provided
        genetic_risk_assessment = None
        should_assess = (
            entry_type in ["genetic_condition", "chronic_condition", "family_history"] and 
            (family_history or transcribed_text)
        )
        
        if should_assess:
            try:
                # For family_history entries, use the text as family history
                assessment_text = transcribed_text
                assessment_family_history = family_history
                
                if entry_type == "family_history":
                    assessment_family_history = transcribed_text
                    assessment_text = "Family history entry"
                
                genetic_risk_assessment = await diary_pipeline.assess_genetic_risk(
                    assessment_text, 
                    gender, 
                    assessment_family_history,
                    azure_clients
                )
            except Exception as e:
                print(f"Error in genetic risk assessment: {e}")
                import traceback
                traceback.print_exc()
        
        # For family_history entries, store the text as both text and family_history
        stored_family_history = family_history
        if entry_type == "family_history":
            stored_family_history = transcribed_text
        
        entry_dict = {
            "id": str(uuid.uuid4()),
            "text": transcribed_text,
            "entry_type": entry_type,
            "timestamp": entry_timestamp,
            "gender": gender,
            "family_history": stored_family_history,
            "sentiment": sentiment,
            "genetic_risk_assessment": genetic_risk_assessment
        }
        
        # Generate suggestions (will use NCBI data if available)
        try:
            suggestions = await diary_pipeline._generate_suggestions_with_ncbi([entry_dict])
        except Exception as e:
            print(f"Error generating suggestions with NCBI, using fallback: {e}")
            suggestions = diary_pipeline._generate_simple_suggestions([entry_dict])
        
        diary_entries.append(entry_dict)
        
        return DiaryEntryResponse(
            id=entry_dict["id"],
            text=transcribed_text,
            entry_type=entry_type,
            timestamp=entry_timestamp,
            gender=gender,
            family_history=stored_family_history,
            genetic_risk_assessment=genetic_risk_assessment,
            sentiment=sentiment,
            summary=None,
            suggestions=suggestions
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating diary entry: {str(e)}")


@app.get("/api/diary/entries", response_model=List[DiaryEntryResponse])
async def get_diary_entries():
    return [
        DiaryEntryResponse(
            id=entry["id"],
            text=entry["text"],
            entry_type=entry["entry_type"],
            timestamp=entry["timestamp"],
            gender=entry.get("gender"),
            family_history=entry.get("family_history"),
            genetic_risk_assessment=entry.get("genetic_risk_assessment"),
            sentiment=entry.get("sentiment"),
            summary=None,
            suggestions=entry.get("suggestions", [])
        )
        for entry in diary_entries
    ]


@app.get("/api/diary/summary", response_model=DiarySummaryResponse)
async def get_diary_summary():
    try:
        summary = diary_pipeline.generate_summary(diary_entries)
        return DiarySummaryResponse(**summary)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating summary: {str(e)}")


@app.get("/api/diary/ai-insights", response_model=AIInsightsResponse)
async def get_ai_insights():
    try:
        insights = await diary_pipeline.generate_ai_insights(diary_entries, azure_clients)
        return AIInsightsResponse(**insights)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating AI insights: {str(e)}")


@app.delete("/api/diary/entries/{entry_id}")
async def delete_diary_entry(entry_id: str):
    global diary_entries
    original_count = len(diary_entries)
    diary_entries = [e for e in diary_entries if e["id"] != entry_id]
    
    if len(diary_entries) == original_count:
        raise HTTPException(status_code=404, detail="Entry not found")
    
    return {"message": "Entry deleted successfully"}


@app.post("/api/clinical/transcribe", response_model=ClinicalNoteResponse)
async def transcribe_clinical_note(
    audio_data: str = Form(...),
    language: str = Form("en-US"),
    diary_entries: str = Form(None)
):
    try:
        audio_bytes = decode_audio_base64(audio_data)
        is_valid, msg = validate_audio_format(audio_bytes)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid audio format: {msg}")
        
        transcription = azure_clients.transcribe_audio(audio_bytes, language=language)
        
        entries_list = []
        if diary_entries:
            try:
                entries_list = json.loads(diary_entries)
                print(f"Received {len(entries_list)} diary entries for context in transcribe endpoint")
                for entry in entries_list:
                    print(f"  Entry: {entry.get('entry_type')} - {entry.get('text')}")
            except Exception as e:
                print(f"Error parsing diary entries in transcribe: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("No diary entries received in transcribe endpoint")
        
        soap_note_dict = soap_pipeline.generate_soap_note(transcription, None, entries_list)
        soap_note = SOAPNote(**soap_note_dict)
        
        return ClinicalNoteResponse(
            transcription=transcription,
            soap_note=soap_note
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing clinical note: {str(e)}")


@app.get("/test-openai")
async def test_openai():
    import sys
    import logging
    from openai import AzureOpenAI
    
    sys.stdout.flush()
    print("\n" + "="*50, flush=True)
    print("=== TESTING OPENAI CLIENT ===", flush=True)
    print("="*50, flush=True)
    sys.stdout.flush()
    
    try:
        endpoint = getattr(azure_clients, 'openai_endpoint', None)
        api_key = getattr(azure_clients, 'openai_api_key', None)
        deployment = getattr(azure_clients, 'openai_deployment', None)
        api_version = getattr(azure_clients, 'openai_api_version', None)
        
        debug_info = {
            "endpoint": endpoint,
            "api_key_present": bool(api_key),
            "api_key_length": len(api_key) if api_key else 0,
            "deployment": deployment,
            "api_version": api_version
        }
        
        print(f"Endpoint: {endpoint}")
        print(f"API Key present: {bool(api_key)}")
        if api_key:
            print(f"API Key length: {len(api_key)}")
        print(f"Deployment: {deployment}")
        print(f"API Version: {api_version}")
        
        if not endpoint:
            return {
                "status": "error",
                "message": "AZURE_OPENAI_ENDPOINT is not set",
                "debug": debug_info
            }
        
        if not api_key:
            return {
                "status": "error",
                "message": "AZURE_OPENAI_API_KEY is not set",
                "debug": debug_info
            }
        
        print("Attempting direct initialization...")
        endpoint_clean = endpoint.rstrip('/')
        
        try:
            test_client = AzureOpenAI(
                api_version=api_version,
                azure_endpoint=endpoint_clean,
                api_key=api_key
            )
            print("Direct initialization successful!")
            
            print("Making test API call...")
            response = test_client.chat.completions.create(
                model=deployment,
                messages=[{"role": "user", "content": "Say hello"}],
                max_tokens=10
            )
            
            return {
                "status": "success",
                "message": "OpenAI is working!",
                "test_response": response.choices[0].message.content,
                "debug": debug_info
            }
        except Exception as init_error:
            error_msg = str(init_error)
            error_type = type(init_error).__name__
            print(f"Initialization failed: {error_type}: {error_msg}")
            import traceback
            tb = traceback.format_exc()
            print(tb)
            
            return {
                "status": "error",
                "message": f"Failed to initialize OpenAI client: {error_msg}",
                "error_type": error_type,
                "traceback": tb,
                "debug": debug_info
            }
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"ERROR in test_openai: {error_detail}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "traceback": error_detail
            }
        )


@app.post("/api/clinical/text-to-soap", response_model=ClinicalNoteResponse)
async def text_to_soap(text: str = Form(...), diary_entries: str = Form(None)):
    try:
        print(f"\n=== SOAP Generation Request ===")
        print(f"OpenAI client check: {azure_clients.openai_client is not None}")
        if not azure_clients.openai_client:
            print("WARNING: OpenAI client is None - will use fallback")
            print(f"Endpoint: {azure_clients.openai_endpoint}")
            print(f"API Key set: {bool(azure_clients.openai_api_key)}")
            print(f"Deployment: {azure_clients.openai_deployment}")
            print(f"API Version: {azure_clients.openai_api_version}")
        
        entries_list = []
        if diary_entries:
            try:
                entries_list = json.loads(diary_entries)
                print(f"Received {len(entries_list)} diary entries for context")
                for entry in entries_list:
                    print(f"  Entry: {entry.get('entry_type')} - {entry.get('text')}")
            except Exception as e:
                print(f"Error parsing diary entries: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("No diary entries received")
        
        soap_note_dict = soap_pipeline.generate_soap_note(text, None, entries_list)
        soap_note = SOAPNote(**soap_note_dict)
        
        return ClinicalNoteResponse(
            transcription=text,
            soap_note=soap_note
        )
    except Exception as e:
        print(f"ERROR in text_to_soap: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error generating SOAP note: {str(e)}")


@app.websocket("/ws/clinical/stream")
async def websocket_clinical_stream(websocket: WebSocket):
    await websocket.accept()
    
    recognizer = None
    push_stream = None
    current_transcript = ""
    current_soap = {
        "subjective": "",
        "objective": "No objective findings documented.",
        "assessment": "",
        "plan": ""
    }
    diary_entries = []
    update_buffer = []
    loop = asyncio.get_event_loop()
    
    def speech_callback(result_type, text):
        nonlocal current_transcript, update_buffer
        
        if result_type == "final":
            current_transcript += " " + text if current_transcript else text
            current_transcript = current_transcript.strip()
            update_buffer.append(("final", text))
            
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(websocket.send_json({
                    "type": "transcription",
                    "status": "final",
                    "text": text,
                    "full_transcript": current_transcript
                }))
            )
        elif result_type == "interim":
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(websocket.send_json({
                    "type": "transcription",
                    "status": "interim",
                    "text": text
                }))
            )
        elif result_type == "error":
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(websocket.send_json({
                    "type": "error",
                    "message": text
                }))
            )
    
    try:
        init_data = await websocket.receive_json()
        if init_data.get("type") == "init":
            if init_data.get("diary_entries"):
                try:
                    diary_entries = json.loads(init_data["diary_entries"])
                except:
                    pass
            
            recognizer, push_stream = azure_clients.start_continuous_recognition(
                speech_callback,
                language=init_data.get("language", "en-US")
            )
            
            await websocket.send_json({"type": "ready"})
        
        async def process_soap_updates():
            nonlocal current_soap, update_buffer, current_transcript
            first_update = True
            
            while True:
                if first_update:
                    await asyncio.sleep(3.0)
                    first_update = False
                else:
                    await asyncio.sleep(2.5)
                
                if current_transcript and len(current_transcript.strip()) > 10:
                    try:
                        new_text = ""
                        if update_buffer:
                            final_chunks = [text for status, text in update_buffer if status == "final"]
                            update_buffer = []
                            new_text = " ".join(final_chunks)
                        
                        updated_soap = await loop.run_in_executor(
                            None,
                            lambda: soap_pipeline.update_soap_incremental(
                                new_text if new_text else current_transcript,
                                current_soap,
                                current_transcript,
                                diary_entries
                            )
                        )
                        
                        changed_sections = []
                        for section in ["subjective", "objective", "assessment", "plan"]:
                            if updated_soap.get(section) != current_soap.get(section):
                                changed_sections.append(section)
                        
                        current_soap = updated_soap
                        
                        await websocket.send_json({
                            "type": "soap_update",
                            "soap": updated_soap,
                            "changed_sections": changed_sections
                        })
                    except Exception as e:
                        print(f"Error updating SOAP: {e}")
        
        update_task = asyncio.create_task(process_soap_updates())
        running = True
        
        while running:
            try:
                data = await websocket.receive()
                
                if "bytes" in data:
                    audio_chunk = data["bytes"]
                    if push_stream:
                        push_stream.write(audio_chunk.tobytes() if hasattr(audio_chunk, 'tobytes') else bytes(audio_chunk))
                elif "text" in data:
                    message = json.loads(data["text"])
                    if message.get("type") == "stop":
                        running = False
                        break
            except WebSocketDisconnect:
                running = False
                break
            except Exception as e:
                print(f"WebSocket error: {e}")
                running = False
                break
        
        print("Stop signal received, preparing for final SOAP generation...")
        
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass
        
        print("Waiting for speech recognition to finalize...")
        await asyncio.sleep(1.5)
        
        if recognizer:
            try:
                print("Stopping continuous recognition...")
                recognizer.stop_continuous_recognition_async().get()
                print("Recognition stopped")
            except Exception as e:
                print(f"Error stopping recognizer: {e}")
        
        await asyncio.sleep(1.0)
        
        if push_stream:
            try:
                print("Closing audio stream...")
                push_stream.close()
                print("Audio stream closed")
            except Exception as e:
                print(f"Error closing stream: {e}")
        
        await asyncio.sleep(0.5)
        
        try:
            if not current_transcript or current_transcript.strip() == "":
                current_transcript = "No speech detected."
            
            print(f"=== FINAL SOAP GENERATION ===")
            print(f"Transcript length: {len(current_transcript)}")
            print(f"Transcript: {current_transcript}")
            print(f"Current incremental SOAP state: {current_soap}")
            print(f"Diary entries: {len(diary_entries)}")
            
            final_soap = await loop.run_in_executor(
                None,
                lambda: soap_pipeline.generate_soap_note(current_transcript, None, diary_entries)
            )
            
            print(f"=== FINAL SOAP GENERATED ===")
            print(f"Subjective: {final_soap.get('subjective', '')[:100]}...")
            print(f"Assessment: {final_soap.get('assessment', '')[:100]}...")
            print(f"Plan: {final_soap.get('plan', '')[:100]}...")
            
            await websocket.send_json({
                "type": "final",
                "transcription": current_transcript,
                "soap": final_soap
            })
            print(f"Final SOAP note sent to client. Transcript length: {len(current_transcript)}")
        except Exception as e:
            print(f"ERROR generating final SOAP: {e}")
            import traceback
            traceback.print_exc()
            try:
                print("Attempting fallback: generating fresh SOAP from transcript...")
                final_soap = await loop.run_in_executor(
                    None,
                    lambda: soap_pipeline.generate_soap_note(current_transcript or "No transcript available", None, diary_entries)
                )
                await websocket.send_json({
                    "type": "final",
                    "transcription": current_transcript or "Error occurred",
                    "soap": final_soap
                })
                print("Fallback SOAP sent successfully")
            except Exception as e2:
                print(f"ERROR in fallback: {e2}")
                try:
                    await websocket.send_json({
                        "type": "final",
                        "transcription": current_transcript or "Error occurred",
                        "soap": current_soap
                    })
                except:
                    pass
        
    except Exception as e:
        print(f"WebSocket stream error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass

@app.websocket("/ws/diary/stream")
async def websocket_diary_stream(websocket: WebSocket):
    await websocket.accept()
    
    recognizer = None
    push_stream = None
    current_transcript = ""
    loop = asyncio.get_event_loop()
    
    def speech_callback(result_type, text):
        nonlocal current_transcript
        
        if result_type == "final":
            current_transcript += " " + text if current_transcript else text
            current_transcript = current_transcript.strip()
            
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(websocket.send_json({
                    "type": "transcription",
                    "status": "final",
                    "text": text,
                    "full_transcript": current_transcript
                }))
            )
        elif result_type == "interim":
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(websocket.send_json({
                    "type": "transcription",
                    "status": "interim",
                    "text": text
                }))
            )
        elif result_type == "error":
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(websocket.send_json({
                    "type": "error",
                    "message": text
                }))
            )
    
    try:
        init_data = await websocket.receive_json()
        if init_data.get("type") == "init":
            recognizer, push_stream = azure_clients.start_continuous_recognition(
                speech_callback,
                language=init_data.get("language", "en-US")
            )
            
            await websocket.send_json({"type": "ready"})
        
        running = True
        
        while running:
            try:
                data = await websocket.receive()
                
                if "bytes" in data:
                    audio_chunk = data["bytes"]
                    if push_stream:
                        push_stream.write(audio_chunk.tobytes() if hasattr(audio_chunk, 'tobytes') else bytes(audio_chunk))
                elif "text" in data:
                    message = json.loads(data["text"])
                    if message.get("type") == "stop":
                        running = False
                        break
            except WebSocketDisconnect:
                running = False
                break
            except Exception as e:
                print(f"WebSocket error: {e}")
                running = False
                break
        
        # Stop recognition and send final transcript
        if recognizer:
            try:
                recognizer.stop_continuous_recognition_async().get()
            except Exception as e:
                print(f"Error stopping recognizer: {e}")
        
        await asyncio.sleep(1.0)
        
        if push_stream:
            try:
                push_stream.close()
            except Exception as e:
                print(f"Error closing stream: {e}")
        
        await asyncio.sleep(0.5)
        
        # Send final transcription
        if not current_transcript or current_transcript.strip() == "":
            current_transcript = "No speech detected."
        
        await websocket.send_json({
            "type": "final",
            "transcription": current_transcript
        })
        
    except Exception as e:
        print(f"WebSocket stream error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass


@app.get("/api/doctors")
async def get_doctors(
    specialty: str = None,
    assessment: str = None,
    transcription: str = None,
    city: str = None,
    state: str = None
):
    try:
        import httpx
        
        default_state = os.getenv("NPI_DEFAULT_STATE", "NY")
        default_city = os.getenv("NPI_DEFAULT_CITY", "")
        search_limit = int(os.getenv("NPI_SEARCH_LIMIT", "10"))
        
        search_state = state or default_state
        search_city = city or default_city
        
        combined_text = f"{(assessment or '')} {(transcription or '')}".strip()
        print(f"[DOCTORS] Combined text: {combined_text[:200]}...")
        print(f"[DOCTORS] Assessment: {assessment}")
        print(f"[DOCTORS] Transcription: {transcription}")
        
        taxonomy_description = None
        
        if azure_clients.openai_client and combined_text:
            try:
                ai_prompt = f"""Based on the following patient symptoms and assessment, determine the most appropriate medical specialty needed. 

Patient Information:
{combined_text}

Respond with ONLY the medical specialty taxonomy name that would be most appropriate. Use one of these exact taxonomy names from the NPI Registry:
- Family Medicine
- Internal Medicine
- Cardiology
- Endocrinology
- Neurology
- Orthopedic Surgery
- Dermatology
- Gastroenterology
- Pulmonology
- Rheumatology
- Psychiatry
- Pediatrics
- Obstetrics & Gynecology
- Emergency Medicine

If the symptoms suggest multiple specialties, choose the PRIMARY specialty that would be most critical for initial evaluation.

Respond with ONLY the specialty name, nothing else."""

                response = azure_clients.openai_client.chat.completions.create(
                    model=azure_clients.openai_deployment,
                    messages=[
                        {"role": "system", "content": "You are a medical specialty advisor. Analyze symptoms and recommend the most appropriate medical specialty."},
                        {"role": "user", "content": ai_prompt}
                    ],
                    temperature=0.3,
                    max_tokens=50
                )
                
                taxonomy_description = response.choices[0].message.content.strip()
                print(f"AI recommended specialty: {taxonomy_description}")
                
                taxonomy_map = {
                    "family medicine": "Family Medicine",
                    "internal medicine": "Internal Medicine",
                    "cardiology": "Cardiology",
                    "endocrinology": "Endocrinology",
                    "neurology": "Neurology",
                    "orthopedic": "Orthopedic Surgery",
                    "orthopedics": "Orthopedic Surgery",
                    "dermatology": "Dermatology",
                    "gastroenterology": "Gastroenterology",
                    "pulmonology": "Pulmonology",
                    "rheumatology": "Rheumatology",
                    "psychiatry": "Psychiatry",
                    "pediatrics": "Pediatrics",
                    "obstetrics": "Obstetrics & Gynecology",
                    "gynecology": "Obstetrics & Gynecology",
                    "emergency": "Emergency Medicine"
                }
                
                taxonomy_lower = taxonomy_description.lower()
                for key, value in taxonomy_map.items():
                    if key in taxonomy_lower:
                        taxonomy_description = value
                        break
                
            except Exception as e:
                print(f"Error getting AI specialty recommendation: {e}")
                taxonomy_description = None
        
        if not taxonomy_description:
            combined_text_lower = combined_text.lower()
            if any(keyword in combined_text_lower for keyword in ["heart", "cardiac", "chest", "hypertension", "blood pressure", "arrhythmia"]):
                taxonomy_description = "Cardiology"
            elif any(keyword in combined_text_lower for keyword in ["diabetes", "thyroid", "hormone", "metabolic", "insulin", "glucose"]):
                taxonomy_description = "Endocrinology"
            elif any(keyword in combined_text_lower for keyword in ["headache", "migraine", "neurological", "brain", "nerve", "seizure", "stroke"]):
                taxonomy_description = "Neurology"
            elif any(keyword in combined_text_lower for keyword in ["bone", "joint", "knee", "hip", "fracture", "arthritis"]):
                taxonomy_description = "Orthopedic Surgery"
            elif any(keyword in combined_text_lower for keyword in ["skin", "rash", "dermatitis", "acne"]):
                taxonomy_description = "Dermatology"
            elif any(keyword in combined_text_lower for keyword in ["stomach", "digestive", "gastro", "nausea", "vomit"]):
                taxonomy_description = "Gastroenterology"
            elif any(keyword in combined_text_lower for keyword in ["lung", "breathing", "asthma", "cough", "respiratory"]):
                taxonomy_description = "Pulmonology"
            else:
                taxonomy_description = "Family Medicine"
        
        params = {
            "version": "2.1",
            "limit": min(search_limit * 2, 50)
        }
        
        if taxonomy_description:
            params["taxonomy_description"] = taxonomy_description
        
        if search_state:
            params["state"] = search_state
        
        if search_city:
            params["city"] = search_city
        
        npi_url = "https://npiregistry.cms.hhs.gov/api/"
        print(f"[DOCTORS] NPI API params: {params}")
        print(f"[DOCTORS] Using specialty: {taxonomy_description}")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(npi_url, params=params)
            response.raise_for_status()
            npi_data = response.json()
        
        print(f"[DOCTORS] NPI API response - result_count: {npi_data.get('result_count', 0)}")
        
        doctors = []
        if npi_data.get("result_count", 0) > 0:
            all_providers = []
            for result in npi_data.get("results", []):
                provider = result.get("basic", {})
                addresses = result.get("addresses", [])
                taxonomies = result.get("taxonomies", [])
                
                primary_address = addresses[0] if addresses else {}
                primary_taxonomy = taxonomies[0] if taxonomies else {}
                
                doctor_name = ""
                if provider.get("organization_name"):
                    doctor_name = provider["organization_name"]
                else:
                    first_name = provider.get("first_name", "")
                    last_name = provider.get("last_name", "")
                    doctor_name = f"{first_name} {last_name}".strip()
                
                if not doctor_name:
                    continue
                
                address_parts = [
                    primary_address.get("address_1", ""),
                    primary_address.get("address_2", ""),
                    primary_address.get("city", ""),
                    primary_address.get("state", ""),
                    primary_address.get("postal_code", "")
                ]
                full_address = ", ".join([p for p in address_parts if p])
                
                phone = primary_address.get("telephone_number", "Phone not available")
                specialty_name = primary_taxonomy.get("desc", taxonomy_description or "General Practice")
                
                all_providers.append({
                    "name": doctor_name,
                    "specialty": specialty_name,
                    "clinic": provider.get("organization_name", ""),
                    "address": full_address or "Address not available",
                    "phone": phone,
                    "npi": result.get("number", ""),
                    "raw_data": combined_text
                })
            
            if azure_clients.openai_client and combined_text and len(all_providers) > 0:
                try:
                    providers_text = "\n".join([
                        f"{i+1}. {p['name']} - {p['specialty']} - {p['address']}"
                        for i, p in enumerate(all_providers[:20])
                    ])
                    
                    ranking_prompt = f"""Based on the patient's symptoms and assessment, rank these doctors from most to least appropriate:

Patient Symptoms/Assessment:
{combined_text}

Available Doctors:
{providers_text}

Respond with ONLY a comma-separated list of numbers (e.g., "3,1,5,2,4") representing the ranking order, where 1 is the first doctor listed, 2 is the second, etc. Return the top {search_limit} most appropriate doctors."""

                    response = azure_clients.openai_client.chat.completions.create(
                        model=azure_clients.openai_deployment,
                        messages=[
                            {"role": "system", "content": "You are a medical referral advisor. Rank doctors by how well they match the patient's needs."},
                            {"role": "user", "content": ranking_prompt}
                        ],
                        temperature=0.3,
                        max_tokens=100
                    )
                    
                    ranking_text = response.choices[0].message.content.strip()
                    ranked_indices = []
                    for num in ranking_text.split(","):
                        try:
                            idx = int(num.strip()) - 1
                            if 0 <= idx < len(all_providers):
                                ranked_indices.append(idx)
                        except:
                            pass
                    
                    if ranked_indices:
                        doctors = [all_providers[i] for i in ranked_indices if i < len(all_providers)][:search_limit]
                        remaining = [all_providers[i] for i in range(len(all_providers)) if i not in ranked_indices]
                        doctors.extend(remaining[:search_limit - len(doctors)])
                    else:
                        doctors = all_providers[:search_limit]
                    
                    print(f"AI ranked {len(doctors)} doctors")
                except Exception as e:
                    print(f"Error ranking doctors with AI: {e}")
                    doctors = all_providers[:search_limit]
            else:
                doctors = all_providers[:search_limit]
            
            for doctor in doctors:
                doctor.pop("raw_data", None)
        
        print(f"[DOCTORS] Returning {len(doctors)} doctors")
        
        if not doctors and taxonomy_description and taxonomy_description != "Family Medicine":
            print(f"[DOCTORS] No doctors found for {taxonomy_description}, trying Family Medicine fallback...")
            fallback_params = {
                "version": "2.1",
                "limit": min(search_limit * 2, 50),
                "taxonomy_description": "Family Medicine"
            }
            if search_state:
                fallback_params["state"] = search_state
            if search_city:
                fallback_params["city"] = search_city
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                fallback_response = await client.get(npi_url, params=fallback_params)
                fallback_response.raise_for_status()
                fallback_data = fallback_response.json()
            
            if fallback_data.get("result_count", 0) > 0:
                for result in fallback_data.get("results", [])[:search_limit]:
                    provider = result.get("basic", {})
                    addresses = result.get("addresses", [])
                    taxonomies = result.get("taxonomies", [])
                    
                    primary_address = addresses[0] if addresses else {}
                    primary_taxonomy = taxonomies[0] if taxonomies else {}
                    
                    doctor_name = ""
                    if provider.get("organization_name"):
                        doctor_name = provider["organization_name"]
                    else:
                        first_name = provider.get("first_name", "")
                        last_name = provider.get("last_name", "")
                        doctor_name = f"{first_name} {last_name}".strip()
                    
                    if not doctor_name:
                        continue
                    
                    address_parts = [
                        primary_address.get("address_1", ""),
                        primary_address.get("address_2", ""),
                        primary_address.get("city", ""),
                        primary_address.get("state", ""),
                        primary_address.get("postal_code", "")
                    ]
                    full_address = ", ".join([p for p in address_parts if p])
                    
                    phone = primary_address.get("telephone_number", "Phone not available")
                    specialty_name = primary_taxonomy.get("desc", "Family Medicine")
                    
                    doctors.append({
                        "name": doctor_name,
                        "specialty": specialty_name,
                        "clinic": provider.get("organization_name", ""),
                        "address": full_address or "Address not available",
                        "phone": phone,
                        "npi": result.get("number", "")
                    })
            
            print(f"[DOCTORS] Fallback returned {len(doctors)} doctors")
        
        if not doctors:
            print(f"[DOCTORS] No doctors found - result_count was {npi_data.get('result_count', 0)}")
            return {
                "doctors": [],
                "message": "No doctors found in NPI Registry"
            }
        
        return {
            "doctors": doctors,
            "total": len(doctors)
        }
    except httpx.HTTPError as e:
        print(f"HTTP error calling NPI Registry: {e}")
        return {
            "doctors": [],
            "message": f"Error connecting to NPI Registry: {str(e)}"
        }
    except Exception as e:
        print(f"Error loading doctors from NPI Registry: {e}")
        import traceback
        traceback.print_exc()
        return {
            "doctors": [],
            "message": f"Error loading doctors: {str(e)}"
        }




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
