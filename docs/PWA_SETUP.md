# PWA Setup Guide

## Overview
OptionsTaxHub is now a Progressive Web App (PWA) with:
- ✅ Installable on mobile and desktop
- ✅ Offline capability with service worker
- ✅ Push notifications support
- ✅ Home screen icon
- ✅ Full mobile responsiveness

## Features

### 1. App Installation
- **Install Prompt**: Shows after 10 seconds (can be triggered earlier after user engagement)
- **Platforms**: Works on iOS Safari, Android Chrome, and desktop browsers
- **Icon**: Blue gradient with "$" symbol and "TAX" text

### 2. Push Notifications
- **Infrastructure**: Client-side setup complete with hooks for server integration
- **Backend Integration Required**: See backend setup below

### 3. Offline Support
- Service worker caches static assets
- Network-first strategy for API calls
- Cache-first for static resources

## Client Setup (Complete ✅)

No additional setup needed! The following are already configured:

- [x] Web manifest (`/public/manifest.json`)
- [x] Service worker (`/public/sw.js`)
- [x] PWA icons (SVG placeholders in `/public/icons/`)
- [x] Meta tags for mobile
- [x] Install prompt component
- [x] Service worker registration

## Backend Setup

### Push Notifications

The FastAPI backend persists subscriptions with their authenticated Supabase user ID.
The subscribe, unsubscribe, list, and test routes require a bearer access token.
Listing returns only subscription IDs and timestamps; endpoint URLs and encryption
keys stay server-side. A test send targets one endpoint only when it belongs to
the requesting user.

`POST /push/send` is reserved for server-side broadcasts and requires the
backend-only `PUSH_ADMIN_KEY` environment variable in the `X-Push-Admin-Key`
header. Never place that secret in browser code or a `NEXT_PUBLIC_*` variable.
Broadcasts stay disabled until the backend key is configured.

#### 1. Generate VAPID Keys

```bash
# Install web-push library
pip install pywebpush

# Generate VAPID keys (run once)
python3 -c "from pywebpush import vapid; print(vapid.Vapid().generate_keys())"
```

Add the keys to your `.env`:
```
VAPID_PUBLIC_KEY=your_public_key_here
VAPID_PRIVATE_KEY=your_private_key_here
VAPID_CLAIM_EMAIL=your_email@example.com
```

#### 2. Send a Tax Deadline Reminder

```python
# Call /push/send from a server-side job with X-Push-Admin-Key.
# Do not call the broadcast endpoint directly from browser code.
```

## Testing

### Test PWA Installation (Desktop)
1. Open Chrome DevTools → Application → Manifest
2. Click "Update" to refresh manifest
3. See install prompt or use Chrome's install button

### Test PWA Installation (Mobile)
1. Open site in mobile browser (Safari iOS or Chrome Android)
2. Wait for install prompt or use browser's "Add to Home Screen"
3. Install and open from home screen

### Test Push Notifications
1. Grant notification permission when prompted
2. Sign in and use `/push/test` with the bearer token and one of that user's
   subscription endpoint URLs
3. Confirm the notification reaches only that device

## Icons

Current icons are SVG placeholders. To replace with custom designs:

1. Create PNG icons (192x192 and 512x512)
2. Replace files in `/public/icons/`
3. Update `manifest.json` to use `.png` instead of `.svg`

## Environment Variables

Add to `.env.local`:
```
NEXT_PUBLIC_VAPID_PUBLIC_KEY=your_public_key_here
NEXT_PUBLIC_API_URL=http://localhost:8011
```

Set `PUSH_ADMIN_KEY` only in the backend environment (including Render) when
server-side broadcasts are needed. It is never a frontend variable.

## Production Checklist

- [ ] Generate production VAPID keys
- [x] Store owner-scoped push subscriptions in the database
- [ ] Configure a server-only `PUSH_ADMIN_KEY` before enabling broadcasts
- [ ] Add HTTPS (required for service workers)
- [ ] Replace placeholder icons with branded designs
- [ ] Test on real iOS and Android devices
- [ ] Set up push notification triggers for:
  - [ ] Tax deadlines
  - [ ] Portfolio rebalancing alerts
  - [ ] Wash sale warnings
  - [ ] Market events

## Browser Support

- ✅ Chrome/Edge (desktop & Android)
- ✅ Safari (iOS 16.4+, macOS)
- ✅ Firefox (desktop & Android)
- ⚠️ iOS: Limited background push (only works when app recently used)

## Troubleshooting

### Service Worker Not Updating
- Hard refresh (Cmd/Ctrl + Shift + R)
- Clear cache in DevTools → Application → Clear storage

### Install Prompt Not Showing
- Check DevTools → Application → Manifest for errors
- Ensure running on HTTPS (or localhost)
- May need to wait 30 seconds or refresh

### Push Notifications Not Working
- Check notification permission in browser settings
- Verify VAPID keys are correctly set
- Check browser console for errors
