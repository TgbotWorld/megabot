#!/usr/bin/env python3
import asyncio
import sys
import time

try:
    import megabot
    from megabot.ai.client import get_ai_config, test_ai_connection, call_openrouter_json
    from megabot.ai.planner import plan_actions
except ImportError as e:
    print("\n❌ Missing Python dependencies!")
    print(f"   Error: {e}")
    print("\n👉 How to fix:")
    print("   • If running directly on Ubuntu/VPS:")
    print("       pip install -r requirements.txt")
    print("   • If running with Docker Compose:")
    print("       docker compose exec bot python check_ai.py\n")
    sys.exit(1)


async def test_connection():
    print("\n🔍 Checking MegaBot AI Agent Configuration...")
    print("━" * 50)

    cfg = await get_ai_config()

    # 1. Check API Key presence
    if not cfg["is_configured"]:
        print("❌ AI API Key is NOT configured!")
        print("\n👉 How to configure:")
        print("   1. In .env: Add OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxx")
        print("   2. OR directly through Telegram:")
        print("      • Send command: /setkey <your-api-key>")
        print("      • Or open: /aiconfig\n")
        return False

    api_key = cfg["api_key"]
    masked_key = api_key[:8] + "..." + api_key[-4:]
    print(f"✅ API Key detected: {masked_key}")
    print(f"🌐 Provider:        {cfg['provider_name']}")
    print(f"🔗 Base URL:        {cfg['base_url']}")
    print(f"🧠 Model:           {cfg['model']}")
    print(f"🌡 Temperature:     {cfg['temperature']}")
    print("━" * 50)
    print("📡 Testing live connection to AI Provider...")

    # 2. Test live connection
    conn = await test_ai_connection(cfg)
    if conn.get("success"):
        print(f"✅ Connection successful! (Latency: {conn.get('latency_ms')} ms)")
    else:
        print(f"⚠️ Connection test returned: {conn.get('error')}")
        print("   (Check your network access or API key validity)")

    # 3. Test Agent File Planning Logic
    print("🧠 Testing Agent decision-making on mock file metadata...")
    mock_metadata = {
        "total_files": 5,
        "total_size": "12.4 MB",
        "category_breakdown": {"image": 5},
        "files": [
            {"name": f"page_{i}.jpg", "extension": ".jpg", "category": "image", "size_human": "2.4 MB"}
            for i in range(1, 6)
        ]
    }
    user_prompt = "merge all images into Chapter1.pdf"

    try:
        plan = await plan_actions(mock_metadata, user_prompt)
        if plan and "actions" in plan:
            print(f"✅ Agent Planning operational!")
            print(f"   Summary: {plan.get('summary')}")
            print(f"   Actions: {[a.get('action') for a in plan.get('actions', [])]}")
            print("━" * 50)
            print("🎉 All checks passed! Your bot is fully connected as an AI Agent.\n")
            return True
        else:
            print(f"⚠️ Planning test returned: {plan}")
            return False
    except Exception as e:
        print(f"❌ Planning test error: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(test_connection())
