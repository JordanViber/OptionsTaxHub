# Supabase Setup Guide

## Local Development Setup

### 1. Create Supabase Project
1. Go to [supabase.com](https://supabase.com) and sign up
2. Click **New Project**
3. Fill in project details:
   - **Name:** OptionsTaxHub
   - **Password:** (save this securely)
   - **Region:** Choose closest to you (e.g., us-east-1)
4. Wait for project to initialize (1-2 minutes)

### 2. Get Your Keys
1. Go to **Settings** → **API**
2. Copy:
   - **Project URL** → `NEXT_PUBLIC_SUPABASE_URL`
   - **anon public** key → `NEXT_PUBLIC_SUPABASE_ANON_KEY`

### 3. Update Local Environment
1. Copy `.env.local.example` to `.env.local`:
   ```bash
   cp client/.env.local.example client/.env.local
   ```

2. Fill in your Supabase keys:
   ```
   NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key-here
   ```

### 4. Test Locally
1. Start the dev server: `npm run dev` (client directory)
2. Navigate to http://localhost:3000
3. You'll be redirected to `/auth/signin`
4. Click "Create Account" to sign up
5. Check your email for confirmation link
6. Sign in and you should see the app dashboard

## Production Deployment (Render)

### 1. Add to Render Dashboard

#### For Production Backend:
1. Go to your **options-tax-hub-server-prod** service
2. **Environment** tab → **Add Environment Variable**
3. Add your Supabase URL and keys (same as local)

#### For Production Frontend:
1. Go to your **options-tax-hub-client-prod** service
2. **Environment** tab → **Add Environment Variables**
3. Add:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`

### 2. Supabase Production Setup

#### Configure Redirect URLs
1. Go to Supabase Dashboard → **Authentication** → **URL Configuration**
2. Add these redirect URLs:
   ```
   http://localhost:3000/auth/signin
   https://options-tax-hub-client-prod.onrender.com/auth/signin
   https://options-tax-hub-client-staging.onrender.com/auth/signin
   ```

#### Enable Email Provider (Optional)
1. **Authentication** → **Providers**
2. Email/Password is enabled by default
3. For custom emails, configure SMTP in **Settings** → **Email Templates**

## Features Implemented

✅ **Sign Up** - Create new account with email/password
✅ **Sign In** - Login with email/password
✅ **Sign Out** - Logout from app
✅ **Auth Context** - Global auth state across app
✅ **Protected Routes** - Only authenticated users see dashboard
✅ **User Profile** - Display user email in header
✅ **Environment Variables** - Local and deployed configs

## Next Steps

1. Once Supabase is configured, users can create accounts
2. Build out user preferences table for tax settings
3. Add portfolio storage linked to user accounts
4. Implement real-time sync with Supabase Realtime

## Troubleshooting

### "Missing Supabase environment variables"
- Make sure `.env.local` has both keys
- Restart dev server after adding env vars

### Can't sign up / "Invalid credentials"
- Check Supabase Authentication is enabled
- Verify email matches your Supabase project settings

### Redirect loop on deployed version
- Confirm redirect URLs are added to Supabase
- Check `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are set in Render

## Resources
- [Supabase Docs](https://supabase.com/docs)
- [Supabase Auth](https://supabase.com/docs/guides/auth)
- [Next.js Auth Integration](https://supabase.com/docs/guides/auth/server-side/nextjs)
