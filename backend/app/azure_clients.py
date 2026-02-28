import os
from typing import Optional
import azure.cognitiveservices.speech as speechsdk
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI


class AzureClients:
    
    def __init__(self):
        self.speech_key = os.getenv("AZURE_SPEECH_KEY")
        self.speech_region = os.getenv("AZURE_SPEECH_REGION", "eastus")
        
        if not self.speech_key:
            print("WARNING: AZURE_SPEECH_KEY not found in environment variables")
        
        endpoint_raw = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.openai_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.openai_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        self.openai_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        
        if endpoint_raw:
            if '/openai/deployments' in endpoint_raw:
                print("WARNING: AZURE_OPENAI_ENDPOINT contains full API path. Extracting base endpoint...")
                from urllib.parse import urlparse
                parsed = urlparse(endpoint_raw)
                self.openai_endpoint = f"{parsed.scheme}://{parsed.netloc}/"
                print(f"Extracted base endpoint: {self.openai_endpoint}")
                
                if '/deployments/' in endpoint_raw:
                    parts = endpoint_raw.split('/deployments/')
                    if len(parts) > 1:
                        deployment_from_url = parts[1].split('/')[0].split('?')[0]
                        if not os.getenv("AZURE_OPENAI_DEPLOYMENT"):
                            self.openai_deployment = deployment_from_url
                            print(f"Extracted deployment name from URL: {self.openai_deployment}")
            else:
                self.openai_endpoint = endpoint_raw.rstrip('/')
        else:
            self.openai_endpoint = None
        
        if not self.openai_api_key:
            print("WARNING: AZURE_OPENAI_API_KEY not found in environment variables")
        if not self.openai_endpoint:
            print("WARNING: AZURE_OPENAI_ENDPOINT not found in environment variables")
        
        self.text_analytics_endpoint = os.getenv("AZURE_TEXT_ANALYTICS_ENDPOINT")
        self.text_analytics_key = os.getenv("AZURE_TEXT_ANALYTICS_KEY")
        
        self._speech_config = None
        self._openai_client = None
        self._text_analytics_client = None
    
    @property
    def speech_config(self) -> Optional[speechsdk.SpeechConfig]:
        try:
            if not self._speech_config and self.speech_key:
                self._speech_config = speechsdk.SpeechConfig(
                    subscription=self.speech_key,
                    region=self.speech_region
                )
            return self._speech_config
        except Exception as e:
            print(f"Error creating Speech config: {e}")
            return None
    
    @property
    def openai_client(self) -> Optional[AzureOpenAI]:
        import sys
        sys.stdout.flush()
        try:
            if not self._openai_client:
                print("\n" + "="*60, flush=True)
                print("=== ATTEMPTING OPENAI CLIENT INITIALIZATION ===", flush=True)
                print("="*60, flush=True)
                print(f"Endpoint value: {self.openai_endpoint}")
                print(f"API Key present: {bool(self.openai_api_key)}")
                print(f"API Key length: {len(self.openai_api_key) if self.openai_api_key else 0}")
                print(f"Deployment: {self.openai_deployment}")
                print(f"API Version: {self.openai_api_version}")
                
                if not self.openai_endpoint:
                    print("ERROR: AZURE_OPENAI_ENDPOINT is not set")
                    return None
                if not self.openai_api_key:
                    print("ERROR: AZURE_OPENAI_API_KEY is not set")
                    return None
                
                endpoint_clean = self.openai_endpoint.rstrip('/')
                print(f"Initializing OpenAI client with endpoint: {endpoint_clean}")
                print(f"Using deployment: {self.openai_deployment}, API version: {self.openai_api_version}")
                
                try:
                    self._openai_client = AzureOpenAI(
                        api_version=self.openai_api_version,
                        azure_endpoint=endpoint_clean,
                        api_key=self.openai_api_key
                    )
                    print("SUCCESS: OpenAI client initialized successfully!")
                except Exception as init_error:
                    print(f"FAILED to create AzureOpenAI client: {init_error}")
                    print(f"Error type: {type(init_error).__name__}")
                    import traceback
                    traceback.print_exc()
                    return None
            else:
                print("OpenAI client already initialized (reusing existing)")
            return self._openai_client
        except Exception as e:
            print(f"ERROR in openai_client property: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    @property
    def text_analytics_client(self) -> Optional[TextAnalyticsClient]:
        if not self._text_analytics_client and self.text_analytics_endpoint and self.text_analytics_key:
            credential = AzureKeyCredential(self.text_analytics_key)
            self._text_analytics_client = TextAnalyticsClient(
                endpoint=self.text_analytics_endpoint,
                credential=credential
            )
        return self._text_analytics_client
    
    def transcribe_audio(self, audio_data: bytes, language: str = "en-US") -> str:
        if not self.speech_config:
            raise ValueError("Azure Speech service not configured")
        
        if len(audio_data) < 1000:
            raise ValueError("Audio file is too short. Please record at least 1-2 seconds of audio.")
        
        import io
        import wave
        
        sample_rate = 16000
        channels = 1
        bits_per_sample = 16
        
        try:
            audio_io = io.BytesIO(audio_data)
            with wave.open(audio_io, 'rb') as wav_file:
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                bits_per_sample = wav_file.getsampwidth() * 8
                frames = wav_file.getnframes()
                print(f"WAV file detected: {sample_rate}Hz, {channels} channel(s), {bits_per_sample}bit, {frames} frames")
                
                audio_io.seek(0)
                audio_data = audio_io.read()
        except Exception as e:
            print(f"Not a standard WAV file or error reading: {e}")
            pass
        
        try:
            stream_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=sample_rate,
                bits_per_sample=bits_per_sample,
                channels=channels
            )
        except Exception as e:
            print(f"Error creating stream format: {e}, using defaults")
            stream_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
        
        push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        
        pcm_data = audio_data
        if audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE':
            try:
                audio_io = io.BytesIO(audio_data)
                with wave.open(audio_io, 'rb') as wav_file:
                    pcm_data = wav_file.readframes(wav_file.getnframes())
                    print(f"Extracted {len(pcm_data)} bytes of PCM data from WAV file")
            except Exception as e:
                print(f"Error extracting PCM from WAV: {e}, using raw data")
                pcm_data = audio_data
        else:
            pcm_data = audio_data
        
        chunk_size = 4096
        audio_io = io.BytesIO(pcm_data)
        bytes_written = 0
        while True:
            chunk = audio_io.read(chunk_size)
            if not chunk:
                break
            push_stream.write(chunk)
            bytes_written += len(chunk)
        
        print(f"Wrote {bytes_written} bytes to audio stream")
        push_stream.close()
        
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=self.speech_config,
            audio_config=audio_config,
            language=language
        )
        
        print("Starting speech recognition...")
        result = recognizer.recognize_once_async().get()
        print(f"Recognition result reason: {result.reason}")
        
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = result.text.strip()
            if not text:
                raise ValueError("Speech was recognized but no text was returned. Please try speaking more clearly.")
            print(f"Recognized text: {text}")
            return text
        elif result.reason == speechsdk.ResultReason.NoMatch:
            no_match_details = speechsdk.NoMatchDetails(result)
            error_msg = (
                "No speech could be recognized. Please try:\n"
                "- Speaking clearly and loudly\n"
                "- Recording for at least 2-3 seconds\n"
                "- Ensuring your microphone is working\n"
                "- Reducing background noise\n"
                f"Reason: {no_match_details.reason}"
            )
            raise ValueError(error_msg)
        elif result.reason == speechsdk.ResultReason.Canceled:
            cancellation = speechsdk.CancellationDetails(result)
            error_msg = f"Speech recognition canceled: {cancellation.reason}"
            if cancellation.reason == speechsdk.CancellationReason.Error:
                error_msg += f"\nError details: {cancellation.error_details}"
            raise ValueError(error_msg)
        else:
            raise ValueError(f"Speech recognition failed with reason: {result.reason}")
    
    def start_continuous_recognition(self, callback, language: str = "en-US"):
        if not self.speech_config:
            raise ValueError("Azure Speech service not configured")
        
        import io
        import wave
        
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000,
            bits_per_sample=16,
            channels=1
        )
        
        push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=self.speech_config,
            audio_config=audio_config,
            language=language
        )
        
        def recognized_cb(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                text = evt.result.text.strip()
                if text:
                    callback("final", text)
            elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                pass
        
        def recognizing_cb(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
                text = evt.result.text.strip()
                if text:
                    callback("interim", text)
        
        def canceled_cb(evt):
            callback("error", f"Recognition canceled: {evt.reason}")
        
        recognizer.recognized.connect(recognized_cb)
        recognizer.recognizing.connect(recognizing_cb)
        recognizer.canceled.connect(canceled_cb)
        
        recognizer.start_continuous_recognition_async()
        
        return recognizer, push_stream
    
    def extract_health_entities(self, text: str) -> dict:
        if not self.text_analytics_client:
            raise ValueError("Text Analytics service not configured")
        
        documents = [text]
        result = self.text_analytics_client.analyze_healthcare_entities(documents)
        
        docs = [doc for doc in result if not doc.is_error]
        if not docs:
            return {"entities": [], "relations": []}
        
        doc_result = docs[0]
        entities = []
        for entity in doc_result.entities:
            entities.append({
                "text": entity.text,
                "category": entity.category,
                "confidence": entity.confidence_score,
                "offset": entity.offset,
                "length": entity.length
            })
        
        relations = []
        for relation in doc_result.entity_relations:
            relations.append({
                "relation_type": relation.relation_type,
                "roles": [
                    {
                        "entity": role.entity.text,
                        "name": role.name
                    }
                    for role in relation.roles
                ]
            })
        
        return {"entities": entities, "relations": relations}
