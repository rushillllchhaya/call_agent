# Tata Smartflo IVR Configuration Guide

## Problem
Your backend is working perfectly:
- ✅ AI generates responses
- ✅ Deepgram TTS converts to audio
- ✅ Webhook returns audio URL

**But you only hear the default "Tata Tele tune"** instead of our AI audio.

## Root Cause
The **IVR flow on Tata Smartflo portal** is not configured to play the `audio_url` from our webhook response.

---

## What Our Backend Returns

When a call comes in, our webhook returns this JSON:

```json
{
  "response_text": "Hello! Welcome to BSB Polymer Trading. How can I help you today?",
  "audio_url": "https://faustino-postcruciate-unabortively.ngrok-free.dev/static/audio/2a001b281a5e809a9df422206afe88d8.mp3",
  "action": "continue"
}
```

**Proof from logs (latest call at 16:05)**:
```
2026-02-12 16:05:09,947 - webhook - INFO - Generated TTS Audio: https://faustino-postcruciate-unabortively.ngrok-free.dev/static/audio/2a001b281a5e809a9df422206afe88d8.mp3
```

✅ The audio file is accessible at that URL and plays correctly in a browser.

---

## Solution: Configure Tata Smartflo IVR

You need to update your **IVR Flow** in the Tata Smartflo portal to read and play the `audio_url` field.

### Step 1: Access Tata Smartflo Portal
1. Log in to https://smartflo.tatatelebusiness.com (or your portal URL)
2. Navigate to **IVR Designer** or **Call Flow**
3. Find your IVR flow for number **9228170743**

### Step 2: Configure Webhook Node

Your IVR flow should look something like:

```
[Incoming Call] → [Webhook API Call] → [Play Audio] → [Gather Speech] → [Loop]
```

#### Current Issue:
Your "Play Audio" node is probably playing a **static file** (the default tune) instead of reading the dynamic `audio_url` from the webhook response.

#### Fix:
Configure the "Play Audio" node to:
- **Source**: Dynamic URL from webhook response
- **Variable**: `$audio_url` or `{audio_url}` (depends on Tata's syntax)

### Step 3: Common IVR Configuration Patterns

Different platforms use different syntax. Try these:

#### Option A: Use Response Variable
```
Play Audio: ${audio_url}
```

#### Option B: Map to Variable
```
1. Webhook Node: Store response in variable $webhook_response
2. Play Audio Node: Use $webhook_response.audio_url
```

#### Option C: Fallback to Text-to-Speech
If `audio_url` is not available, fallback to their TTS:
```
1. Check if $audio_url exists
2. If yes: Play $audio_url
3. If no: Use TTS with $response_text
```

---

## Alternative: Return Audio File Content Directly

If Tata Smartflo doesn't support playing external URLs, we can modify our backend to return:
1. **Base64-encoded audio** in the response
2. **Inline audio data**

Let me know if you need help implementing this approach.

---

## Testing: Verify Audio URL is Accessible

Before configuring the IVR, verify the audio URL works:

### Test 1: Open in Browser
Copy this URL and open in your browser:
```
https://faustino-postcruciate-unabortively.ngrok-free.dev/static/audio/2a001b281a5e809a9df422206afe88d8.mp3
```

**Expected**: Audio should download/play

### Test 2: Using curl
```powershell
curl -I https://faustino-postcruciate-unabortively.ngrok-free.dev/static/audio/2a001b281a5e809a9df422206afe88d8.mp3
```

**Expected**: HTTP 200 OK with `Content-Type: audio/mpeg`

---

## Contact Tata Smartflo Support

If you're unsure how to configure the IVR, ask their support:

**Question to ask**:
> "How do I configure my IVR flow to play a dynamic audio URL returned from my webhook API response? 
> My webhook returns a JSON with an `audio_url` field containing an MP3 file URL. 
> How do I make the IVR play that URL instead of a static audio file?"

**Information to provide**:
- Webhook URL: `https://faustino-postcruciate-unabortively.ngrok-free.dev/webhook/incoming`
- Response format: JSON with `audio_url` field
- Audio format: MP3, publicly accessible via HTTPS

---

## Quick Diagnostic

Run this command to see what the webhook is currently returning:

```powershell
curl -X POST https://faustino-postcruciate-unabortively.ngrok-free.dev/webhook/incoming `
  -H "Content-Type: application/json" `
  -d '{"call_id":"test_manual","caller_id_number":"919999999999","start_stamp":"2026-02-12","call_to_number":"919228170743"}'
```

**Expected output**:
```json
{
  "response_text": "Hello! Welcome to BSB Polymer Trading...",
  "audio_url": "https://...ngrok.../static/audio/xxx.mp3",
  "action": "continue"
}
```

---

## Next Steps

1. **Check Tata Smartflo IVR configuration** - This is the most likely issue
2. **Test audio URL accessibility** - Verify it's publicly reachable
3. **Contact Tata support** if needed - Get their guidance on IVR configuration
4. **Let me know** if you need to implement base64 audio or other workarounds
