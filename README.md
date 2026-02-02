# OptionsTaxHub

A full-stack web application for retail investors focused on tax optimization and options trading.

## 🚀 Quick Start

### Prerequisites
- Node.js 18+ (for frontend)
- Python 3.9+ (for backend)
- npm or yarn

### Installation

```bash
# Install dependencies for both client and server
npm install

# Install client dependencies
cd client && npm install

# Install server dependencies
cd ../server && pip install -r requirements.txt
```

### Running Development Servers

```bash
# Run both frontend and backend concurrently (from root)
npm run dev

# Or run separately:

# Terminal 1 - Frontend (http://localhost:3000)
npm run dev:client

# Terminal 2 - Backend (http://localhost:8080)
npm run dev:server
```

## 🧪 Testing

### Run All Tests
```bash
# From root - runs all tests (client + server)
npm test

# With coverage reports
npm run test:coverage

# Including E2E tests
npm run test:all
```

### Client Tests (Jest + Playwright)
```bash
cd client

# Unit tests
npm test

# Unit tests with coverage
npm run test:coverage

# Watch mode for development
npm run test:watch

# E2E tests (Playwright)
npm run test:e2e
```

### Server Tests (pytest)
```bash
cd server

# Run tests
npm test
# or
python -m pytest

# With coverage
npm run test:coverage
# or
python -m pytest --cov=. --cov-report=term-missing
```

### Current Test Coverage
- **Frontend**: 100% coverage (48 unit tests + 9 E2E tests)
- **Backend**: 100% coverage (9 tests)
- **Total**: 66 tests

## 📁 Project Structure

```
OptionsTaxHub/
├── client/                 # Next.js 14 frontend (TypeScript, Tailwind, Material UI)
│   ├── app/               # Next.js App Router pages
│   ├── lib/               # API utilities and helpers
│   ├── tests/
│   │   ├── unit/         # Jest unit tests
│   │   └── e2e/          # Playwright E2E tests
│   └── package.json
├── server/                # FastAPI backend (Python)
│   ├── main.py           # FastAPI application
│   ├── tests/            # pytest tests
│   ├── requirements.txt
│   └── package.json
├── docs/                  # Documentation
├── render.yaml           # Render deployment config
└── package.json          # Root package.json for monorepo scripts
```

## 🛠️ Tech Stack

### Frontend
- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS v4, Material UI v7+
- **State Management**: React Query v5+
- **Auth**: Supabase Authentication
- **Testing**: Jest, @testing-library/react, Playwright

### Backend
- **Framework**: FastAPI
- **Language**: Python 3.9+
- **Database**: Supabase PostgreSQL
- **Testing**: pytest, pytest-cov
- **Server**: uvicorn

### Features
- Progressive Web App (PWA) with offline support
- Web Push Notifications (VAPID)
- CSV portfolio upload and parsing
- Tax optimization suggestions

## 🔒 Security & Legal

**Important**: This application is for educational/simulation purposes only.
- Not financial advice
- Simulation only, not real trading
- Always consult a licensed financial advisor

## 📝 Development Guidelines

- Always use TypeScript in frontend with strict typing
- Follow ESLint rules (run `npm run lint` in client/)
- Maintain 100% test coverage for new features
- Add comments for tax logic (wash-sale rules, capital gains brackets)
- Use async endpoints in FastAPI where possible

## 🚢 Deployment

Deployed on Render.com (free tier):
- Frontend: Static site
- Backend: Web service

See [render.yaml](render.yaml) for configuration.

## 📄 License

This project is for educational purposes only.
