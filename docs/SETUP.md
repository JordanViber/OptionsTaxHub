# Development Setup Guide

## Prerequisites

### Windows & Mac
- **Git**: [Download](https://git-scm.com/downloads)
- **Node.js**: v18+ [Download](https://nodejs.org/)
- **Python**: v3.11+ [Download](https://www.python.org/downloads/)
- **VS Code**: [Download](https://code.visualstudio.com/)

---

## Initial Setup

### 1. Clone Repository
```bash
git clone https://github.com/JordanViber/OptionsTaxHub.git
cd OptionsTaxHub
```

### 2. Install VS Code Extensions
Open the project in VS Code and it will prompt you to install recommended extensions, or run:
```bash
code --install-extension ms-python.python
code --install-extension ms-playwright.playwright
# ... (see .vscode/extensions.json for full list)
```

### 3. Backend Setup (Python)

#### Install Dependencies
```bash
cd server
pip install -r requirements.txt
```

#### Configure Environment Variables
Copy `.env.example` to `.env.local` and update values:
```bash
cp server/.env.example server/.env.local
```

**VAPID Keys for Push Notifications:**
- Keys are already set in `server/.env.local` for local development
- See `.vapid-keys` file for reference keys (dev, staging, prod)
- Each environment (local, staging, prod) can have different keys
- The server's `VAPID_PUBLIC_KEY` must match client's `NEXT_PUBLIC_VAPID_PUBLIC_KEY`
```bash
npm start
# or
python main.py
```

Server runs at: `http://localhost:8080`

---

### 4. Frontend Setup (Next.js)

#### Install Dependencies
```bash
cd client
npm install
```

#### Configure Environment Variables
Copy `.env.example` to `.env.local` and update values:
```bash
cp client/.env.example client/.env.local
```

**VAPID Keys for Push Notifications:**
- Keys are already set in `client/.env.local` for local development
- The public key `NEXT_PUBLIC_VAPID_PUBLIC_KEY` must match server's `VAPID_PUBLIC_KEY`
```bash
npm run dev
```

Frontend runs at: `http://localhost:3000`

---

## Platform-Specific Notes

### Windows
- Use PowerShell or Git Bash
- Python might be `python` or `python3` depending on installation
- Use backslashes `\` for paths (or forward slashes `/` work in most cases)

### Mac/Linux
- Use Terminal or zsh
- Python is typically `python3`
- Use forward slashes `/` for paths
- May need to use `sudo` for global package installations

---

## Running Both Services

### Option 1: VS Code Tasks (Recommended)
Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac) and select:
- **Tasks: Run Task** → **Client: dev server**
- **Tasks: Run Task** → **Server: API**

### Option 2: Separate Terminals
**Terminal 1 (Backend):**
```bash
cd server
npm start
```

**Terminal 2 (Frontend):**
```bash
cd client
npm run dev
```

---

## VAPID Keys & Push Notifications

### What are VAPID Keys?
VAPID (Voluntary Application Server Identification) keys enable secure Web Push Notifications. They're a public/private key pair used to authenticate your server to the browser's push service.

### Key Setup

#### Local Development
- Keys are already generated and stored in `.env.local` files
- Both server and client use the **same public key**
- Server uses the **private key** to send notifications
- No additional setup needed

#### For Each Developer/Machine
- Each machine can have its own local keys (doesn't affect others)
- Keys are in `.gitignore` so they won't be committed
- Reference all keys in `.vapid-keys` file (not committed, for your reference)

#### Staging Environment (Render)
1. Go to Render Dashboard
2. For `options-tax-hub-server-staging`:
   - **Settings** → **Environment Variables**
   - Add:
     ```
     VAPID_PUBLIC_KEY=BEmKg-N2T2yG9oaqL3S9nM0pQ4wR8xS5yT6uV7wX8yZ9a0B1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV1wX2
     VAPID_PRIVATE_KEY=MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgLy8aNWsoH2NU/IT239ccztieFsEVqJBZB3WcYt2EroNCAASF5vTyNtsp7PaKWspH2NU/IT239ccztieFsEVqJBZB3WcYt2Erop
     ```
3. For `options-tax-hub-client-staging`:
   - **Settings** → **Environment Variables**
   - Add:
     ```
     NEXT_PUBLIC_VAPID_PUBLIC_KEY=BEmKg-N2T2yG9oaqL3S9nM0pQ4wR8xS5yT6uV7wX8yZ9a0B1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV1wX2
     ```

#### Production Environment (Render)
1. Follow the same steps as staging for `options-tax-hub-server-prod` and `options-tax-hub-client-prod`
2. Use the keys from `.vapid-keys` under the `PROD_` section

### Important Notes
- **Never commit `.env.local` files** - they're in `.gitignore`
- **Never commit `.vapid-keys` file** - it's in `.gitignore`
- Public key is safe to expose (`NEXT_PUBLIC_` prefix)
- Private key must stay secret (server-side only)
- Both environments (server/client) must use matching public keys
- Different public keys between environments are OK (staging vs prod)

---

### Unit Tests (Frontend)
```bash
cd client
npm test
```

### E2E Tests (Playwright)
```bash
cd client
npm run test:e2e
```

### Backend Tests (Python)
```bash
cd server
pytest
```

---

## Common Issues

### Port Already in Use
- **Frontend (3000)**: Check if another Next.js app is running
- **Backend (8080)**: Kill the process using port 8080

**Windows:**
```powershell
netstat -ano | findstr :8080
taskkill /PID <PID> /F
```

**Mac/Linux:**
```bash
lsof -ti:8080 | xargs kill -9
```

### Python Module Not Found
```bash
pip install -r requirements.txt
```

### Node Modules Issues
```bash
rm -rf node_modules package-lock.json
npm install
```

---

## Development Workflow

1. **Create feature branch**: `git checkout -b feature/your-feature`
2. **Make changes** and test locally
3. **Commit**: `git commit -m "feat: your feature"`
4. **Push to staging**: `git push origin staging`
5. **Test on staging**: Wait for Render deployment
6. **Merge to main**: Create PR from `staging` to `main` for production

---

## Environment Variables Reference

### Backend (server/.env.local)
```env
DATABASE_URL=postgresql://user:password@localhost:5432/optionstaxhub
API_KEY_SECRET=your_secret_key_here
FRONTEND_URL=http://localhost:3000
PORT=8080
```

### Frontend (client/.env.local)
```env
NEXT_PUBLIC_API_URL=http://localhost:8080
```

---

## Useful Commands

### Git
```bash
git status                    # Check current status
git branch                    # List branches
git checkout main             # Switch to main
git pull origin main          # Update from remote
```

### NPM
```bash
npm install                   # Install dependencies
npm run dev                   # Run development server
npm run build                 # Build for production
npm start                     # Start production server
```

### Python
```bash
pip list                      # List installed packages
pip freeze > requirements.txt # Update requirements
python main.py                # Run server
```

---

## VS Code Shortcuts

### Cross-Platform
- **Command Palette**: `Ctrl+Shift+P` (Win) / `Cmd+Shift+P` (Mac)
- **Quick Open File**: `Ctrl+P` (Win) / `Cmd+P` (Mac)
- **Search in Files**: `Ctrl+Shift+F` (Win) / `Cmd+Shift+F` (Mac)
- **Toggle Terminal**: `` Ctrl+` `` (Win) / `` Cmd+` `` (Mac)
- **Run Task**: `Ctrl+Shift+B` (Win) / `Cmd+Shift+B` (Mac)

---

## Need Help?
- Check the [GitHub Issues](https://github.com/JordanViber/OptionsTaxHub/issues)
- Review the [Render Docs](https://render.com/docs)
- Consult [Next.js Docs](https://nextjs.org/docs) or [FastAPI Docs](https://fastapi.tiangolo.com/)
