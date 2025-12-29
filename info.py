#!/usr/bin/env python3
"""
RedLuck Lotto Bot - Railway-Only Deployment

This bot is configured to run EXCLUSIVELY on Railway platform.
It will not run on Replit or any other platform to prevent
Telegram polling conflicts.

To deploy:
1. Push this code to your GitHub repository
2. Connect the repository to Railway
3. Set all required environment variables in Railway
4. The bot will start automatically

Required Environment Variables:
- BOT_TOKEN: Telegram Bot API token
- OWNER_WALLET: Main jackpot wallet address
- OWNER_WALLET_PRIVATE_KEY: For automated prize distributions
- TEAM_WALLET: Receives 20% of stakes
- ADMIN_ID: Telegram user ID for admin access
- ROUND_CHANNEL_ID: Public Telegram channel for announcements
- ENCRYPTION_KEY: Master key for private key encryption
- DATABASE_URL: PostgreSQL connection string (provided by Railway)

Optional:
- SOLANA_RPC: Your Solana RPC endpoint (e.g., Helius). Falls back to public RPC if not set.
- ANNOUNCEMENTS_GROUP_ID: Additional Telegram group for announcements
- SUPPORT_USERNAME: Telegram support contact
"""

print("=" * 60)
print("RedLuck Lotto Bot - Project Info")
print("=" * 60)
print()
print("This bot runs ONLY on Railway platform.")
print("It cannot be run from Replit to prevent conflicts.")
print()
print("Deployment Instructions:")
print("1. Push code to GitHub")
print("2. Deploy to Railway")
print("3. Configure environment variables in Railway")
print("4. Bot starts automatically")
print()
print("For more info, see replit.md")
print("=" * 60)
