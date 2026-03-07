import asyncio
from app.dependencies import get_supabase
from app.config import settings

async def check_user():
    sb = get_supabase()
    demo_id = settings.demo_landlord_id
    print(f"Checking for DEMO_LANDLORD_ID: {demo_id}")
    
    res = sb.table("users").select("*").eq("id", demo_id).execute()
    if res.data:
        print(f"Found user: {res.data[0]}")
    else:
        print("User NOT found in database.")
        
    # List some actual landlords
    res = sb.table("users").select("id, name, email").eq("role", "landlord").limit(5).execute()
    if res.data:
        print("\nExisting Landlords in DB:")
        for user in res.data:
            print(f"- {user['id']} ({user['name']})")
    else:
        print("\nNo landlords found in DB.")

if __name__ == "__main__":
    asyncio.run(check_user())
