import asyncio
from app.dependencies import get_supabase

async def check_payments():
    sb = get_supabase()
    
    # Check the payments table directly
    print("Checking recently added payments...")
    res = sb.table("payments").select("*").order("created_at", desc=True).limit(5).execute()
    
    if res.data:
        for p in res.data:
            print(f"- Payment {p['id']}: Amount {p['amount']}, Status: {p['status']}, Provider: {p['provider']}, Tenant: {p['tenant_id']}")
    else:
        print("No recent payments found in the database.")

if __name__ == "__main__":
    asyncio.run(check_payments())
