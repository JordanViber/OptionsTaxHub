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

Subscribe, unsubscribe, list, and test require a signed-in Supabase user. The
server stores each endpoint with that user's id in `push_subscriptions`.
Apply `server/migrations/013_push_subscriptions.sql` before enabling push in
an environment. Do not apply it to a database you have not chosen, and do not
apply it from this repository's automated jobs.

No route returns endpoint URLs or encryption keys. `GET /push/subscriptions`
returns only the caller's subscription ids and timestamps. `POST /push/test`
delivers to the caller's own devices. Pass `{ "endpoint": "<the browser's PushSubscription endpoint>" }`
to target the current device, or `{}` to target every device that account owns.
A signed-out visitor cannot subscribe; the client leaves notifications off.

`POST /push/send` is the broadcast. It stays disabled until the API process
has `PUSH_ADMIN_KEY` set to at least 32 ASCII characters. The caller sends
that value in the `X-Push-Admin-Key` header. Generate it outside the repo
(for example `openssl rand -base64 32`) and put it only in the server
environment you intend to broadcast from, such as the Render dashboard for
that one service. Do not commit a value, do not add it to `render.yaml`, and
do not put it in a `NEXT_PUBLIC_*` variable or browser code. Messages are
kept for up to a day (`TTL` 86400) so an offline device can still receive a
deadline reminder.

#### 1. Generate VAPID Keys

```bash
pip install pywebpush
python3 -c "from pywebpush import vapid; print(vapid.Vapid().generate_keys())"
```

Add the keys to the server environment:

```
VAPID_PUBLIC_KEY=your_public_key_here
VAPID_PRIVATE_KEY=your_private_key_here
VAPID_CLAIM_EMAIL=your_email@example.com
```

#### 2. Send a Tax Deadline Reminder

Call `POST /push/send` from a server-side job with `X-Push-Admin-Key`.
Do not call the broadcast endpoint from browser code. The list route does
not return endpoint URLs, so a device test is `POST /push/test` from the
signed-in browser that already holds the `PushSubscription`.

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
1. Sign in, then grant notification permission when prompted
2. Call `POST /push/test` with that user's bearer token
3. Confirm the notification reaches only that account's devices, including when the app is closed

## Icons

Current icons are SVG placeholders. To replace with custom designs:

1. Create PNG icons (192x192 and 512x512)
2. Replace files in `/public/icons/`
3. Update `manifest.json` to use `.png` instead of `.svg`

## Environment Variables

Add to the frontend `.env.local`:
```
NEXT_PUBLIC_VAPID_PUBLIC_KEY=your_public_key_here
NEXT_PUBLIC_API_URL=http://localhost:8011
```

`PUSH_ADMIN_KEY` is not a frontend variable and is not listed here with a
value. Set it only on the server when you are ready to allow broadcasts.

## Production Checklist

- [ ] Generate production VAPID keys
- [ ] Apply `server/migrations/013_push_subscriptions.sql` to the database you intend to use
- [ ] Set a server-only `PUSH_ADMIN_KEY` of at least 32 characters before enabling broadcasts
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
