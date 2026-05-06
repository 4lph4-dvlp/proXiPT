import asyncio
from openai import AsyncOpenAI

async def main():
    client = AsyncOpenAI(
        base_url="http://localhost:8787/v1",
        api_key="sk-proxipt"
    )
    
    print("Testing ChatGPT in headless mode via API...")
    try:
        response = await client.chat.completions.create(
            model="duckai-gpt-4o-mini",
            messages=[{"role": "user", "content": "What is 3 + 4? Just say the number."}],
            stream=False
        )
        print("ChatGPT response:", response.choices[0].message.content)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
