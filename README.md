# BSB Polymer Voice AI Agent

An AI-powered voice agent system for **BSB Fortrade Private Limited** - a tech-driven polymer distribution platform based in Ahmedabad, Gujarat, India.

## Overview

This project implements an intelligent voice agent (named **Monica/Mohini**) that handles incoming customer calls for BSB Polymer. The AI agent:

- Greets customers professionally in Hindi/Hinglish
- Collects customer information (name, company type, product, polymer requirements)
- Qualifies leads and captures requirements
- Provides product pricing and catalog information
- Guides customers to download the BSB Polymer App
- Transfers qualified leads to human Relationship Managers (RMs)

## System Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Tata Smartflo  │────▶│   FastAPI API   │────▶│   Groq LLM      │
│   (IVR/PSTN)    │     │   (Backend)     │     │  (AI Brain)     │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │Database  │ │Deepgram  │ │ Deepgram │
              │(SQLite)  │ │  (STT)   │ │  (TTS)   │
              └──────────┘ └──────────┘ └──────────┘
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend** | FastAPI (Python) |
| **LLM** | Groq (llama-3.3-70b-versatile) |
| **Speech-to-Text** | Deepgram (nova-2) |
| **Text-to-Speech** | Deepgram (aura-asteria-en) |
| **Database** | SQLite + SQLAlchemy |
| **IVR Integration** | Tata Smartflo |
| **Tunneling** | ngrok |

## Project Structure

```
Project/
├── app/
│   ├── api/
│   │   └── endpoints.py         # API routes for chat
│   ├── core/
│   │   ├── bsb_knowledge.md     # BSB business knowledge base
│   │   ├── config.py            # Configuration & settings
│   │   ├── database.py         # Database connection
│   │   └── models.py           # SQLAlchemy models
│   ├── services/
│   │   ├── ai_agent.py         # LLM integration (Groq)
│   │   ├── language_handler.py # Language normalization
│   │   ├── orchestrator.py     # Conversation flow manager
│   │   ├── pricing.py          # Pricing engine
│   │   ├── router.py           # Intent classification
│   │   ├── sarvam_service.py   # Additional AI service
│   │   ├── state.py            # Conversation state
│   │   ├── tts_service.py      # Text-to-speech
│   │   ├── validator.py        # Input validation
│   │   └── voice_stream_service.py # Real-time voice handling
│   └── static/
│       ├── audio/              # Generated TTS audio files
│       └── index.html          # Web interface
├── scripts/
│   ├── check_db.py            # Database utilities
│   ├── check_public.py        # ngrok URL checker
│   ├── generate_audio.py      # Audio generation
│   ├── seed_catalog.py        # Product catalog seeder
│   ├── start_tunnel.py        # ngrok startup
│   ├── test_ngrok.py          # ngrok testing
│   ├── test_websocket.py      # WebSocket testing
│   ├── verify_integration.py  # Integration tests
│   ├── verify_models.py       # Model verification
│   └── verify_rag_persistence.py # RAG tests
├── tests/
│   └── verify_flow.py         # Flow verification
├── .env                       # Environment variables
├── .gitignore
├── pyrightconfig.json
├── TATA_IVR_CONFIG.md        # IVR configuration
└── bsbinfotrade_bsb_prime_demo.sql  # Database schema
```

## Features

### 1. Voice Conversation Flow
The system manages a 7-stage conversation pipeline:
1. **Greeting** - Welcome message and initial contact
2. **Name Collection** - Capture customer's name
3. **Pitch** - Brief intro about BSB services
4. **Qualification** - Collect product, polymer type, grade, quantity, location
5. **App Offer** - Promote BSB Polymer App
6. **WhatsApp Confirm** - Confirm contact details
7. **Completed** - Warm closing and handoff

### 2. AI Capabilities
- **Multi-language Support**: Hindi, Gujarati, English (Hinglish)
- **Intent Detection**: Classifies user intents (ASK_PRICE, MOVE_FORWARD, etc.)
- **Semantic Caching**: Reduces API calls for repeated queries
- **Dynamic Context**: RAG-based conversation history management

### 3. Product Catalog
- **Polymers**: PVC, HDPE, LDPE, LLDPE, PP, PET
- **Grades**: Multiple grade codes (SG-5, H030, etc.)
- **Brands**: Reliance, GAIL, Sigfied, IOCL, Chemplast, Finolex, etc.

### 4. Pricing Engine
- Real-time price calculation
- Location-based freight estimation
- Tax (18% GST) and margin calculations
- Dispatch price vs. delivered price

### 5. Lead Management
- Automatic lead capture and storage
- JSON dump for real-time backup
- Status tracking (New, Qualified, Converted)

