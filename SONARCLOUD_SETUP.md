# SonarCloud Quality Gate Setup

Your CI pipeline now sends coverage reports to SonarCloud. To make it fail the build when quality issues are found, you need to set up a **Quality Gate**.

## Steps to Configure Quality Gate

1. **Go to SonarCloud Dashboard**
   - https://sonarcloud.io/organizations/jordanviber

2. **Select your project**: OptionsTaxHub

3. **Navigate to Quality Gate** (in project settings)
   - Project → Project Settings → Quality Gate

4. **Set up conditions** (recommended thresholds):
   ```
   ✅ New Issues > 0                     → FAIL
   ✅ Code Coverage < 80%                → FAIL
   ✅ Duplications > 3%                  → FAIL
   ✅ Security Hotspots Reviewed < 100%  → FAIL
   ```

5. **Make it the Default Quality Gate**
   - After creating, mark it as "Default" so all projects use it

## Expected Behavior

After setup:
- ✅ SonarCloud scans will now **fail your PR** if:
  - New code introduces quality issues
  - Code coverage drops below 80%
  - Duplicated code exceeds 3%
  - Security issues aren't reviewed

- ✅ PR merge will be blocked until issues are resolved
- ✅ You'll have full confidence in code quality before production

## CI Pipeline Integration

Your workflow now:
1. Runs unit/integration tests
2. Generates coverage reports (lcov.info, coverage.xml)
3. Uploads coverage to SonarCloud
4. Scans code with SonarCloud
5. **Enforces Quality Gate** (once you create it above)

## Coverage Report Locations

The workflow passes these to SonarCloud:
- **Frontend**: `client/coverage/lcov.info`
- **Backend**: `server/coverage.xml`

## Token Requirements

Your SONAR_TOKEN secret is required. Verify it exists:
- GitHub → Settings → Secrets and variables → Actions
- Should have: `SONAR_TOKEN`

If missing:
1. Generate token: https://sonarcloud.io/account/security/
2. Add to repo: GitHub Settings → New repository secret
