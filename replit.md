# RedLuck Lotto Bot

## Overview
A Telegram-based cryptocurrency lottery bot with real Solana wallet integration. Users can buy lottery tickets with SOL, with automated draws and prize distribution using a **tiered prize system**.

**PostgreSQL only - No SQLite.**

## Tiered Prize System
The lottery uses a 3-tier prize structure:
- **5-Match Winners (70%)** - Match all 5 numbers for the JACKPOT tier
- **4-Match Winners (20%)** - Match 4 numbers
- **3-Match Winners (10%)** - Match 3 numbers

**Key Rules:**
- Prize pool = 80% of ticket sales + rollover from previous rounds
- 20% of ticket sales goes to team
- Multiple winners in a tier split that tier's allocation equally
- If no winners in a tier, that tier's allocation **rolls over** to the next round
- Each tier rolls over independently

## Database
- **PostgreSQL only** - No SQLite fallback
- Connected via `DATABASE_URL` environment variable
- Users can buy multiple tickets per round
- Optimized indexes for round_id, user_id, transaction_signature

## Architecture
- **Bot Framework**: aiogram 3.4+ (Telegram Bot API)
- **Blockchain**: Solana mainnet via solana-py/solders
- **Database**: PostgreSQL (required)
- **AI Assistant**: Groq Llama-3.1-8B-instant (fast responses)
- **Encryption**: cryptography library for wallet private key encryption
- **Caching**: In-memory cache layer for reduced DB/RPC reads
- **Rate Limiting**: Per-user rate limits for spam prevention

## Key Files
- `main.py` - Main bot logic and handlers
- `db.py` - PostgreSQL-only database layer
- `wallet.py` - Solana wallet management
- `wallet_buttons.py` - Wallet action button handlers
- `ai/ai_client.py` - AI assistant client (Groq only)
- `ai/prompts.py` - AI prompts and FAQ responses
- `email_service.py` - Email verification service
- `encryption.py` - Private key encryption
- `rpc_manager.py` - Centralized RPC with load balancing and failover
- `cache_layer.py` - In-memory cache with TTL for reducing reads
- `rate_limiter.py` - Per-user rate limiting

## AI Assistant Features
- **Fast responses** - Uses Groq Llama-3.1-8B-instant for speed
- **Async handling** - Non-blocking AI calls
- **Response caching** - 5-minute cache for repeated questions
- **FAQ matching** - Instant responses for common questions
- **Limited context** - 5 messages max for fast processing

## Required Environment Variables
- `BOT_TOKEN` - Telegram Bot API token
- `DATABASE_URL` - PostgreSQL connection string (required)
- `OWNER_WALLET` - Bot's Solana wallet public address
- `OWNER_WALLET_PRIVATE_KEY` - Bot's Solana wallet private key
- `ENCRYPTION_KEY` - Strong random key for wallet encryption
- `ADMIN_ID` - Admin's numeric Telegram user ID
- `ROUND_CHANNEL_ID` - Telegram channel for announcements

## Optional Environment Variables
- `SOLANA_RPC` - Custom Solana RPC endpoint
- `HELIUS_RPC` - Helius RPC endpoint (primary for writes)
- `TEAM_WALLET` - Team Solana wallet for fees
- `SUPPORT_USERNAME` - Support contact
- `ANNOUNCEMENTS_GROUP_ID` - Additional announcement group
- `GROQ_API_KEY` - Groq API key for AI assistant

## Recent Changes (December 2025)
- **Groq-only AI** - Removed OpenAI and Gemini, using Groq exclusively for faster responses
- **Async AI handling** - Non-blocking AI calls using asyncio.to_thread()
- **Response caching** - 5-minute cache for repeated AI questions
- **Tiered prize system verified** - 5-match (70%), 4-match (20%), 3-match (10%)
- **Independent rollover** - Each tier rolls over separately if no winners
- **Updated rules display** - All prize displays now show tiered system correctly
- **Cleaned dependencies** - Removed unused AI provider packages
- **Callback acknowledgment** - Immediate button response for better UX

## Running the Bot
1. Set all required environment variables (see above)
2. Ensure PostgreSQL database is available
3. Run `python main.py`

The bot will:
- Initialize the PostgreSQL connection pool
- Create all necessary tables
- Initialize cache and rate limiter
- Start listening for Telegram commands
