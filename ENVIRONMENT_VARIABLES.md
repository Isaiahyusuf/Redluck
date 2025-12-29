# Environment Variables - Complete Reference

This document lists ALL environment variables used by the RedLuck Lotto Telegram Bot.

## 🔴 REQUIRED Environment Variables

These MUST be set for the bot to function properly:

### 1. BOT_TOKEN
- **Description**: Telegram Bot API token from @BotFather
- **Used in**: `Main.py`, `encryption.py`
- **Example**: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`
- **How to get**: 
  1. Message @BotFather on Telegram
  2. Create a new bot with `/newbot`
  3. Copy the token provided

### 2. OWNER_WALLET
- **Description**: Main treasury Solana wallet address (receives 80% of stakes)
- **Used in**: `Main.py`
- **Example**: `AdsUp4UT3AAGv9m8fYAYXY5mMMAxkJXneVyd6MMwwBVR`
- **Note**: This is the primary revenue wallet

### 3. ADMIN_ID
- **Description**: Telegram user ID of the administrator
- **Used in**: `Main.py` (admin commands authorization)
- **Example**: `123456789`
- **How to get**:
  1. Message @userinfobot on Telegram
  2. It will reply with your user ID

### 4. ROUND_CHANNEL_ID
- **Description**: Telegram channel ID for public announcements
- **Used in**: `Main.py` (channel announcements)
- **Example**: `@redlucklottoportal` or `-1001234567890`
- **Default**: `@redlucklottoportal`
- **Note**: Use channel username (with @) or numeric ID (with -100 prefix)

### 5. SOLANA_RPC
- **Description**: Solana RPC endpoint URL
- **Used in**: `Main.py`, `wallet.py`
- **Default**: `https://api.mainnet-beta.solana.com`
- **Recommended**: Use a premium RPC for production
  - Helius: `https://mainnet.helius-rpc.com/?api-key=YOUR_KEY`
  - QuickNode: `https://YOUR_ENDPOINT.quiknode.pro/YOUR_KEY/`
- **Note**: Free public RPC is rate-limited and unreliable

### 6. ENCRYPTION_KEY
- **Description**: Master encryption key for wallet private keys
- **Used in**: `encryption.py`, `wallet.py`
- **Example**: `your-super-secret-encryption-key-min-32-chars`
- **Requirements**: 
  - Minimum 32 characters
  - Use strong random string
  - NEVER share or commit to git
- **Generate with**: `openssl rand -base64 32`

---

## 🟡 OPTIONAL Environment Variables

These have defaults but should be configured for production:

### 7. TEAM_WALLET
- **Description**: Team wallet address (receives 20% of stakes)
- **Used in**: `Main.py`
- **Default**: Falls back to `OWNER_WALLET`
- **Example**: `BteamWalletAddressHere123456789ABCDEF`
- **Note**: If not set, 100% goes to OWNER_WALLET

### 8. SUPPORT_USERNAME
- **Description**: Telegram username for support contact
- **Used in**: `Main.py` (support button)
- **Example**: `redluck_support` (without @)
- **Default**: None (support message won't include contact link)
- **Note**: Users will see "Contact: @YOUR_USERNAME" in support menu

---

## 📋 How to Set Environment Variables in Replit

1. Click on "Tools" in the left sidebar
2. Select "Secrets"
3. Add each variable:
   - Key: Variable name (e.g., `BOT_TOKEN`)
   - Value: The actual value
4. Click "Add new secret"
5. Restart the workflow after adding all secrets

---

## ✅ Quick Setup Checklist

Copy this to check off as you configure:

```
☐ BOT_TOKEN - From @BotFather
☐ OWNER_WALLET - Your Solana wallet address
☐ ADMIN_ID - Your Telegram user ID
☐ ROUND_CHANNEL_ID - Your announcement channel
☐ SOLANA_RPC - Your RPC endpoint (Helius/QuickNode recommended)
☐ ENCRYPTION_KEY - Generated random 32+ character string
☐ TEAM_WALLET - Team wallet address (optional, defaults to OWNER_WALLET)
☐ SUPPORT_USERNAME - Your support Telegram username (optional)
```

---

## 🔍 Verification

The bot automatically audits all environment variables on startup. Check the logs for:

```
🔐 Auditing Configuration...
  ✅ BOT_TOKEN: 1234567890...
  ✅ OWNER_WALLET: AdsUp4UT...
  ✅ ADMIN_ID: 123456789
  ✅ ROUND_CHANNEL_ID: @yourChannel
  ✅ SOLANA_RPC: https://mainnet...
  ✅ ENCRYPTION_KEY: ***
  ⚠️ Optional: TEAM_WALLET - Not set
  ⚠️ Optional: SUPPORT_USERNAME - Not set
```

---

## ⚠️ Security Notes

1. **NEVER** commit `.env` file to git
2. **NEVER** share your `BOT_TOKEN` or `ENCRYPTION_KEY`
3. Use Replit Secrets (not .env file) for production
4. Rotate `ENCRYPTION_KEY` if compromised (will invalidate existing encrypted wallets)
5. Keep `ADMIN_ID` private to prevent unauthorized access

---

## 🚨 Common Issues

### "ENCRYPTION_KEY environment variable is required"
**Solution**: Set ENCRYPTION_KEY in Replit Secrets (minimum 32 characters)

### "⛔ CRITICAL: Missing required environment variables!"
**Solution**: Check startup logs and add all missing required variables

### Bot not responding
**Solution**: Verify BOT_TOKEN is correct and not revoked

### Transactions failing
**Solution**: Check OWNER_WALLET is valid Solana address and SOLANA_RPC is working

### No channel announcements
**Solution**: 
- Verify ROUND_CHANNEL_ID is correct
- Make sure bot is admin in the channel
- Check channel username format (@channel or -1001234567890)

---

## 📞 Support

If you need help configuring environment variables:
1. Check the logs for specific error messages
2. Verify each variable follows the format examples above
3. Use `/status` admin command to test bot configuration
