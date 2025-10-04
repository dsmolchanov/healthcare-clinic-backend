"""
Migrate plaintext OAuth tokens to encrypted vault storage.
Run this AFTER the SQL migration to encrypt credentials.
"""
import os
import asyncio
from supabase import create_client
from app.security.compliance_vault import ComplianceVault

async def migrate_credentials_to_vault():
    """Migrate all credentials to vault encryption"""

    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    vault = ComplianceVault()

    # Get all calendar integrations that need vault encryption
    # Note: Supabase Python client doesn't support schema notation properly
    # So we'll query the clinic_calendar_tokens table instead and migrate those
    result = supabase.table('clinic_calendar_tokens').select('*').execute()

    migrated = 0
    failed = 0

    if not result.data:
        print("⚠️  No clinic_calendar_tokens found to migrate")
        return 0, 0

    for token_data in result.data:
        try:
            clinic_id = token_data['clinic_id']

            # Get organization_id from clinics table directly
            # Use an RPC function that queries healthcare schema
            clinic_result = supabase.rpc('healthcare.get_clinic_organization_id', {
                'p_clinic_id': clinic_id
            }).execute()

            if clinic_result.data is None:
                print(f"⚠️  Clinic not found: {clinic_id}")
                failed += 1
                continue

            org_id = clinic_result.data
            if not org_id:
                print(f"⚠️  No organization_id for clinic {clinic_id}")
                failed += 1
                continue

            # Build credentials object
            credentials = {
                'access_token': token_data['access_token'],
                'refresh_token': token_data['refresh_token'],
                'token_type': 'Bearer',
                'expires_at': token_data['expires_at'],
                'token_uri': 'https://oauth2.googleapis.com/token',
                'client_id': os.getenv('GOOGLE_CLIENT_ID'),
                'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
                'scopes': token_data.get('scope', '').split() if token_data.get('scope') else []
            }

            # Generate vault reference matching the SQL migration pattern
            import uuid
            vault_ref = f"vault:org:{org_id}:calendar:{token_data['provider']}:{uuid.uuid4()}"

            # Store in vault
            await vault.store_calendar_credentials(
                organization_id=org_id,
                provider=token_data['provider'],
                credentials=credentials,
                vault_ref=vault_ref
            )

            print(f"✅ Migrated credentials for clinic {clinic_id} (provider: {token_data['provider']})")
            migrated += 1

        except Exception as e:
            print(f"❌ Failed to migrate clinic {token_data.get('clinic_id', 'unknown')}: {e}")
            failed += 1

    print(f"\n📊 Migration Summary:")
    print(f"   Migrated: {migrated}")
    print(f"   Failed: {failed}")
    print(f"   Total: {migrated + failed}")

    return migrated, failed

if __name__ == "__main__":
    asyncio.run(migrate_credentials_to_vault())
