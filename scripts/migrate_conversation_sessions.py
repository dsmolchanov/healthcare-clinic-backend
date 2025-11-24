#!/usr/bin/env python3
"""
Migrate conversation_sessions from public to healthcare schema.

Run with: python scripts/migrate_conversation_sessions.py --dry-run
Then:     python scripts/migrate_conversation_sessions.py --execute
"""
import argparse
import logging
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_healthcare_client, get_main_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def count_sessions(client, table_name: str = 'conversation_sessions') -> int:
    """Count sessions in a table."""
    try:
        result = client.table(table_name).select('id', count='exact').execute()
        return result.count if hasattr(result, 'count') and result.count else len(result.data or [])
    except Exception as e:
        logger.error(f"Error counting sessions: {e}")
        return 0


def migrate_batch(batch_size: int = 500, dry_run: bool = True) -> dict:
    """Migrate sessions in batches."""
    main = get_main_client()  # public schema
    healthcare = get_healthcare_client()  # healthcare schema

    stats = {
        'total_in_public': 0,
        'total_in_healthcare': 0,
        'migrated': 0,
        'skipped': 0,
        'errors': 0
    }

    # Get counts
    stats['total_in_public'] = count_sessions(main)
    stats['total_in_healthcare'] = count_sessions(healthcare)

    logger.info(f"Sessions in public schema: {stats['total_in_public']}")
    logger.info(f"Sessions in healthcare schema: {stats['total_in_healthcare']}")

    if dry_run:
        logger.info("[DRY RUN] Would migrate sessions from public to healthcare schema")

        # Show sample of what would be migrated
        sample = main.table('conversation_sessions').select('id, user_identifier, status, created_at').limit(5).execute()
        if sample.data:
            logger.info("Sample sessions to migrate:")
            for s in sample.data:
                logger.info(f"  - {s['id'][:8]}... user={s.get('user_identifier', 'N/A')[:15] if s.get('user_identifier') else 'N/A'} status={s.get('status')}")

        return stats

    # Actual migration
    offset = 0
    while True:
        try:
            # Get batch of sessions from public schema
            result = main.table('conversation_sessions').select('*').range(offset, offset + batch_size - 1).execute()

            if not result.data:
                break

            batch = result.data
            logger.info(f"Processing batch of {len(batch)} sessions (offset {offset})...")

            for session in batch:
                session_id = session['id']

                try:
                    # Check if already exists in healthcare
                    exists = healthcare.table('conversation_sessions').select('id').eq('id', session_id).execute()

                    if exists.data:
                        logger.debug(f"Session {session_id[:8]}... already exists in healthcare, skipping")
                        stats['skipped'] += 1
                        continue

                    # Insert into healthcare schema
                    healthcare.table('conversation_sessions').insert(session).execute()
                    stats['migrated'] += 1

                    if stats['migrated'] % 100 == 0:
                        logger.info(f"Migrated {stats['migrated']} sessions...")

                except Exception as e:
                    logger.error(f"Error migrating session {session_id[:8]}...: {e}")
                    stats['errors'] += 1

            offset += batch_size

            # Safety limit
            if offset > 100000:
                logger.warning("Reached safety limit of 100,000 sessions")
                break

        except Exception as e:
            logger.error(f"Error fetching batch at offset {offset}: {e}")
            stats['errors'] += 1
            break

    # Final count
    stats['total_in_healthcare'] = count_sessions(healthcare)

    return stats


def verify_migration() -> bool:
    """Verify migration was successful."""
    main = get_main_client()
    healthcare = get_healthcare_client()

    public_count = count_sessions(main)
    healthcare_count = count_sessions(healthcare)

    logger.info(f"Verification: public={public_count}, healthcare={healthcare_count}")

    if healthcare_count >= public_count:
        logger.info("Migration verification PASSED")
        return True
    else:
        logger.warning(f"Migration verification FAILED: healthcare has fewer sessions")
        return False


def main():
    parser = argparse.ArgumentParser(description='Migrate conversation_sessions to healthcare schema')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    parser.add_argument('--execute', action='store_true', help='Actually perform the migration')
    parser.add_argument('--verify', action='store_true', help='Verify migration was successful')
    parser.add_argument('--batch-size', type=int, default=500, help='Batch size for migration (default: 500)')
    args = parser.parse_args()

    if args.verify:
        success = verify_migration()
        sys.exit(0 if success else 1)

    if not args.dry_run and not args.execute:
        print("Must specify either --dry-run or --execute")
        print("\nUsage:")
        print("  python scripts/migrate_conversation_sessions.py --dry-run    # Preview migration")
        print("  python scripts/migrate_conversation_sessions.py --execute    # Run migration")
        print("  python scripts/migrate_conversation_sessions.py --verify     # Verify migration")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Conversation Sessions Migration")
    logger.info("=" * 60)

    stats = migrate_batch(args.batch_size, dry_run=args.dry_run)

    logger.info("=" * 60)
    logger.info("Migration Summary:")
    logger.info(f"  Total in public schema: {stats['total_in_public']}")
    logger.info(f"  Total in healthcare schema: {stats['total_in_healthcare']}")
    logger.info(f"  Migrated: {stats['migrated']}")
    logger.info(f"  Skipped (already exists): {stats['skipped']}")
    logger.info(f"  Errors: {stats['errors']}")
    logger.info("=" * 60)

    if args.execute and stats['errors'] == 0:
        logger.info("Migration completed successfully!")
    elif args.dry_run:
        logger.info("Dry run completed. Use --execute to perform actual migration.")


if __name__ == '__main__':
    main()
