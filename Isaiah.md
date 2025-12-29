# RedLuck Lotto Telegram Bot

## Overview
A decentralized lottery bot for Telegram with **real Solana mainnet integration**. Users can create wallets, connect external wallets (Phantom/Solflare), deposit real SOL, and play lottery games with automatic on-chain payments.

## Project Status
✅ **LIVE & PRODUCTION-READY** - Successfully migrated to Replit (Nov 9, 2025)

**Bot Status:** 🟢 RUNNING  
**Health Check:** https://af9f7558-cda5-4715-a9ce-d8a179fe83c1-00-2qxmeqehkqxml.riker.replit.dev/health

### ✅ Migration Complete
- All Python dependencies installed (aiogram, solana, solders, etc.)
- All 9 environment variables configured in Replit Secrets
- Workflow active and bot responding on Telegram
- Web server running on port 8080 for 24/7 uptime
- Ready for UptimeRobot setup (see UPTIMEROBOT_SETUP.md)

### 🎯 All Critical Features Implemented
- ✅ On-chain payment verification
- ✅ Automatic prize distribution (using OWNER_WALLET_PRIVATE_KEY)
- ✅ Smart refund system
- ✅ Verifiable cryptographic randomness
- ✅ Minimum player enforcement (10 players per stake)

**See IMPLEMENTATION_STATUS.md for detailed feature breakdown**

## Tech Stack
- **Language**: Python 3.11
- **Framework**: Aiogram 2.25.1 (Telegram Bot)
- **Blockchain**: Solana Mainnet
- **Database**: PostgreSQL (required, no local files)
- **Hosting**: Replit

## Recent Changes (Nov 9, 2025)
- ✅ Installed Python 3.11 and all dependencies
- ✅ Implemented real Solana mainnet integration with actual transactions
- ✅ Added support for multiple wallets per user (up to 3)
- ✅ Implemented real balance checking from Solana blockchain
- ✅ Added bot-managed wallet creation with private key storage
- ✅ Added external wallet connection (Phantom, Solflare, etc.)
- ✅ Implemented real SOL transfers to OWNER_WALLET on stakes
- ✅ Added database persistence for wallet data
- ✅ Created comprehensive wallet management UI
- ✅ Fixed security issue with API key logging
- ✅ Fixed Telegram webhook conflict error
- ✅ Implemented 4-digit PIN system for wallet security
- ✅ Added private key viewing feature (PIN-protected)
- ✅ Added wallet deletion functionality
- ✅ Implemented send SOL feature from bot wallets (PIN-protected)
- ✅ Enhanced lottery ticket display with Ticket ID
- ✅ Improved winner announcement with prize pool and better formatting
- ✅ Added all required environment variables (SUPPORT_USERNAME, etc.)
- ✅ Replaced pseudo-random with SHA256-based cryptographic randomness
- ✅ Implemented verifiable seed generation using transaction signatures
- ✅ Added aiohttp web server for UptimeRobot keep-alive functionality
- ✅ Exposed health check endpoints (/, /health, /ping) on port 8080

## Project Architecture

### File Structure
```
/
├── Main.py              # Main bot file with handlers and commands
├── wallet.py            # Real Solana wallet management (create, balance, transactions)
├── wallet_buttons.py    # Wallet management button handlers
├── encryption.py        # AES encryption for private keys
├── Index.html           # Wallet connection interface (future WalletConnect integration)
├── requirements.txt     # Python dependencies
├── .gitignore          # Git ignore rules for Python
├── .env                # Local environment variables (not used, use Replit Secrets)
└── (PostgreSQL database - external, no local files)
```

### Database Tables
- **users**: Tracks Telegram users (user_id, username)
- **wallets**: Stores user wallets (address, type, encrypted private_key for bot wallets)
- **user_active_wallet**: Tracks which wallet is currently active per user
- **user_pins**: Stores hashed 4-digit PINs for wallet security
- **entries**: Lottery entries (id/ticket_id, user_id, round, numbers, stake_amount, tx_signature)
- **draws**: Draw results (round, winning_numbers, timestamp)
- **meta**: System metadata (current_round)

