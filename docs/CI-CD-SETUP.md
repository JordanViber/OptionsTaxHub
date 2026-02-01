# CI/CD Pipeline Setup Guide

## Overview
This project uses GitHub Actions for automated testing, security scanning, and deployment - replicating enterprise CI/CD pipelines for free.

## Components

### 1. **Continuous Integration (CI)**
- **Location**: `.github/workflows/ci.yml`
- **Triggers**: Push/PR to `main` or `staging`
- **Jobs**:
  - Backend tests with pytest + coverage
  - Frontend tests with Jest + coverage
  - E2E tests with Playwright
  - Code linting (flake8, ESLint)
  - Security scanning (Bandit, Trivy, Snyk)
  - Code quality analysis (SonarCloud)
  - Build verification

### 2. **Continuous Deployment (CD)**
- **Location**: `.github/workflows/deploy.yml`
- **Triggers**: Push to `main` or `staging`
- **Action**: Automatically deploys to corresponding Render environment

### 3. **Dependency Management**
- **Location**: `.github/dependabot.yml`
- **Function**: Automated dependency updates with security alerts

---

## Setup Instructions

### Step 1: Enable GitHub Actions
1. Go to your repo on GitHub
2. Click **Settings** → **Actions** → **General**
3. Enable **Allow all actions and reusable workflows**

### Step 2: Set Up SonarCloud (Free Code Quality)
1. Go to [SonarCloud](https://sonarcloud.io/)
2. Sign in with GitHub
3. Click **+** → **Analyze new project**
4. Select `OptionsTaxHub`
5. Copy the **SONAR_TOKEN**
6. Go to GitHub repo → **Settings** → **Secrets and variables** → **Actions**
7. Add secret: `SONAR_TOKEN` = (your token)
8. Update `sonar-project.properties` with your organization key

### Step 3: Set Up Codecov (Free Coverage Reports)
1. Go to [Codecov](https://codecov.io/)
2. Sign in with GitHub
3. Add `OptionsTaxHub` repository
4. Copy the **CODECOV_TOKEN**
5. Add to GitHub secrets: `CODECOV_TOKEN` = (your token)

### Step 4: Set Up Snyk (Free Security Scanning)
1. Go to [Snyk](https://snyk.io/)
2. Sign in with GitHub
3. Go to **Account Settings** → **API Token**
4. Copy the token
5. Add to GitHub secrets: `SNYK_TOKEN` = (your token)

### Step 5: Configure Render Deploy Hooks
1. Go to Render Dashboard
2. For each service (4 total), go to **Settings** → **Deploy Hook**
3. Copy the webhook URL
4. Add to GitHub secrets:
   - `RENDER_DEPLOY_HOOK_BACKEND_PROD`
   - `RENDER_DEPLOY_HOOK_FRONTEND_PROD`
   - `RENDER_DEPLOY_HOOK_BACKEND_STAGING`
   - `RENDER_DEPLOY_HOOK_FRONTEND_STAGING`

### Step 6: Enable Branch Protection (Optional but Recommended)
1. Go to repo → **Settings** → **Branches**
2. Add rule for `main`:
   - Require pull request reviews
   - Require status checks to pass before merging:
     - ✅ Backend Tests & Quality
     - ✅ Frontend Tests & Quality
     - ✅ E2E Tests
     - ✅ Security Scanning
   - Require branches to be up to date
   - Include administrators
3. Repeat for `staging` branch

---

## Pipeline Workflow

### On Pull Request to `staging` or `main`:
```
1. Lint checks (ESLint, flake8)
2. Unit tests (Jest, pytest)
3. Security scans (Bandit, Trivy, Snyk)
4. Code quality (SonarCloud)
5. Build verification
6. E2E tests (Playwright)
```

### On Push to `staging`:
```
All CI checks → Deploy to Render Staging
```

### On Push to `main`:
```
All CI checks → Deploy to Render Production
```

---

## What Each Tool Does

| Tool | Purpose | Free Tier |
|------|---------|-----------|
| **GitHub Actions** | CI/CD orchestration | 2,000 min/month (private), unlimited (public) |
| **SonarCloud** | Code quality & security | Unlimited for public repos |
| **Codecov** | Test coverage reports | Unlimited for public repos |
| **Snyk** | Dependency vulnerability scanning | Unlimited for open source |
| **Trivy** | Container/filesystem security scanning | Free |
| **Bandit** | Python security linting | Free |
| **Dependabot** | Automated dependency updates | Free |
| **flake8** | Python code linting | Free |
| **ESLint** | JavaScript/TypeScript linting | Free |

---

## Comparison to Enterprise Tools

| Enterprise Tool | Free Alternative | Coverage |
|----------------|------------------|----------|
| SonarQube | SonarCloud | ✅ Code quality, bugs, vulnerabilities |
| Twistlock | Trivy + Snyk | ✅ Container scanning, dependencies |
| Jenkins | GitHub Actions | ✅ CI/CD pipelines |
| Nexus/Artifactory | GitHub Packages | ✅ Artifact storage |
| JFrog Xray | Snyk + Dependabot | ✅ Dependency scanning |

---

## Monitoring CI/CD

### View Pipeline Status
- Go to **Actions** tab in GitHub
- See all workflow runs, logs, and artifacts

### View Coverage Reports
- [Codecov Dashboard](https://codecov.io/gh/JordanViber/OptionsTaxHub)

### View Code Quality
- [SonarCloud Dashboard](https://sonarcloud.io/project/overview?id=JordanViber_OptionsTaxHub)

### View Security Alerts
- GitHub repo → **Security** tab
- Dependabot alerts
- Code scanning alerts

---

## Local Testing

Test CI pipeline locally before pushing:

### Backend
```bash
cd server
pip install pytest pytest-cov flake8 bandit safety
flake8 .
bandit -r .
safety check
pytest --cov=.
```

### Frontend
```bash
cd client
npm run lint
npm test
npm run build
npm run test:e2e
```

---

## Troubleshooting

### CI Fails on GitHub but Works Locally
- Check Python/Node versions match
- Ensure all dependencies are in requirements.txt/package.json
- Check environment variables are set in GitHub secrets

### Coverage Not Uploading
- Verify `CODECOV_TOKEN` is set
- Check coverage file paths in workflow

### SonarCloud Not Working
- Verify `SONAR_TOKEN` is set
- Check `sonar-project.properties` configuration
- Ensure organization key is correct

---

## Cost Optimization

All tools used are **100% free** for:
- Public repositories (unlimited)
- Private repositories (within free tier limits)

GitHub Actions free tier (private repos):
- 2,000 minutes/month
- Our pipeline uses ~15 min/run
- = ~130 runs/month free

To optimize:
- Use caching (already configured)
- Run E2E tests only on staging/main pushes
- Skip some checks on draft PRs
