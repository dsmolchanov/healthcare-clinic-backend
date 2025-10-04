# Healthcare Clinic Backend

FastAPI-based conversational AI backend for dental and medical clinics with WhatsApp integration, intelligent appointment scheduling, and HIPAA-compliant data handling.

## 🚀 Features

- **Multi-Channel Support**: WhatsApp, Web Widget, Voice
- **Intelligent Routing**: Dual-lane architecture (Direct Lane + LLM Lane)
- **Appointment Management**: Smart scheduling with conflict resolution
- **Multi-Clinic Support**: Federated architecture with organization isolation
- **HIPAA Compliance**: PHI encryption, audit logging, data retention
- **Memory System**: mem0 for context + Redis cache for performance
- **Calendar Integration**: Google Calendar, Outlook (coming soon)
- **Multi-Language**: Automatic language detection and response

## 📋 Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or Supabase account)
- Redis 7+
- Fly.io account (for deployment)

## 🛠️ Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/dsmolchanov/healthcare-clinic-backend.git
cd healthcare-clinic-backend
```

### 2. Install Dependencies

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Environment

Create a `.env` file:

```bash
# Database
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=xxx
SUPABASE_SERVICE_KEY=xxx
DATABASE_URL=postgresql://user:pass@host/db

# AI Services
OPENAI_API_KEY=sk-xxx

# WhatsApp (Evolution API)
EVOLUTION_API_URL=https://evolution-api-prod.fly.dev
EVOLUTION_API_KEY=xxx

# Redis
REDIS_URL=redis://localhost:6379

# Deployment
FLY_APP_NAME=healthcare-clinic-backend
```

### 4. Run Locally

```bash
# Start the API server
uvicorn app.main:app --reload --port 8000

# Start the WhatsApp worker (separate terminal)
python run_worker.py
```

## 🚢 Deployment

### Automatic Deployment (Recommended)

Push to `main` branch to trigger automatic deployment via GitHub Actions:

```bash
git add .
git commit -m "Your changes"
git push origin main
```

### Manual Deployment

```bash
# First time setup
fly auth login
fly launch

# Deploy
fly deploy
```

## 🧪 Testing

```bash
# Run all tests
pytest tests/

# Run specific test category
pytest tests/integration/
pytest tests/security/

# Run with coverage
pytest --cov=app --cov-report=html
```

## 📖 API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🏗️ Architecture

```
┌─────────────────────────────────────┐
│   WhatsApp / Web Widget / Voice     │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│      Evolution Webhook Handler       │
│    (app/api/evolution_webhook.py)   │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│       Message Router                 │
│   (Dual-lane architecture)          │
├─────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐ │
│  │ Direct Lane  │  │  LLM Lane    │ │
│  │ (FAQ/Price)  │  │ (Complex)    │ │
│  │ 100-300ms ⚡ │  │ <2s          │ │
│  └──────────────┘  └──────────────┘ │
└─────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│  Redis Cache + Supabase Database    │
└─────────────────────────────────────┘
```

## 📁 Project Structure

```
.
├── app/
│   ├── api/              # API endpoints
│   ├── services/         # Business logic
│   ├── memory/           # mem0 & conversation memory
│   ├── security/         # HIPAA compliance
│   └── workers/          # Background workers
├── tests/                # Test suites
├── scripts/              # Utility scripts
├── .github/workflows/    # CI/CD
└── fly.toml             # Fly.io config
```

## 🔐 Security

- All PHI is encrypted at rest (AES-256)
- De-identification before external API calls
- Immutable audit logging
- Role-based access control
- Rate limiting and DDoS protection

## 🤝 Contributing

1. Create a feature branch
2. Make your changes
3. Add tests
4. Submit a pull request

## 📝 License

Proprietary - All rights reserved

## 📧 Support

For issues or questions, please create a GitHub issue.

---

**Version**: 1.0.0
**Last Updated**: October 2025


## 🚀 Auto-Deployment

This repository is configured with GitHub Actions for automatic deployment to Fly.io. Every push to `main` or `master` branch will trigger a deployment.

### Deployment Status

Check the [Actions tab](https://github.com/dsmolchanov/healthcare-clinic-backend/actions) to see deployment status.



## 📦 Repository Structure

- **** - Main application code
  -  - API endpoints and webhooks
  -  - Business logic and integrations
  -  - Background workers
  -  - HIPAA compliance and PHI handling
- **** - Comprehensive test suite
- **** - Utility scripts
- **** - CI/CD pipelines

