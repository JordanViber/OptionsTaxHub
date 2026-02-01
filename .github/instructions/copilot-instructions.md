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
├── client/                 # Next.js 14 (App Router, TypeScript, Tailwind, Material UI)
├── server/                 # FastAPI + Python
├── docs/                   # Documentation
├── .github/
│   └── instructions/
│       └── copilot-instructions.md
├── render.yaml             # Render deployment configuration
├── test.csv                # Sample test data
└── README.md

## Tech Stack & Conventions

- Structure: `/client` (Next.js 14, TypeScript, Tailwind CSS v4, Material UI), `/server` (FastAPI + Python 3.9+, uvicorn)
- Frontend runs on `http://localhost:3000` (default `npm run dev`)
- Backend runs on `http://localhost:8080` (uvicorn main:app --reload)
- Database: Supabase PostgreSQL + Auth (free tier)
- API style: REST endpoints, JSON responses, Pydantic models for validation
- Frontend libraries: Material UI v7+ components, React Query v5+, Supabase client
- Styling: Tailwind CSS + Material UI theme system
- Auth: Supabase email/password authentication
- PWA: Service Worker for offline support, Web Push API for notifications
- Push Notifications: VAPID keys for Web Push Protocol authentication
- Deployment: Render.com (free tier) for both frontend and backend
- Data sources: Yahoo Finance / Alpha Vantage APIs for options quotes (free tiers)
- AI/ML: Scikit-learn for rebalancing optimization (free/local)

## Coding Guidelines

- Always use TypeScript in frontend, strict typing.
- FastAPI: async endpoints where possible, proper error handling, enable CORS for `http://localhost:3000` during development.
- Prefer functional components + hooks in Next.js.
- Add comments for tax logic (e.g., wash-sale rules, 2026 capital gains brackets).
- Keep MVP lean: Start with CSV upload → parse → basic tax suggestions → export.
- Responses: Suggest code with explanations, use modern patterns, avoid deprecated features.

## Code Quality & Testing

**Before completing any code changes:**
1. **Check for syntax errors** - Use `get_errors()` tool to validate TypeScript and JavaScript
2. **Check ESLint compliance** - Run ESLint checks for frontend code (Next.js, React)
3. **Fix all errors immediately** - Do not leave failing code; always resolve issues before finishing
4. **Type safety** - Ensure all TypeScript files pass strict type checking
5. **Import paths** - Use absolute imports with `@/` alias in Next.js instead of relative paths

**Before creating files:**
- Verify directory structure exists or create if needed
- Ensure proper TypeScript/JavaScript syntax
- Check for ESLint violations (no `any` types unless absolutely necessary)
- Validate imports reference correct paths

**When modifying existing files:**
- Always run `get_errors()` after editing to catch breaking changes
- Ensure no regression in existing functionality
- Maintain consistent code style with existing code
- Update related type definitions if changing function signatures

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
