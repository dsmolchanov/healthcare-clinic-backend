"""
External Data Backup Service
Backs up non-Supabase data (Redis, WhatsApp auth, config files, localStorage)
"""

import json
import redis.asyncio as redis
import aiofiles
import os
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from pathlib import Path
import logging
import asyncio
import tarfile
import io
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class ExternalBackupService:
    """
    Manages backup and restore of external data not stored in Supabase:
    - Redis session data and cache
    - WhatsApp Evolution API authentication
    - Configuration files
    - Frontend localStorage data (via API)
    """

    def __init__(self, redis_client: redis.Redis, backup_dir: str = "/data/backups"):
        self.redis_client = redis_client
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Encryption key for sensitive data (should be from environment)
        encryption_key = os.getenv("BACKUP_ENCRYPTION_KEY")
        if encryption_key:
            self.cipher = Fernet(encryption_key.encode()[:32].ljust(32, b'0'))
        else:
            self.cipher = None
            logger.warning("No encryption key found, backups will not be encrypted")

    async def backup_redis_data(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Backs up Redis data for a specific tenant or all tenants

        Args:
            tenant_id: Optional tenant ID to filter data

        Returns:
            Dict containing backup metadata and data
        """
        try:
            backup_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "redis",
                "tenant_id": tenant_id,
                "data": {}
            }

            # Define key patterns to backup
            patterns = [
                "session:*",
                "cache:appointments:*",
                "cache:calendar:*",
                "hold:*",
                "conflict:*",
                "websocket:*"
            ]

            if tenant_id:
                patterns = [f"{tenant_id}:{pattern}" for pattern in patterns]

            # Backup each pattern
            for pattern in patterns:
                keys = []
                async for key in self.redis_client.scan_iter(match=pattern):
                    keys.append(key)

                if keys:
                    # Get all values in pipeline for efficiency
                    pipe = self.redis_client.pipeline()
                    for key in keys:
                        pipe.get(key)
                        pipe.ttl(key)  # Also get TTL for restoration

                    results = await pipe.execute()

                    # Process results (pairs of value, ttl)
                    for i in range(0, len(results), 2):
                        key_str = keys[i // 2].decode() if isinstance(keys[i // 2], bytes) else keys[i // 2]
                        value = results[i]
                        ttl = results[i + 1]

                        if value:
                            backup_data["data"][key_str] = {
                                "value": value.decode() if isinstance(value, bytes) else value,
                                "ttl": ttl if ttl > 0 else None
                            }

            # Calculate checksum
            data_str = json.dumps(backup_data["data"], sort_keys=True)
            backup_data["checksum"] = hashlib.sha256(data_str.encode()).hexdigest()

            logger.info(f"Backed up {len(backup_data['data'])} Redis keys")
            return backup_data

        except Exception as e:
            logger.error(f"Redis backup failed: {e}")
            raise

    async def backup_whatsapp_auth(self) -> Dict[str, Any]:
        """
        Backs up WhatsApp Evolution API authentication and session data

        Returns:
            Dict containing WhatsApp auth backup
        """
        try:
            backup_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "whatsapp_auth",
                "instances": []
            }

            # Read Evolution API data directory
            evolution_data_dir = Path("/data/evolution")
            if not evolution_data_dir.exists():
                evolution_data_dir = Path("./evolution_data")  # Fallback for local dev

            if evolution_data_dir.exists():
                # Backup each instance
                for instance_dir in evolution_data_dir.iterdir():
                    if instance_dir.is_dir():
                        instance_data = {
                            "name": instance_dir.name,
                            "files": {}
                        }

                        # Backup auth files
                        auth_files = ["auth_info.json", "session.json", "creds.json"]
                        for auth_file in auth_files:
                            file_path = instance_dir / auth_file
                            if file_path.exists():
                                async with aiofiles.open(file_path, 'r') as f:
                                    content = await f.read()
                                    # Encrypt sensitive auth data
                                    if self.cipher:
                                        content = self.cipher.encrypt(content.encode()).decode()
                                    instance_data["files"][auth_file] = content

                        if instance_data["files"]:
                            backup_data["instances"].append(instance_data)

            logger.info(f"Backed up {len(backup_data['instances'])} WhatsApp instances")
            return backup_data

        except Exception as e:
            logger.error(f"WhatsApp auth backup failed: {e}")
            raise

    async def backup_configuration(self) -> Dict[str, Any]:
        """
        Backs up configuration files not stored in Supabase

        Returns:
            Dict containing configuration backup
        """
        try:
            backup_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "configuration",
                "files": {}
            }

            # Configuration files to backup
            config_files = [
                ".env",
                "config/production.json",
                "config/calendar_providers.json",
                "config/webhook_endpoints.json"
            ]

            for config_file in config_files:
                file_path = Path(config_file)
                if file_path.exists():
                    async with aiofiles.open(file_path, 'r') as f:
                        content = await f.read()
                        # Only encrypt .env file
                        if config_file == ".env" and self.cipher:
                            content = self.cipher.encrypt(content.encode()).decode()
                        backup_data["files"][config_file] = content

            logger.info(f"Backed up {len(backup_data['files'])} configuration files")
            return backup_data

        except Exception as e:
            logger.error(f"Configuration backup failed: {e}")
            raise

    async def create_complete_backup(self, tenant_id: Optional[str] = None) -> str:
        """
        Creates a complete backup of all external data

        Args:
            tenant_id: Optional tenant ID for tenant-specific backup

        Returns:
            Path to the backup file
        """
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_name = f"external_backup_{tenant_id or 'all'}_{timestamp}"

            # Collect all backups
            backups = {
                "metadata": {
                    "version": "1.0",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tenant_id": tenant_id,
                    "encrypted": self.cipher is not None
                },
                "redis": await self.backup_redis_data(tenant_id),
                "whatsapp": await self.backup_whatsapp_auth(),
                "configuration": await self.backup_configuration()
            }

            # Save as JSON
            backup_file = self.backup_dir / f"{backup_name}.json"
            async with aiofiles.open(backup_file, 'w') as f:
                await f.write(json.dumps(backups, indent=2))

            # Also create compressed archive
            tar_file = self.backup_dir / f"{backup_name}.tar.gz"
            with tarfile.open(tar_file, "w:gz") as tar:
                tar.add(backup_file, arcname=f"{backup_name}.json")

            logger.info(f"Complete backup created: {tar_file}")
            return str(tar_file)

        except Exception as e:
            logger.error(f"Complete backup failed: {e}")
            raise

    async def restore_redis_data(self, backup_data: Dict[str, Any]) -> bool:
        """
        Restores Redis data from backup

        Args:
            backup_data: Redis backup data

        Returns:
            True if successful
        """
        try:
            # Verify checksum
            data_str = json.dumps(backup_data["data"], sort_keys=True)
            checksum = hashlib.sha256(data_str.encode()).hexdigest()

            if checksum != backup_data.get("checksum"):
                logger.error("Backup checksum verification failed")
                return False

            # Restore each key
            pipe = self.redis_client.pipeline()
            restored_count = 0

            for key, data in backup_data["data"].items():
                pipe.set(key, data["value"])
                if data.get("ttl"):
                    pipe.expire(key, data["ttl"])
                restored_count += 1

            await pipe.execute()
            logger.info(f"Restored {restored_count} Redis keys")
            return True

        except Exception as e:
            logger.error(f"Redis restore failed: {e}")
            return False

    async def restore_whatsapp_auth(self, backup_data: Dict[str, Any]) -> bool:
        """
        Restores WhatsApp authentication from backup

        Args:
            backup_data: WhatsApp auth backup data

        Returns:
            True if successful
        """
        try:
            evolution_data_dir = Path("/data/evolution")
            if not evolution_data_dir.exists():
                evolution_data_dir = Path("./evolution_data")

            evolution_data_dir.mkdir(parents=True, exist_ok=True)

            for instance in backup_data["instances"]:
                instance_dir = evolution_data_dir / instance["name"]
                instance_dir.mkdir(parents=True, exist_ok=True)

                for filename, content in instance["files"].items():
                    file_path = instance_dir / filename

                    # Decrypt if encrypted
                    if self.cipher and content.startswith("gAAAAA"):  # Fernet encrypted marker
                        try:
                            content = self.cipher.decrypt(content.encode()).decode()
                        except Exception:
                            logger.warning(f"Failed to decrypt {filename}, using as-is")

                    async with aiofiles.open(file_path, 'w') as f:
                        await f.write(content)

            logger.info(f"Restored {len(backup_data['instances'])} WhatsApp instances")
            return True

        except Exception as e:
            logger.error(f"WhatsApp auth restore failed: {e}")
            return False

    async def restore_configuration(self, backup_data: Dict[str, Any]) -> bool:
        """
        Restores configuration files from backup

        Args:
            backup_data: Configuration backup data

        Returns:
            True if successful
        """
        try:
            for filename, content in backup_data["files"].items():
                file_path = Path(filename)
                file_path.parent.mkdir(parents=True, exist_ok=True)

                # Decrypt if needed
                if filename == ".env" and self.cipher and content.startswith("gAAAAA"):
                    try:
                        content = self.cipher.decrypt(content.encode()).decode()
                    except Exception:
                        logger.warning(f"Failed to decrypt {filename}, using as-is")

                async with aiofiles.open(file_path, 'w') as f:
                    await f.write(content)

            logger.info(f"Restored {len(backup_data['files'])} configuration files")
            return True

        except Exception as e:
            logger.error(f"Configuration restore failed: {e}")
            return False

    async def restore_from_backup(self, backup_path: str) -> bool:
        """
        Restores all external data from a backup file

        Args:
            backup_path: Path to the backup file

        Returns:
            True if successful
        """
        try:
            backup_file = Path(backup_path)

            # Extract if compressed
            if backup_file.suffix == ".gz":
                with tarfile.open(backup_file, "r:gz") as tar:
                    tar.extractall(path=self.backup_dir)
                    # Find the JSON file
                    json_file = list(self.backup_dir.glob("*.json"))[0]
                    backup_file = json_file

            # Load backup data
            async with aiofiles.open(backup_file, 'r') as f:
                backups = json.loads(await f.read())

            # Restore each component
            results = []

            if "redis" in backups:
                results.append(await self.restore_redis_data(backups["redis"]))

            if "whatsapp" in backups:
                results.append(await self.restore_whatsapp_auth(backups["whatsapp"]))

            if "configuration" in backups:
                results.append(await self.restore_configuration(backups["configuration"]))

            success = all(results)
            if success:
                logger.info(f"Successfully restored from {backup_path}")
            else:
                logger.error(f"Partial restore from {backup_path}")

            return success

        except Exception as e:
            logger.error(f"Restore from backup failed: {e}")
            return False

    async def list_backups(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists available backups

        Args:
            tenant_id: Optional tenant ID to filter backups

        Returns:
            List of backup metadata
        """
        backups = []

        for backup_file in self.backup_dir.glob("*.json"):
            if tenant_id and f"_{tenant_id}_" not in backup_file.name:
                continue

            try:
                async with aiofiles.open(backup_file, 'r') as f:
                    data = json.loads(await f.read())
                    backups.append({
                        "file": backup_file.name,
                        "path": str(backup_file),
                        "timestamp": data["metadata"]["timestamp"],
                        "tenant_id": data["metadata"].get("tenant_id"),
                        "size": backup_file.stat().st_size
                    })
            except Exception as e:
                logger.warning(f"Could not read backup {backup_file}: {e}")

        # Sort by timestamp descending
        backups.sort(key=lambda x: x["timestamp"], reverse=True)
        return backups

    async def cleanup_old_backups(self, days_to_keep: int = 7) -> int:
        """
        Removes backups older than specified days

        Args:
            days_to_keep: Number of days to keep backups

        Returns:
            Number of backups deleted
        """
        from datetime import timedelta

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
        deleted_count = 0

        for backup_file in self.backup_dir.glob("*"):
            if backup_file.stat().st_mtime < cutoff_date.timestamp():
                backup_file.unlink()
                deleted_count += 1
                logger.info(f"Deleted old backup: {backup_file.name}")

        return deleted_count