# GitHub Actions CI/CD Setup Guide

## Step 1: Get Render Webhook URLs

### For Production Services:

**Backend (options-tax-hub-server-prod):**
1. Go to [Render Dashboard](https://dashboard.render.com)
2. Select **options-tax-hub-server-prod**
3. Click **Settings** tab
4. Scroll down to **Deploy Hooks**
5. Click **"Create new hook"**
6. Select **GitHub** as the provider
7. Copy the generated URL

**Frontend (options-tax-hub-client-prod):**
8. Repeat steps 2-7 for **options-tax-hub-client-prod** service
9. Copy the generated URL

### For Staging Services:

**Backend (options-tax-hub-server-staging):**
1. Select **options-tax-hub-server-staging**
2. Follow steps 3-7 above
3. Copy the generated URL

**Frontend (options-tax-hub-client-staging):**
1. Select **options-tax-hub-client-staging**
2. Follow steps 3-7 above
3. Copy the generated URL

## Step 2: Add GitHub Secrets

### Via GitHub UI:
1. Go to your repository: `https://github.com/YOUR_USERNAME/OptionsTaxHub`
2. Click **Settings** tab
3. Left sidebar → **Secrets and variables** → **Actions**
4. Click **"New repository secret"** for each:

**Add these 4 secrets:**
- Name: `RENDER_DEPLOY_HOOK_BACKEND_PROD`
  - Value: (paste backend production webhook from Step 1)

- Name: `RENDER_DEPLOY_HOOK_FRONTEND_PROD`
  - Value: (paste frontend production webhook from Step 1)

- Name: `RENDER_DEPLOY_HOOK_BACKEND_STAGING`
  - Value: (paste backend staging webhook from Step 1)

- Name: `RENDER_DEPLOY_HOOK_FRONTEND_STAGING`
  - Value: (paste frontend staging webhook from Step 1)

### Via GitHub CLI (alternative):
```bash
gh secret set RENDER_DEPLOY_HOOK_BACKEND_PROD --body "YOUR_WEBHOOK_URL"
gh secret set RENDER_DEPLOY_HOOK_FRONTEND_PROD --body "YOUR_WEBHOOK_URL"
gh secret set RENDER_DEPLOY_HOOK_BACKEND_STAGING --body "YOUR_WEBHOOK_URL"
gh secret set RENDER_DEPLOY_HOOK_FRONTEND_STAGING --body "YOUR_WEBHOOK_URL"
```

## Step 3: Verify Your Workflows

### Test the Pipeline:
1. Go to **GitHub repo** → **Actions** tab
2. You should see:
   - **CI Pipeline** - runs on every push/PR
   - **Deploy to Render** - runs only on `main` or `staging` branch pushes

### Make a test commit to trigger CI:
```bash
git add .
git commit -m "test: trigger CI pipeline"
git push origin main
```

### Check the workflow:
1. Go to **Actions** tab
2. Click the latest workflow run
3. View **Backend Tests & Quality** and **Frontend Tests & Quality** jobs
4. Once CI passes, **Deploy to Render** job should automatically trigger

## What Each Workflow Does

### CI Pipeline (ci.yml)
**Runs on:** Every push to main/staging + all pull requests

**Backend Tests:**
- ✅ Python 3.11 setup
- ✅ Dependency installation
- ✅ Flake8 linting (code style)
- ✅ Bandit security scan
- ✅ Safety check (vulnerability scanner)
- ✅ Pytest with coverage
- ✅ Codecov upload
- ✅ SonarCloud analysis

**Frontend Tests:**
- ✅ Node.js 18 setup
- ✅ Dependency installation
- ✅ ESLint linting
- ✅ Jest unit tests
- ✅ Build check
- ✅ E2E tests (Playwright)

### Deploy Pipeline (deploy.yml)
**Runs on:** Push to main or staging branch (only after CI passes)

**Automatic Deployment:**
- Detects if push is to `main` (production) or `staging`
- Triggers appropriate Render webhooks
- Backend and frontend deploy simultaneously
- Sends deployment notification

## Troubleshooting

### "Deploy failed - webhook not found"
- Double-check the webhook URL in GitHub Secrets
- Make sure the secret name matches exactly (case-sensitive)
- Regenerate the webhook in Render if unsure

### "CI tests are failing"
- Click the failing job in Actions tab
- Scroll through logs to find the error
- Fix locally and push again
- CI will automatically re-run

### "E2E tests timing out"
- Tests may be slow on CI - increase timeout in playwright.config.ts
- Or check if backend is properly responding

## Next Steps

1. ✅ Set up GitHub Secrets (above)
2. ✅ Make a test commit to trigger workflows
3. ✅ Monitor Actions tab for success
4. ✅ Once working, every push will auto-test and deploy!

## Resources
- [GitHub Actions Docs](https://docs.github.com/en/actions)
- [Render Deploy Hooks](https://render.com/docs/deploy-hooks)
- [GitHub Secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
