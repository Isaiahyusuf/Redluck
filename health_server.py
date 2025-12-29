"""
Simple health check server for development.
The main bot should run on Railway to avoid conflicts.
"""
from aiohttp import web
import asyncio

async def health_check(request):
    return web.Response(text="OK - Bot running on Railway")

async def index(request):
    return web.Response(text="""
    <html>
    <body>
        <h1>RedLuck Lotto Bot</h1>
        <p>Status: Development Mode</p>
        <p>The main bot is running on Railway.</p>
        <p>This server is for development/testing only.</p>
    </body>
    </html>
    """, content_type='text/html')

async def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5000)
    await site.start()
    print("Health server running on http://0.0.0.0:5000")
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
