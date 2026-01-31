# OptionsTaxHub Project Instructions for GitHub Copilot

## Always Remember

## Project Overview

This is a full-stack web app for retail investors focused on tax optimization and options trading.

**Core MVP priority (order matters):**
1. Portfolio rebalancer: Users upload CSV exports (e.g., from Robinhood), parse positions, suggest tax-loss harvesting, rebalance to minimize taxes while maintaining risk profile.
2. Options strategy builder + simulator: Build strategies (straddles, iron condors, etc.), simulate P&L and tax impact (short-term vs. long-term gains, wash-sale rules).
3. Community watchlist sharing: Users share options watchlists, discuss strategies in threaded forum-style posts.

Target users: DIY retail traders interested in tax efficiency (e.g., avoiding short-term capital gains hits).

## Folder Structure
OptionsTaxHub/
├── frontend/               # Next.js app (App Router, TypeScript, Tailwind)
├── backend/                # FastAPI app
├── shared/                 # (optional) shared types/interfaces
├── docs/                   # documentation, tax rule references, etc.
├── .github/
│   └── instructions/
│       └── copilot-instructions.md
├── render.yaml             # Render Blueprint deployment
└── README.md

## Tech Stack & Conventions

- Monorepo structure: `/frontend` (Next.js 15+ App Router, TypeScript, Tailwind v4, dark mode), `/backend` (FastAPI + Python 3.12+, uvicorn)
- Frontend runs on `http://localhost:3000` (default `npm run dev`)
- Backend runs on `http://localhost:8080` (uvicorn --port 8080)
- Database: PostgreSQL (managed on Render); use SQLAlchemy or Prisma (if added later)
- API style: REST endpoints, JSON responses, Pydantic for models/validation
- Styling: Tailwind CSS utility-first, responsive, dark mode support
- Auth: MVP → no auth or simple session; plan for JWT or Supabase later
- Data sources: Yahoo Finance / Alpha Vantage APIs for options quotes (free tiers)
- AI/ML: Scikit-learn for rebalancing optimization (free/local); Hugging Face Transformers optional for text (e.g., post summaries) – keep 100% free/no paid APIs

## Coding Guidelines

- Always use TypeScript in frontend, strict typing.
- FastAPI: async endpoints where possible, proper error handling, enable CORS for `http://localhost:3000` during development.
- Prefer functional components + hooks in Next.js.
- Add comments for tax logic (e.g., wash-sale rules, 2026 capital gains brackets).
- Keep MVP lean: Start with CSV upload → parse → basic tax suggestions → export.
- Responses: Suggest code with explanations, use modern patterns, avoid deprecated features.

## Security & Legal

- This app is for educational/simulation use only – include prominent disclaimers ("not financial advice", "simulation only, not real trading").
- Security: Never store full portfolio data persistently in MVP; process CSV in-memory where possible. Only store user preferences or aggregated stats if needed later.
- When suggesting code, reference localhost ports, monorepo paths, and stack above.

## Quick Local Start Commands

```bash
# Terminal 1 – Frontend
cd frontend && npm run dev
# → http://localhost:3000

# Terminal 2 – Backend
cd backend && uvicorn main:app --reload --port 8080
# → http://localhost:8080