## Installation

### Prerequisites
- Python 3.9+
- ngrok account and auth token

### Setup

1. **Clone and navigate to project**
   ```bash
   cd Desktop/Project
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate     # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   # Create .env file with:
   GROQ_API_KEY=your_groq_api_key
   DEEPGRAM_API_KEY=your_deepgram_api_key
   SARVAM_API_KEY=your_sarvam_api_key
   DATABASE_URL=sqlite:///./conversations.db
   ```

5. **Initialize database**
   ```bash
   python -c "from app.core.database import Base, engine; Base.metadata.create_all(bind=engine)"
   ```

6. **Start ngrok tunnel**
   ```bash
   python scripts/start_tunnel.py
   ```

7. **Run the application**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

## Configuration

### Environment Variables (`.env`)

| Variable | Description |
|----------|-------------|
| `PROJECT_NAME` | Project name (default: "Voice AI Pipeline") |
| `DATABASE_URL` | Database connection string |
| `GROQ_API_KEY` | Groq API key for LLM |
| `DEEPGRAM_API_KEY` | Deepgram API key for STT/TTS |
| `SARVAM_API_KEY` | Sarvam AI API key |

### Tata Smartflo IVR Configuration

See `TATA_IVR_CONFIG.md` for detailed IVR setup instructions including:
- Webhook URL configuration
- HTTP method settings
- Header configuration
- Payload format

## API Endpoints

### POST /api/chat
Process voice input and get AI response.

**Request:**
```json
{
  "user_id": 123,
  "message": "Hello, I want to know about PVC prices",
  "conversation_id": 1
}
```

**Response:**
```json
{
  "response": "Hello sir, welcome to BSB Polymer...",
  "state_updates": {
    "stage": "qualification"
  }
}
```

## Database Schema

### Key Tables

- **users** - User accounts
- **conversations** - Chat history and state
- **leads** - Captured lead information
- **grades** - Polymer types (PVC, HDPE, etc.)
- **brands** - Brand names
- **brand_grade_maps** - Brand-grade relationships
- **buyer_masters** - Buyer information
- **seller_masters** - Seller information
- **orders** - Order records
- **pricing_rules** - Location-based pricing
- **polymer_inventory** - Stock information

## BSB Business Knowledge

### Company Information
- **Head Office**: Ahmedabad, Gujarat
- **Founder**: Mr. Nalin Bhavsar
- **Business**: Tech-driven polymer distribution
- **Services**: Buying, Selling, Logistics, Finance, Global Sourcing, Customs Clearing, Warehousing

### Products & Pricing (Reference)
- **PVC**: Rs 60.25/kg
- **HDPE**: Rs 85-90/kg
- **LDPE**: Rs 88/kg
- **LLDPE**: Rs 85/kg
- **PP**: Rs 82/kg
- **PET**: Rs 82.50/kg

### Locations
- **Loading**: Mundra, Nhava Sheva, Bhiwandi, Ahmedabad, Rajkot, Surat, Morbi
- **Delivery**: Gujarat, Rajasthan, Maharashtra, MP, Punjab, Delhi

## Conversation Scripts

### Sample Dialogues

**Initial Greeting:**
> Agent: "Hello Sir, Monica this side. Welcome to BSB. May I know your good name sir?"

**Qualification:**
> Agent: "Aap Manufacturer ho ya Trader?"
> User: "Main manufacturer hun."
> Agent: "Ok, to aap kya product banate ho? Aur kaunsa polymer/grade use karte ho?"

**App Promotion:**
> Agent: "Kya aapne hamari BSB Polymer App download ki hai? App download karne par aapko dedicated RM allot kiya jayega..."

## Development Notes

### Key Files

- `app/services/orchestrator.py` - Main conversation flow logic
- `app/services/ai_agent.py` - LLM prompt and response handling
- `app/core/bsb_knowledge.md` - Business rules and pricing
- `app/services/pricing.py` - Price calculation engine

### Running Tests

```bash
# Verify integrations
python scripts/verify_integration.py

# Check database
python scripts/check_db.py

# Verify models
python scripts/verify_models.py
```

## Deployment

### Production Considerations
1. Use PostgreSQL instead of SQLite for production
2. Implement proper logging and monitoring
3. Set up rate limiting for API endpoints
4. Configure proper CORS settings
5. Set up SSL/TLS for HTTPS

### IVR Integration
The system is designed to integrate with Tata Smartflo IVR:
- Receive calls via webhook
- Stream audio in real-time
- Use μ-law (G.711) encoding at 8kHz
- Configure proper timeout settings

## License

Proprietary - BSB Fortrade Private Limited

## Support

For technical support, contact the development team.