## Environment Variables (Secrets)
All stored in Replit Secrets:
- `BOT_TOKEN`: Telegram bot token from @BotFather
- `OWNER_WALLET`: Main Solana wallet (receives 80% of stakes)
- `TEAM_WALLET`: Team Solana wallet (receives 20% of stakes)
- `ADMIN_ID`: Telegram user ID with admin privileges
- `SOLANA_RPC`: Solana RPC endpoint (Helius, QuickNode, or mainnet-beta)
- `ROUND_CHANNEL_ID`: Optional channel for posting results (defaults to @redlucklottoportal)
- `SUPPORT_USERNAME`: Support contact (Telegram username without @)

## How It Works

### User Flow
1. User starts bot with `/start`
2. User clicks "🎲 Play" to begin
3. User creates a bot wallet OR connects external wallet (Phantom/Solflare)
4. User can create up to 3 wallets and switch between them
5. Bot checks **real balance** from Solana blockchain
6. User chooses stake package (0.05 SOL - 5 SOL)
7. Bot generates 5 random numbers (1-40)
8. **Real SOL transaction** is sent to OWNER_WALLET (80%) and TEAM_WALLET (20%)
9. Transaction signature is stored in database
10. Admin draws winning numbers with `/admin_draw`
11. Winners are announced publicly

### Wallet System

#### Multiple Wallets Support
- Each user can create/connect up to **3 wallets**
- Switch between wallets anytime from "💼 My Wallets" menu
- Each wallet shows real-time balance from Solana blockchain

#### Bot-Managed Wallets
- Created directly in Telegram
- Private keys encrypted with AES and stored in database
- Automatic transaction signing for stakes
- Users can deposit SOL from exchanges or other wallets
- Protected by 4-digit PIN for sensitive operations

#### PIN Security System
- Users set a 4-digit PIN on first wallet creation
- PIN is SHA256 hashed before database storage (never stored in plain text)
- Required for:
  - Viewing private keys
  - Sending SOL from bot wallets
  - Deleting wallets
- PIN verification with 3-attempt limit to prevent brute force
- Failed attempts are logged with user context

#### External Wallets
- Connect Phantom, Solflare, or any Solana wallet
- Paste wallet address in Telegram
- Bot checks real balance on-chain
- Manual transaction required for stakes (users send SOL themselves)

### Real Blockchain Integration
- **Balance Checking**: Fetches real SOL balance from Solana mainnet
- **Transaction Sending**: Uses Solana SDK to send real SOL transactions
- **Transaction Verification**: Stores transaction signatures for verification
- **RPC Connection**: Connects to Solana RPC (Helius, QuickNode, or public mainnet)

### Admin Commands
- `/admin_draw` - Draw winning numbers for current round and announce winners
- Bot tracks all entries with transaction signatures for verification

## Bot Commands & Buttons

### User Commands
- `/start` - Open main menu

### Main Menu Buttons
- 🎲 **Play** - Start lottery play session
- 💼 **My Wallets** - Manage wallets (view, create, connect, switch)
- 📊 **View Results** - See current round and results
- 📘 **Rules** - Learn how to play
- 🛠 **Support** - Get help

### Wallet Management
- ➕ **Create Wallet** - Create new bot-managed wallet
- 🔗 **Connect Wallet** - Connect external wallet (Phantom, Solflare)
- ✅ **Active Wallet** - Shows which wallet is currently active
- **Wallet List** - View all wallets with balances

### Play Flow
- 💵 **Choose Stake** - Select stake amount (0.05-5 SOL)
- 💼 **Switch Wallet** - Change active wallet
- Balance is checked in real-time before allowing stakes

## Workflow Configuration
- **Name**: telegram-bot
- **Command**: `python Main.py`
- **Type**: Console (background process)
- **Auto-restart**: Yes

## Security Features

### Private Key Protection
- Bot-managed wallet private keys stored in database
- Never displayed to users
- Used only for automatic transaction signing
- External wallet private keys never requested or stored

### Transaction Security
- Real on-chain transactions with verification
- Transaction signatures stored for audit trail
- Balance checked before every stake
- 80/20 split automatically enforced (owner/team)

### API Key Protection
- RPC API keys masked in logs
- All secrets stored in Replit Secrets (not .env)
- No secrets exposed in code

## Testing the Bot

