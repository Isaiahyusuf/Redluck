# 🤖 UptimeRobot Setup Guide - Keep Your Bot Active 24/7

## Why You Need This

Replit free tier puts inactive projects to sleep after a period of inactivity. UptimeRobot will "ping" your bot every 5 minutes to keep it awake and running continuously.

---

## Step-by-Step Setup Instructions

### Step 1: Sign Up for UptimeRobot

1. Go to **https://uptimerobot.com**
2. Click **"Sign Up Free"** (100% free, no credit card required)
3. Create your account with email verification
4. Log in to your dashboard

---

### Step 2: Create a New Monitor

1. In your UptimeRobot dashboard, click **"+ Add New Monitor"**
2. Fill in the following details:

   **Monitor Type:** `HTTP(s)`
   
   **Friendly Name:** `RedLuck Lotto Bot`
   
   **URL (or IP):** 
   ```
   https://af9f7558-cda5-4715-a9ce-d8a179fe83c1-00-2qxmeqehkqxml.riker.replit.dev/health
   ```
   
   **Monitoring Interval:** `5 minutes` (default)
   
   **Monitor Timeout:** `30 seconds` (default)
   
   **Alert Contacts:** (Optional - add your email to get notified if bot goes down)

3. Click **"Create Monitor"**

---

### Step 3: Verify It's Working

1. Wait 5 minutes for the first check
2. You should see a green checkmark ✅ next to your monitor
3. The "Uptime" percentage should show 100%
4. If there's a red X ❌, check that:
   - Your Replit project is running
   - The URL is correct
   - Your bot's web server is active on port 8080

---

## Your Bot's Health Check Endpoints

Your bot has 3 endpoints that UptimeRobot can ping:

- **`/health`** - Returns "✅ RedLuck Lotto Bot is alive!" (RECOMMENDED)
- **`/ping`** - Same as /health
- **`/`** - Returns bot status message

**Recommended URL for UptimeRobot:**
```
https://af9f7558-cda5-4715-a9ce-d8a179fe83c1-00-2qxmeqehkqxml.riker.replit.dev/health
```

---

## Testing Your Setup

### Test 1: Manual Health Check
Open this URL in your browser:
```
https://af9f7558-cda5-4715-a9ce-d8a179fe83c1-00-2qxmeqehkqxml.riker.replit.dev/health
```
You should see: `✅ RedLuck Lotto Bot is alive!`

### Test 2: Check UptimeRobot Dashboard
- Log in to UptimeRobot
- You should see your monitor with 100% uptime
- Click on it to see ping history

### Test 3: Verify Bot on Telegram
- Open your Telegram bot
- Send `/start` command
- Bot should respond immediately

---

## What Happens Now?

✅ **Every 5 minutes:** UptimeRobot pings your bot's health endpoint
✅ **Your bot stays awake:** The ping keeps Replit from putting your project to sleep
✅ **24/7 availability:** Your users can interact with the bot anytime
✅ **Automatic recovery:** If the bot crashes, UptimeRobot will detect it and alert you

---

## Troubleshooting

### ❌ UptimeRobot shows "Down"

**Possible causes:**
1. Replit workflow stopped - Restart it manually
2. Bot crashed - Check logs for errors
3. Port 8080 not responding - Verify web server is running
4. Wrong URL - Double-check the URL in UptimeRobot

**Solution:**
- Go to your Replit project
- Check the workflow status (should say "RUNNING")
- Restart the workflow if needed
- Check logs for any errors

### ⚠️ Bot sometimes doesn't respond

**Possible causes:**
1. Telegram API rate limits
2. RPC endpoint issues
3. Bot processing heavy tasks

**Solution:**
- Upgrade to a premium Solana RPC (Helius, QuickNode)
- Check Telegram API status
- Monitor bot logs for errors

### 💡 Bot uses too many resources

**Current setup:**
- Free Replit tier has limits
- For heavy usage, consider Replit Core subscription

---

## Advanced: Multiple Monitors (Optional)

For extra reliability, you can create 2 monitors:

1. **Primary Monitor:** Pings `/health` every 5 minutes
2. **Backup Monitor:** Pings `/ping` every 10 minutes

This ensures redundancy if one monitor fails.

---

## Cost Summary

✅ **UptimeRobot:** FREE (50 monitors, 5-minute interval)
✅ **Replit:** FREE tier (with some limitations)
💰 **Replit Core:** $20/month (for guaranteed 24/7 uptime, more resources)

---

## Next Steps

1. ✅ Set up UptimeRobot monitor (5 minutes)
2. ✅ Wait 24 hours and check uptime percentage
3. ✅ Test your bot functionality on Telegram
4. ✅ Monitor the logs for any errors
5. ✅ If needed, upgrade Solana RPC to premium tier

---

## Support

If you encounter any issues:
1. Check the bot logs in Replit
2. Verify all environment variables are set correctly
3. Test the health endpoint manually
4. Check UptimeRobot monitor status

---

## ✅ Setup Complete!

Your bot is now configured for 24/7 operation. The UptimeRobot monitor will keep it alive and alert you if anything goes wrong.

**Your Bot URL:** https://af9f7558-cda5-4715-a9ce-d8a179fe83c1-00-2qxmeqehkqxml.riker.replit.dev
**Health Check:** https://af9f7558-cda5-4715-a9ce-d8a179fe83c1-00-2qxmeqehkqxml.riker.replit.dev/health

Happy lottery running! 🎰🚀
