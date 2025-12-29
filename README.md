# RedLuck Lotto Telegram Bot 🎲

RedLuck Lotto is a decentralized-style lottery bot for Telegram, powered by Solana blockchain. Players can connect wallets, buy tickets, pick their lucky numbers, and compete for the jackpot - all directly inside Telegram!

---

## Features

| Feature | Description |
|---------|-------------|
| 🎲 Play in Telegram | Buy tickets and play directly in the bot - no external website required |
| 🤖 AI Assistant | Built-in AI help system for questions about gameplay, fairness, and support |
| 🔒 Secure Wallets | Encrypted private key storage with PIN protection |
| 💰 Jackpot System | Rolling jackpot that grows until someone wins |
| 🎫 Pick Your Numbers | Choose 5 numbers (1-40) for each ticket |
| ⏰ Hourly Rounds | 24 draws per day, one every hour |
| 🏆 VIP Tiers | Bronze, Silver, Gold, Platinum, Diamond based on activity |
| 📊 Leaderboards | Top winners and most active players |
| 🎁 Referral System | Earn bonuses when friends play (coming soon) |

---

## Commands

### User Commands
| Command | Description |
|---------|-------------|
| `/start` | Open main menu with all options |
| `/ai_help [question]` | Ask the AI assistant a question |
| `/ai_fairness` | Learn how the lottery ensures fairness |

### Admin Commands
| Command | Description |
|---------|-------------|
| `/status` | View system status and active rounds |
| `/refund` | List pending refunds |
| `/refund [stake_id]` | View specific refund details |
| `/mark_refund [id] [tx]` | Mark refund as completed |
| `/force_draw [stake_id]` | Force winner selection |
| `/seedjackpot [amount]` | Add SOL to jackpot |
| `/announce [text]` | Send announcement to channel |

---

## Menu Buttons

| Button | Function |
|--------|----------|
| 🎲 Play Now | Start playing and buy tickets |
| 💰 Check Jackpot | View current prize pool |
| 🎰 Active Rounds | See open rounds and join |
| 💼 Wallets | Manage your Solana wallets |
| 📈 My Stats | View your statistics and VIP tier |
| 🏆 Leaderboard | Top winners and players |
| 🎁 Invite Friends | Get your referral link |
| 📊 Results | View draw results |
| 📘 Rules | Game rules and info |
| 🤖 AI Assistant | Get help from AI |
| 🛠 Support | Contact support |
| ⚙️ Settings | PIN, security, notifications |

---

## AI Assistant Features

The bot includes a built-in AI assistant powered by GPT4All that can help users with:

- **How to Play** - Step-by-step guide to buying tickets and playing
- **Fairness & Transparency** - Explanation of how draws are fair and verifiable
- **Wallet Help** - Creating, importing, and securing wallets
- **Stats & VIP System** - Understanding statistics and VIP tiers
- **Ask Questions** - Free-form questions about the lottery

Access the AI Assistant from the main menu or use `/ai_help [your question]`.

---

## How to Play

1. **Start the bot** - Send `/start` to the bot
2. **Create a wallet** - Create a bot-managed wallet or import your own
3. **Set up PIN** - Create a 4-digit PIN for security
4. **Deposit SOL** - Send SOL to your wallet address
5. **Buy a ticket** - Tap "Play Now" then "Buy Ticket" (0.025 SOL)
6. **Pick 5 numbers** - Select numbers from 1-40
7. **Wait for the draw** - Draws happen every hour on the hour
8. **Win the jackpot** - Match all 5 numbers to win!

---

## Prize Distribution

- **80%** goes to the prize pool (jackpot)
- **20%** goes to the team wallet
- **No winner?** Jackpot rolls over to the next round!

---

## Wallet Security

- **Encryption** - Private keys encrypted with Fernet (AES-128-CBC)
- **PIN Protection** - 4-digit PIN required for sensitive operations
- **Auto-Delete** - Private key messages deleted after 30 seconds
- **Security Questions** - PIN recovery via security question
- **Max 3 Wallets** - Up to 3 wallets per user

---

## VIP Tiers

| Tier | SOL Spent |
|------|-----------|
| Bronze | 0-1 SOL |
| Silver | 1-5 SOL |
| Gold | 5-20 SOL |
| Platinum | 20-50 SOL |
| Diamond | 50+ SOL |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Bot Framework | Aiogram 3.x |
| Database | PostgreSQL |
| Blockchain | Solana (Mainnet) |
| AI | GPT4All (local model) |
| Hosting | Replit |

---

## Environment Variables

### Required
```env
BOT_TOKEN=your_telegram_bot_token
OWNER_WALLET=your_solana_public_address
OWNER_WALLET_PRIVATE_KEY=your_private_key_hex_or_json
ENCRYPTION_KEY=your_32_char_encryption_key
ADMIN_ID=your_telegram_user_id
ROUND_CHANNEL_ID=@yourchannel
DATABASE_URL=postgresql://...
```

### Optional
```env
SOLANA_RPC=https://api.mainnet-beta.solana.com
TEAM_WALLET=separate_team_wallet_address
SUPPORT_USERNAME=support_telegram_username
ANNOUNCEMENTS_GROUP_ID=-100123456789
```

---

## Project Structure

```
├── main.py              # Main bot code with all handlers
├── wallet.py            # Wallet management and Solana operations
├── db.py                # Database operations (PostgreSQL)
├── ai/                  # AI Assistant module
│   ├── __init__.py
│   ├── ai_client.py     # AI model and response generation
│   └── prompts.py       # AI prompts and FAQ responses
├── cache_layer.py       # In-memory caching
├── rate_limiter.py      # Rate limiting for API calls
├── email_service.py     # Email verification service
├── encryption.py        # Encryption utilities
├── rpc_manager.py       # Solana RPC management
├── tx_verification_queue.py  # Transaction verification
├── health_server.py     # Health check endpoint
└── requirements.txt     # Python dependencies
```

---

## Running the Bot

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up environment variables in Replit Secrets

3. Run the bot:
```bash
python main.py
```

---

## Security Notes

- Private keys are never logged or displayed in plain text
- All wallet operations require PIN verification
- Auto-delete feature removes sensitive messages
- Database connections use SSL
- Rate limiting prevents abuse

---

## License

This project is for personal or team use. Redistribution or commercial use requires permission from the author.