### Setup
1. Add all required secrets in Replit Secrets
2. Make sure OWNER_WALLET is your real Solana wallet address
3. Set SOLANA_RPC to a reliable endpoint (Helius recommended for production)

### Test Flow
1. Open Telegram and find your bot
2. Send `/start`
3. Click "🎲 Play"
4. Create a bot wallet or connect external wallet
5. Deposit some SOL to your wallet address
6. Choose a stake amount
7. Bot will send real SOL transaction
8. Receive lottery numbers
9. Admin uses `/admin_draw` to draw winners

### Testing Multiple Wallets
1. Create first wallet
2. Go to "💼 My Wallets"
3. Create 2 more wallets (maximum 3)
4. Switch between wallets to see different balances
5. Each wallet can play independently

## Troubleshooting

### Bot Not Responding
- Check workflow status (should be "RUNNING")
- Verify BOT_TOKEN is correct in Replit Secrets
- Check logs for errors

### Transaction Failures
- Ensure wallet has sufficient SOL balance
- Check SOLANA_RPC endpoint is working
- Verify OWNER_WALLET address is valid
- Check network fees (need extra SOL for fees)

### Balance Not Updating
- Balance is fetched in real-time from blockchain
- May take a few seconds for new transactions to confirm
- Check Solana network status if delays persist

### Webhook Conflicts
If you see "webhook is active" error:
```bash
python -c "import asyncio; from aiogram import Bot; import os; from dotenv import load_dotenv; load_dotenv(); bot = Bot(token=os.getenv('BOT_TOKEN')); asyncio.run(bot.delete_webhook(drop_pending_updates=True))"
```

### Keep Bot Alive 24/7
The bot includes a web server on port 8080 to prevent Replit from sleeping:

1. **UptimeRobot Setup** (Recommended):
   - Sign up at https://uptimerobot.com (free)
   - Create new HTTP(S) monitor
   - Use your Replit URL + `/health` (e.g., `https://your-repl.username.repl.co/health`)
   - Set interval to 5 minutes
   - UptimeRobot will ping your bot to keep it awake

2. **Available Endpoints**:
   - `/` - Bot status message
   - `/health` - Health check (returns "✅ RedLuck Lotto Bot is alive!")
   - `/ping` - Same as /health

3. **Note**: For guaranteed 24/7 uptime, consider using Replit Deployments (paid) or hosting on VPS/cloud providers.

## Development Notes

### Current Implementation
✅ Multiple wallets per user (up to 3)
✅ Real Solana mainnet integration
✅ Real balance checking
✅ Real SOL transactions
✅ Bot-managed wallet creation
✅ External wallet connection
✅ Database persistence
✅ Transaction signature tracking
✅ Web server for UptimeRobot keep-alive (port 8080)
✅ SHA256-based cryptographic randomness with verifiable seeds

### Future Improvements (Phase 2)
- WalletConnect integration for seamless Phantom/Solflare connection
- QR code wallet connection
- Chainlink VRF for provably fair random numbers
- Prize pool distribution to winners
- Multi-chain support (Ethereum, Polygon, etc.)
- Modern web dashboard
- Auto-compound winnings feature

## Important Notes

### For Users
- Bot-managed wallets are convenient but keep only small amounts
- For large amounts, use external wallets (Phantom, Solflare)
- Always verify transaction signatures on Solscan
- Network fees are automatically deducted from balance

### For Developers
- Private keys stored in database (encrypt in production)
- Use premium RPC (Helius, QuickNode) for production
- Monitor transaction failures and implement retry logic
- Consider implementing transaction confirmation checks
- Add rate limiting for stake requests

## RPC Recommendations

### Free Option
- `https://api.mainnet-beta.solana.com` (rate limited)

### Premium Options (Recommended)
- **Helius**: High performance, generous free tier
- **QuickNode**: Reliable, good support
- **Alchemy**: Enterprise grade

## License
Free to use for personal or team projects. Redistribution or commercial use requires permission from the author.

---

## Quick Reference

**Max Wallets Per User**: 3
**Stake Range**: 0.05 - 5 SOL
**Numbers Range**: 1-40 (5 numbers per entry)
**Prize Split**: 80% owner / 20% team
**Blockchain**: Solana Mainnet
