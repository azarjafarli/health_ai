# Healthcare AI Assistant

A comprehensive AI-powered healthcare solution that combines **Health Diary Summarization** and **Clinical Note Cleaning** using Azure AI services.

## Features

### 🏥 Health Diary Summarizer
- Log symptoms, food, mood, or general health entries via text or voice
- AI-powered sentiment analysis
- Visual summaries and trend tracking over time
- Personalized health suggestions
- Interactive charts and statistics

### 📋 Clinical Note Cleaner
- Voice-to-text transcription using Azure Speech-to-Text
- Automatic conversion to structured SOAP format (Subjective, Objective, Assessment, Plan)
- Health entity extraction using Text Analytics for Health
- Clean, professional clinical documentation

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: Vanilla JavaScript, HTML5, CSS3
- **Azure Services**:
  - Azure Speech-to-Text
  - Azure OpenAI (GPT-4)
  - Text Analytics for Health
- **Visualization**: Plotly.js

## Setup Instructions

### 1. Backend Setup

```bash
cd backend
pip install -r requirements.txt
```

### 2. Environment Configuration

Create a `.env` file in the `backend/` directory:

```env

# Azure Speech Service
AZURE_SPEECH_KEY=your_azure_speech_key_here
AZURE_SPEECH_REGION=eastus

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your_azure_openai_api_key_here
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_DEPLOYMENT=gpt-4

# Azure Text Analytics for Health
AZURE_TEXT_ANALYTICS_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_TEXT_ANALYTICS_KEY=your_text_analytics_key_here
```

### 3. Start Backend Server

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`

### 4. Frontend Setup

Simply open `frontend/index.html` in a web browser, or serve it using a local server:

```bash
# Using Python
cd frontend
python -m http.server 8080

# Using Node.js
npx http-server -p 8080
```

Then navigate to `http://localhost:8080`

## API Endpoints

### Health Diary

- `POST /api/diary/entry` - Create a new diary entry
- `GET /api/diary/entries` - Get all diary entries
- `GET /api/diary/summary` - Get summary and trends
- `DELETE /api/diary/entries/{entry_id}` - Delete an entry

### Clinical Notes

- `POST /api/clinical/transcribe` - Transcribe audio to SOAP note
- `POST /api/clinical/text-to-soap` - Convert text to SOAP format

## Privacy & Security

⚠️ **Important**: This is a prototype. For production use:
- Implement proper authentication and authorization
- Use encrypted storage for sensitive data
- Ensure HIPAA compliance
- Never store real PHI (Protected Health Information) in development
- Use synthetic or anonymized data for testing

## Project Structure

```
.
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application
│   │   ├── azure_clients.py     # Azure service clients
│   │   ├── schemas.py           # Pydantic models
│   │   ├── pipeline.py          # Processing pipelines
│   │   └── utils_audio.py       # Audio utilities
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── README.md
```

## Usage Examples

### Health Diary Entry

1. Select entry type (symptom, food, mood, general)
2. Enter text or record audio
3. Submit to get sentiment analysis and suggestions
4. View trends and summaries in the dashboard

### Clinical Note

1. Record voice dictation or enter text
2. Submit to generate structured SOAP note
3. Review extracted health entities
4. Copy formatted note for documentation

## Judging Criteria Alignment

✅ **Innovativeness**: Combines multiple Azure AI services in a unified solution  
✅ **Impact/Value**: Saves time for clinicians and helps patients track health  
✅ **Sustainability**: Modular architecture, scalable design  
✅ **Prototype Quality**: Full-stack implementation with modern UI/UX  
✅ **Presentation**: Clean interface with clear visualizations  

### Bonus Points

✅ **Privacy & Security**: Designed with compliance in mind  
✅ **SOAP Notes**: Structured, professional clinical documentation  
✅ **Visualizations**: Interactive charts for health trends  
✅ **Azure Integration**: Comprehensive use of Azure AI services  

## License

This project is created for the GDE Hackathon.
