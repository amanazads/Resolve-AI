import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import motor.motor_asyncio
try:
    from app.config import settings
except ImportError:
    from ..config import settings

logger = logging.getLogger(__name__)

class MongoDBManager:
    """
    Manages MongoDB persistence for chat sessions, messages, and escalations.
    Includes an in-memory fallback store if MongoDB server is unreachable.
    """
    def __init__(self):
        self.client = None
        self.db = None
        self.is_connected = False
        
        # In-memory fallbacks
        self._memory_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_escalations: List[Dict[str, Any]] = []
        self._memory_plans: Dict[str, Dict[str, Any]] = {}
        self._memory_campaigns: Dict[str, Dict[str, Any]] = {}
        self._memory_jobs: Dict[str, Dict[str, Any]] = {}
        self._memory_idempotency: Dict[str, Dict[str, Any]] = {}
        self._memory_contacts: Dict[str, Dict[str, Any]] = {}
        self._memory_datasets: Dict[str, Dict[str, Any]] = {}
        self._memory_integrations: Dict[str, Dict[str, Any]] = {}
        self._memory_campaign_jobs: Dict[str, Dict[str, Any]] = {}
        self._memory_campaign_progress: Dict[str, Dict[str, Any]] = {}
        self._memory_automation_runs: Dict[str, Dict[str, Any]] = {}
        self._memory_permissions: Dict[str, Dict[str, Any]] = {}
        self._memory_audit: List[Dict[str, Any]] = []
        self._memory_suppressions: Dict[str, Dict[str, Any]] = {}
        self._memory_safety_audits: List[Dict[str, Any]] = []
        self._memory_campaign_activities: List[Dict[str, Any]] = []
        self._memory_tasks: Dict[str, Dict[str, Any]] = {}
        self._memory_task_jobs: Dict[str, Dict[str, Any]] = {}
        self._memory_task_events: List[Dict[str, Any]] = []

    async def connect(self):
        try:
            self.client = motor.motor_asyncio.AsyncIOMotorClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000
            )
            # Test connection
            await self.client.admin.command('ping')
            self.db = self.client[settings.MONGODB_DB_NAME]
            self.is_connected = True
            logger.info(f"Connected to MongoDB Atlas database '{settings.MONGODB_DB_NAME}' successfully.")
            await self._ensure_all_indexes()
        except Exception as e:
            self.is_connected = False
            logger.warning(f"MongoDB not reachable ({e}). Operating with in-memory persistence fallback.")

    async def close(self):
        if self.client:
            self.client.close()
            logger.info("Closed MongoDB client connection.")

    async def clear_all_test_data(self):
        """Cleans all test data from both in-memory structures and live MongoDB database."""
        for attr in (
            "_memory_messages",
            "_memory_escalations",
            "_memory_plans",
            "_memory_campaigns",
            "_memory_jobs",
            "_memory_idempotency",
            "_memory_contacts",
            "_memory_datasets",
            "_memory_integrations",
            "_memory_campaign_jobs",
            "_memory_campaign_progress",
            "_memory_automation_runs",
            "_memory_permissions",
            "_memory_audit",
            "_memory_suppressions",
            "_memory_safety_audits",
            "_memory_campaign_activities",
            "_memory_tasks",
            "_memory_task_jobs",
            "_memory_task_events",
        ):
            store = getattr(self, attr, None)
            if store is not None and hasattr(store, "clear"):
                store.clear()

        if self.is_connected and self.db is not None:
            try:
                collections = [
                    "tasks", "task_jobs", "task_events", "contacts", "datasets",
                    "permissions", "audit_log", "suppressions", "campaigns", "jobs", "idempotency"
                ]
                for coll in collections:
                    await self.db[coll].delete_many({})
            except Exception as e:
                logger.warning(f"Error clearing test collections: {e}")


    # ==================== Index Management ====================

    async def _ensure_all_indexes(self):
        """
        Creates indexes for every collection on startup.
        Idempotent: MongoDB ignores requests for identical indexes that already exist.
        Calls _campaign_jobs_indexes() for the hot-path campaign_jobs collection first.
        """
        if not (self.is_connected and self.db is not None):
            return
        try:
            await self._campaign_jobs_indexes()
        except Exception as e:
            logger.warning(f"campaign_jobs indexes error: {e}")

        index_defs = {
            # ---- campaigns ----
            "campaigns": [
                [("campaign_id", 1)],                        # unique campaign lookup
                [("owner_id", 1), ("created_at", -1)],       # user's campaign list
                [("status", 1), ("created_at", -1)],         # status filtering
            ],
            # ---- automation jobs (generic worker queue) ----
            "jobs": [
                [("id", 1)],                                  # PK lookup
                [("campaign_id", 1), ("status", 1)],          # campaign job queries
                [("status", 1), ("created_at", 1)],           # queue claim order
                [("lease_expires_at", 1)],                    # stale lease recovery
            ],
            # ---- idempotency records ----
            "idempotency": [
                [("idempotency_key", 1)],                     # unique guard
            ],
            # ---- contacts ----
            "contacts": [
                [("contact_id", 1)],                          # PK lookup
                [("dataset_id", 1), ("contact_type", 1)],     # campaign audience queries
                [("dataset_id", 1), ("is_valid", 1)],         # valid contacts for a dataset
                [("email", 1)],                               # duplicate & suppression check
            ],
            # ---- datasets ----
            "datasets": [
                [("dataset_id", 1)],
                [("uploaded_at", -1)],
            ],
            # ---- suppression list ----
            "suppressions": [
                [("email", 1)],                               # unique; fast pre-send check
                [("reason", 1)],
            ],
            # ---- safety audits ----
            "safety_audits": [
                [("campaign_id", 1), ("timestamp", -1)],
                [("event_type", 1), ("timestamp", -1)],
            ],
            # ---- campaign activity events (monitoring / SSE) ----
            "campaign_activity_events": [
                [("campaign_id", 1), ("timestamp", -1)],
                [("event_type", 1)],
            ],
            # ---- permission grants ----
            "permission_grants": [
                [("grant_id", 1)],
                [("user_id", 1), ("integration", 1), ("revoked", 1)],
                [("campaign_id", 1)],
            ],
            # ---- audit events ----
            "audit_events": [
                [("user_id", 1), ("at", -1)],
                [("campaign_id", 1), ("at", -1)],
                [("grant_id", 1)],
            ],
            # ---- automation runs ----
            "automation_runs": [
                [("run_id", 1)],
                [("phase", 1), ("created_at", -1)],
            ],
            # ---- campaign progress ----
            "campaign_progress": [
                [("campaign_id", 1)],
            ],
            # ---- integration accounts ----
            "integration_accounts": [
                [("provider", 1), ("account_id", 1)],
            ],
            # ---- plans ----
            "plans": [
                [("id", 1)],
            ],
            # ---- general execution tasks ----
            "tasks": [
                [("task_id", 1)],
                [("user_id", 1), ("created_at", -1)],
                [("status", 1), ("created_at", -1)],
            ],
            "task_jobs": [
                [("job_id", 1)],
                [("task_id", 1), ("status", 1)],
                [("idempotency_key", 1)],
                [("status", 1), ("created_at", 1)],
                [("lease_expires_at", 1)],
            ],
            "task_events": [
                [("task_id", 1), ("timestamp", -1)],
                [("event_id", 1)],
            ],
            # ---- chat messages ----
            "messages": [
                [("session_id", 1), ("timestamp", 1)],
            ],
        }

        for collection, specs in index_defs.items():
            for spec in specs:
                try:
                    await self.db[collection].create_index(spec)
                except Exception as e:
                    logger.warning(f"Index creation on {collection} {spec} failed: {e}")

        logger.info("MongoDB indexes ensured for all collections.")

    # (Kept for backward-compat if anything imports it directly.)
    async def _campaign_jobs_indexes(self):
        """Creates the indexes the claim query and the uniqueness guard rely on."""
        if not (self.is_connected and self.db is not None):
            return
        try:
            await self.db.campaign_jobs.create_index("idempotency_key", unique=True)
            await self.db.campaign_jobs.create_index([("campaign_id", 1), ("status", 1)])
            await self.db.campaign_jobs.create_index(
                [("status", 1), ("next_attempt_at", 1), ("lease_expires_at", 1)]
            )
        except Exception as e:
            logger.warning(f"Could not ensure campaign_jobs indexes: {e}")

    # ==================== Plans Persistence ====================


    async def save_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        plan_id = plan.get("id")
        plan_copy = dict(plan)
        if self.is_connected and self.db is not None:
            try:
                await self.db.plans.update_one(
                    {"id": plan_id},
                    {"$set": plan_copy},
                    upsert=True
                )
                return plan_copy
            except Exception as e:
                logger.error(f"Error saving plan to MongoDB: {e}")
        self._memory_plans[plan_id] = plan_copy
        return plan_copy

    async def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                plan = await self.db.plans.find_one({"id": plan_id})
                if plan:
                    plan.pop("_id", None)
                    return plan
            except Exception as e:
                logger.error(f"Error getting plan from MongoDB: {e}")
        return self._memory_plans.get(plan_id)

    # ==================== Campaigns Persistence ====================

    async def save_campaign(self, campaign: Dict[str, Any]) -> Dict[str, Any]:
        campaign_id = campaign.get("campaign_id") or campaign.get("id")
        camp_copy = dict(campaign)
        if "id" not in camp_copy:
            camp_copy["id"] = campaign_id
        if "campaign_id" not in camp_copy:
            camp_copy["campaign_id"] = campaign_id
        if self.is_connected and self.db is not None:
            try:
                await self.db.campaigns.update_one(
                    {"$or": [{"campaign_id": campaign_id}, {"id": campaign_id}]},
                    {"$set": camp_copy},
                    upsert=True
                )
                return camp_copy
            except Exception as e:
                logger.error(f"Error saving campaign to MongoDB: {e}")
        self._memory_campaigns[campaign_id] = camp_copy
        return camp_copy

    async def get_campaign(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                camp = await self.db.campaigns.find_one({"$or": [{"campaign_id": campaign_id}, {"id": campaign_id}]})
                if camp:
                    camp.pop("_id", None)
                    return camp
            except Exception as e:
                logger.error(f"Error getting campaign from MongoDB: {e}")
        return self._memory_campaigns.get(campaign_id)

    async def update_campaign(self, campaign_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        updates["updated_at"] = updates.get("updated_at") or datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.campaigns.update_one(
                    {"$or": [{"campaign_id": campaign_id}, {"id": campaign_id}]},
                    {"$set": updates}
                )
                return await self.get_campaign(campaign_id)
            except Exception as e:
                logger.error(f"Error updating campaign in MongoDB: {e}")
        if campaign_id in self._memory_campaigns:
            self._memory_campaigns[campaign_id].update(updates)
            return self._memory_campaigns[campaign_id]
        return None

    async def list_campaigns(self) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.campaigns.find().sort("created_at", -1)
                campaigns = await cursor.to_list(length=200)
                for c in campaigns:
                    c.pop("_id", None)
                return campaigns
            except Exception as e:
                logger.error(f"Error listing campaigns from MongoDB: {e}")
        return list(self._memory_campaigns.values())

    # ==================== Jobs Persistence & Queue ====================

    async def save_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = job.get("id")
        job_copy = dict(job)
        if self.is_connected and self.db is not None:
            try:
                await self.db.jobs.update_one(
                    {"id": job_id},
                    {"$set": job_copy},
                    upsert=True
                )
                return job_copy
            except Exception as e:
                logger.error(f"Error saving job to MongoDB: {e}")
        self._memory_jobs[job_id] = job_copy
        return job_copy

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                job = await self.db.jobs.find_one({"id": job_id})
                if job:
                    job.pop("_id", None)
                    return job
            except Exception as e:
                logger.error(f"Error getting job from MongoDB: {e}")
        return self._memory_jobs.get(job_id)

    async def get_jobs_by_campaign(self, campaign_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                query: Dict[str, Any] = {"campaign_id": campaign_id}
                if status:
                    query["status"] = status
                cursor = self.db.jobs.find(query).sort("created_at", 1)
                jobs = await cursor.to_list(length=1000)
                for j in jobs:
                    j.pop("_id", None)
                return jobs
            except Exception as e:
                logger.error(f"Error getting campaign jobs from MongoDB: {e}")
        results = [j for j in self._memory_jobs.values() if j.get("campaign_id") == campaign_id]
        if status:
            results = [j for j in results if j.get("status") == status]
        return results

    async def update_job(self, job_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        updates["updated_at"] = updates.get("updated_at") or datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.jobs.update_one(
                    {"id": job_id},
                    {"$set": updates}
                )
                return await self.get_job(job_id)
            except Exception as e:
                logger.error(f"Error updating job in MongoDB: {e}")
        if job_id in self._memory_jobs:
            self._memory_jobs[job_id].update(updates)
            return self._memory_jobs[job_id]
        return None

    async def find_and_lock_next_job(self, worker_id: str, lease_duration_seconds: int = 30, campaign_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        lease_expires = now + timedelta(seconds=lease_duration_seconds)
        now_str = now.isoformat()
        lease_str = lease_expires.isoformat()

        if self.is_connected and self.db is not None:
            try:
                query: Dict[str, Any] = {
                    "status": "READY",
                    "$or": [
                        {"lease_expires_at": None},
                        {"lease_expires_at": {"$lt": now_str}}
                    ]
                }
                if campaign_id:
                    query["campaign_id"] = campaign_id

                update = {
                    "$set": {
                        "status": "RUNNING",
                        "started_at": now_str,
                        "lease_owner": worker_id,
                        "lease_expires_at": lease_str,
                        "updated_at": now_str
                    },
                    "$inc": {"attempt_count": 1}
                }
                from pymongo import ReturnDocument
                job = await self.db.jobs.find_one_and_update(query, update, return_document=ReturnDocument.AFTER)
                if job:
                    job.pop("_id", None)
                    return job
                return None
            except Exception as e:
                logger.error(f"Error locking next job in MongoDB: {e}")

        # In-memory fallback
        for job_id, job in self._memory_jobs.items():
            if campaign_id and job.get("campaign_id") != campaign_id:
                continue
            if job.get("status") == "READY":
                lease_at = job.get("lease_expires_at")
                if lease_at is None or str(lease_at) < now_str:
                    job["status"] = "RUNNING"
                    job["started_at"] = now_str
                    job["lease_owner"] = worker_id
                    job["lease_expires_at"] = lease_str
                    job["updated_at"] = now_str
                    job["attempt_count"] = job.get("attempt_count", 0) + 1
                    return dict(job)
        return None

    async def recover_stale_jobs(self, lease_timeout_seconds: int = 30) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        recovered_count = 0
        if self.is_connected and self.db is not None:
            try:
                # Find RUNNING jobs with expired leases
                cursor = self.db.jobs.find({
                    "status": "RUNNING",
                    "lease_expires_at": {"$lt": now_str}
                })
                stale_jobs = await cursor.to_list(length=500)
                for job in stale_jobs:
                    attempts = job.get("attempt_count", 1)
                    max_r = job.get("max_retries", 3)
                    if attempts <= max_r:
                        await self.db.jobs.update_one(
                            {"id": job["id"]},
                            {"$set": {"status": "READY", "lease_owner": None, "lease_expires_at": None, "updated_at": now_str}}
                        )
                    else:
                        await self.db.jobs.update_one(
                            {"id": job["id"]},
                            {"$set": {"status": "FAILED", "error": "Max retries exceeded upon lease expiry", "updated_at": now_str}}
                        )
                    recovered_count += 1
                return recovered_count
            except Exception as e:
                logger.error(f"Error recovering stale jobs in MongoDB: {e}")

        # In-memory fallback
        for job_id, job in self._memory_jobs.items():
            if job.get("status") == "RUNNING":
                lease_at = job.get("lease_expires_at")
                if lease_at and str(lease_at) < now_str:
                    attempts = job.get("attempt_count", 1)
                    max_r = job.get("max_retries", 3)
                    if attempts <= max_r:
                        job["status"] = "READY"
                        job["lease_owner"] = None
                        job["lease_expires_at"] = None
                    else:
                        job["status"] = "FAILED"
                        job["error"] = "Max retries exceeded upon lease expiry"
                    job["updated_at"] = now_str
                    recovered_count += 1
        return recovered_count

    # ==================== Idempotency Records ====================

    async def save_idempotency_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        key = record.get("idempotency_key")
        rec_copy = dict(record)
        rec_copy["created_at"] = rec_copy.get("created_at") or datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.idempotency.update_one(
                    {"idempotency_key": key},
                    {"$set": rec_copy},
                    upsert=True
                )
                return rec_copy
            except Exception as e:
                logger.error(f"Error saving idempotency record to MongoDB: {e}")
        self._memory_idempotency[key] = rec_copy
        return rec_copy

    async def get_idempotency_record(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                rec = await self.db.idempotency.find_one({"idempotency_key": idempotency_key})
                if rec:
                    rec.pop("_id", None)
                    return rec
            except Exception as e:
                logger.error(f"Error fetching idempotency record from MongoDB: {e}")
        return self._memory_idempotency.get(idempotency_key)

    # ==================== Existing Chat & Escalations ====================

    async def save_message(self, session_id: str, message: Dict[str, Any]):
        message["timestamp"] = message.get("timestamp") or datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.messages.insert_one({"session_id": session_id, **message})
                return
            except Exception as e:
                logger.error(f"Error saving message to MongoDB: {e}")
        
        # Fallback in-memory
        if session_id not in self._memory_messages:
            self._memory_messages[session_id] = []
        self._memory_messages[session_id].append(message)

    async def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.messages.find({"session_id": session_id}).sort("timestamp", 1)
                messages = await cursor.to_list(length=100)
                for m in messages:
                    m.pop("_id", None)
                return messages
            except Exception as e:
                logger.error(f"Error reading history from MongoDB: {e}")

        # Fallback in-memory
        return self._memory_messages.get(session_id, [])

    async def save_escalation(self, record: Dict[str, Any]):
        record["timestamp"] = record.get("timestamp") or datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.escalations.insert_one(record)
                return
            except Exception as e:
                logger.error(f"Error saving escalation to MongoDB: {e}")
        
        self._memory_escalations.append(record)

    async def get_escalations(self) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.escalations.find().sort("timestamp", -1)
                records = await cursor.to_list(length=100)
                for r in records:
                    r.pop("_id", None)
                return records
            except Exception as e:
                logger.error(f"Error fetching escalations from MongoDB: {e}")

        return self._memory_escalations

    async def save_contact(self, contact: Dict[str, Any]) -> str:
        """Saves a single contact."""
        await self.save_contacts_batch([contact])
        return contact.get("contact_id", "")

    async def list_contacts(
        self,
        filters: Optional[Dict[str, Any]] = None,
        filter_query: Optional[Dict[str, Any]] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Convenience alias for querying contacts."""
        f = filters if filters is not None else (filter_query or {})
        return await self.query_contacts(filter_query=f, skip=skip, limit=limit)

    async def save_contacts_batch(self, contacts: List[Dict[str, Any]]) -> int:
        """Saves a batch of normalized contacts."""
        if not contacts:
            return 0
        if self.is_connected and self.db is not None:
            try:
                from pymongo import ReplaceOne
                operations = [
                    ReplaceOne({"contact_id": c["contact_id"]}, dict(c), upsert=True)
                    for c in contacts
                ]
                res = await self.db.contacts.bulk_write(operations, ordered=False)
                return len(contacts)
            except Exception as e:
                logger.error(f"Error bulk saving contacts to MongoDB: {e}")

        # In-memory fallback
        for c in contacts:
            self._memory_contacts[c["contact_id"]] = dict(c)
        return len(contacts)

    async def get_contact(self, contact_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                c = await self.db.contacts.find_one({"contact_id": contact_id})
                if c:
                    c.pop("_id", None)
                    return c
            except Exception as e:
                logger.error(f"Error getting contact from MongoDB: {e}")
        return self._memory_contacts.get(contact_id)

    async def query_contacts(
        self,
        filter_query: Dict[str, Any],
        skip: int = 0,
        limit: int = 20,
        sort_field: str = "created_at",
        sort_order: int = -1
    ) -> List[Dict[str, Any]]:
        """Queries contacts with filtering, pagination, and sorting."""
        if self.is_connected and self.db is not None:
            try:
                mongo_q: Dict[str, Any] = {}
                if "dataset_id" in filter_query:
                    mongo_q["dataset_id"] = filter_query["dataset_id"]
                if "contact_type" in filter_query:
                    mongo_q["contact_type"] = filter_query["contact_type"]
                if "is_valid" in filter_query:
                    mongo_q["is_valid"] = filter_query["is_valid"]
                if "search" in filter_query and filter_query["search"]:
                    s = filter_query["search"]
                    mongo_q["$or"] = [
                        {"full_name": {"$regex": s, "$options": "i"}},
                        {"email": {"$regex": s, "$options": "i"}},
                        {"company": {"$regex": s, "$options": "i"}},
                        {"role": {"$regex": s, "$options": "i"}}
                    ]

                cursor = self.db.contacts.find(mongo_q).sort(sort_field, sort_order).skip(skip).limit(limit)
                results = await cursor.to_list(length=limit)
                for r in results:
                    r.pop("_id", None)
                return results
            except Exception as e:
                logger.error(f"Error querying contacts in MongoDB: {e}")

        # In-memory fallback
        matched = list(self._memory_contacts.values())
        if "dataset_id" in filter_query:
            matched = [c for c in matched if c.get("dataset_id") == filter_query["dataset_id"]]
        if "contact_type" in filter_query:
            matched = [c for c in matched if c.get("contact_type") == filter_query["contact_type"]]
        if "is_valid" in filter_query:
            matched = [c for c in matched if c.get("is_valid") == filter_query["is_valid"]]
        if "search" in filter_query and filter_query["search"]:
            s = filter_query["search"].lower()
            matched = [
                c for c in matched
                if s in (c.get("full_name") or "").lower()
                or s in (c.get("email") or "").lower()
                or s in (c.get("company") or "").lower()
                or s in (c.get("role") or "").lower()
            ]

        # Sorting
        reverse = (sort_order == -1)
        matched.sort(key=lambda x: str(x.get(sort_field, "")), reverse=reverse)
        return matched[skip : skip + limit]

    async def count_contacts(self, filter_query: Dict[str, Any]) -> int:
        """Counts contacts matching filter."""
        if self.is_connected and self.db is not None:
            try:
                mongo_q: Dict[str, Any] = {}
                if "dataset_id" in filter_query:
                    mongo_q["dataset_id"] = filter_query["dataset_id"]
                if "contact_type" in filter_query:
                    mongo_q["contact_type"] = filter_query["contact_type"]
                if "is_valid" in filter_query:
                    mongo_q["is_valid"] = filter_query["is_valid"]
                if "search" in filter_query and filter_query["search"]:
                    s = filter_query["search"]
                    mongo_q["$or"] = [
                        {"full_name": {"$regex": s, "$options": "i"}},
                        {"email": {"$regex": s, "$options": "i"}},
                        {"company": {"$regex": s, "$options": "i"}},
                        {"role": {"$regex": s, "$options": "i"}}
                    ]
                return await self.db.contacts.count_documents(mongo_q)
            except Exception as e:
                logger.error(f"Error counting contacts in MongoDB: {e}")

        # In-memory fallback
        matched = list(self._memory_contacts.values())
        if "dataset_id" in filter_query:
            matched = [c for c in matched if c.get("dataset_id") == filter_query["dataset_id"]]
        if "contact_type" in filter_query:
            matched = [c for c in matched if c.get("contact_type") == filter_query["contact_type"]]
        if "is_valid" in filter_query:
            matched = [c for c in matched if c.get("is_valid") == filter_query["is_valid"]]
        if "search" in filter_query and filter_query["search"]:
            s = filter_query["search"].lower()
            matched = [
                c for c in matched
                if s in (c.get("full_name") or "").lower()
                or s in (c.get("email") or "").lower()
                or s in (c.get("company") or "").lower()
                or s in (c.get("role") or "").lower()
            ]
        return len(matched)

    # ============ Campaign Execution Jobs Persistence ============
    # A separate collection from `jobs`: campaign jobs carry the outbound-message
    # lifecycle (PENDING/GENERATING/READY/SENDING/SENT/...) rather than the
    # generic automation lifecycle, and the two must not share a claim query.


    async def insert_campaign_jobs(self, jobs: List[Dict[str, Any]]) -> int:
        """
        Inserts jobs, skipping any whose idempotency_key already exists.
        Returns the number actually inserted, so a repeated enqueue is a no-op.
        """
        if not jobs:
            return 0
        inserted = 0
        if self.is_connected and self.db is not None:
            try:
                for job in jobs:
                    result = await self.db.campaign_jobs.update_one(
                        {"idempotency_key": job["idempotency_key"]},
                        {"$setOnInsert": job},
                        upsert=True
                    )
                    if result.upserted_id is not None:
                        inserted += 1
                return inserted
            except Exception as e:
                logger.error(f"Error inserting campaign jobs in MongoDB: {e}")

        existing_keys = {j.get("idempotency_key") for j in self._memory_campaign_jobs.values()}
        for job in jobs:
            if job["idempotency_key"] in existing_keys:
                continue
            self._memory_campaign_jobs[job["id"]] = dict(job)
            existing_keys.add(job["idempotency_key"])
            inserted += 1
        return inserted

    async def get_campaign_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                doc = await self.db.campaign_jobs.find_one({"id": job_id}, {"_id": 0})
                if doc:
                    return doc
            except Exception as e:
                logger.error(f"Error reading campaign job from MongoDB: {e}")
        job = self._memory_campaign_jobs.get(job_id)
        return dict(job) if job else None

    async def get_campaign_job_by_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                doc = await self.db.campaign_jobs.find_one(
                    {"idempotency_key": idempotency_key}, {"_id": 0}
                )
                if doc:
                    return doc
            except Exception as e:
                logger.error(f"Error reading campaign job by key from MongoDB: {e}")
        for job in self._memory_campaign_jobs.values():
            if job.get("idempotency_key") == idempotency_key:
                return dict(job)
        return None

    async def update_campaign_job(
        self,
        job_id: str,
        updates: Dict[str, Any],
        expected_status: Optional[str] = None,
        expected_lease_owner: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Conditionally updates a job.

        `expected_status` / `expected_lease_owner` make the write a
        compare-and-set: a worker whose lease has already been stolen cannot
        overwrite the state of the worker that now owns the job. Returns None
        when the condition did not hold.
        """
        query: Dict[str, Any] = {"id": job_id}
        if expected_status is not None:
            query["status"] = expected_status
        if expected_lease_owner is not None:
            query["lease_owner"] = expected_lease_owner

        if self.is_connected and self.db is not None:
            try:
                from pymongo import ReturnDocument
                doc = await self.db.campaign_jobs.find_one_and_update(
                    query,
                    {"$set": updates},
                    return_document=ReturnDocument.AFTER,
                    projection={"_id": 0}
                )
                return doc
            except Exception as e:
                logger.error(f"Error updating campaign job in MongoDB: {e}")

        job = self._memory_campaign_jobs.get(job_id)
        if job is None:
            return None
        if expected_status is not None and job.get("status") != expected_status:
            return None
        if expected_lease_owner is not None and job.get("lease_owner") != expected_lease_owner:
            return None
        job.update(updates)
        return dict(job)

    async def claim_next_campaign_job(
        self,
        worker_id: str,
        claimable_statuses: List[str],
        in_flight_status: str,
        now_iso: str,
        lease_expires_iso: str,
        campaign_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically leases the next claimable job.

        The find-and-update is a single operation, so two workers racing for the
        same job produce exactly one winner; the loser sees the next job or None.
        A job is claimable when it is in a claimable status, its retry backoff
        has elapsed, and no live lease is held on it.
        """
        query: Dict[str, Any] = {
            "status": {"$in": claimable_statuses},
            "$and": [
                {"$or": [{"next_attempt_at": None}, {"next_attempt_at": {"$lte": now_iso}}]},
                {"$or": [{"lease_expires_at": None}, {"lease_expires_at": {"$lte": now_iso}}]},
            ],
        }
        if campaign_id:
            query["campaign_id"] = campaign_id

        updates = {
            "status": in_flight_status,
            "lease_owner": worker_id,
            "lease_expires_at": lease_expires_iso,
            "updated_at": now_iso,
        }

        if self.is_connected and self.db is not None:
            try:
                from pymongo import ReturnDocument
                doc = await self.db.campaign_jobs.find_one_and_update(
                    query,
                    {"$set": updates},
                    sort=[("created_at", 1)],
                    return_document=ReturnDocument.AFTER,
                    projection={"_id": 0}
                )
                return doc
            except Exception as e:
                logger.error(f"Error claiming campaign job in MongoDB: {e}")

        # In-memory fallback. Single-threaded event loop, and there is no await
        # between the check and the write, so this is atomic for the same reason.
        for job in sorted(
            self._memory_campaign_jobs.values(), key=lambda j: str(j.get("created_at", ""))
        ):
            if campaign_id and job.get("campaign_id") != campaign_id:
                continue
            if job.get("status") not in claimable_statuses:
                continue
            next_attempt = job.get("next_attempt_at")
            if next_attempt and str(next_attempt) > now_iso:
                continue
            lease = job.get("lease_expires_at")
            if lease and str(lease) > now_iso:
                continue
            job.update(updates)
            return dict(job)
        return None

    async def list_campaign_jobs(
        self,
        campaign_id: str,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"campaign_id": campaign_id}
        if status:
            query["status"] = status

        if self.is_connected and self.db is not None:
            try:
                cursor = (
                    self.db.campaign_jobs.find(query, {"_id": 0})
                    .sort("created_at", 1)
                    .skip(skip)
                    .limit(limit)
                )
                return await cursor.to_list(length=limit)
            except Exception as e:
                logger.error(f"Error listing campaign jobs in MongoDB: {e}")

        matched = [
            dict(j) for j in self._memory_campaign_jobs.values()
            if j.get("campaign_id") == campaign_id and (status is None or j.get("status") == status)
        ]
        matched.sort(key=lambda j: str(j.get("created_at", "")))
        return matched[skip : skip + limit]

    async def count_campaign_jobs_by_status(self, campaign_id: str) -> Dict[str, int]:
        """Returns {status: count} for one campaign."""
        if self.is_connected and self.db is not None:
            try:
                pipeline = [
                    {"$match": {"campaign_id": campaign_id}},
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                ]
                cursor = self.db.campaign_jobs.aggregate(pipeline)
                rows = await cursor.to_list(length=100)
                return {row["_id"]: row["count"] for row in rows}
            except Exception as e:
                logger.error(f"Error counting campaign jobs in MongoDB: {e}")

        counts: Dict[str, int] = {}
        for job in self._memory_campaign_jobs.values():
            if job.get("campaign_id") != campaign_id:
                continue
            key = job.get("status", "UNKNOWN")
            counts[key] = counts.get(key, 0) + 1
        return counts

    async def count_campaign_jobs_needing_review(self, campaign_id: str) -> int:
        if self.is_connected and self.db is not None:
            try:
                return await self.db.campaign_jobs.count_documents(
                    {"campaign_id": campaign_id, "requires_manual_review": True}
                )
            except Exception as e:
                logger.error(f"Error counting campaign jobs needing review: {e}")
        return sum(
            1 for j in self._memory_campaign_jobs.values()
            if j.get("campaign_id") == campaign_id and j.get("requires_manual_review")
        )

    async def find_expired_campaign_job_leases(
        self, now_iso: str, in_flight_statuses: List[str], limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Jobs a worker was holding when it died."""
        query = {
            "status": {"$in": in_flight_statuses},
            "lease_expires_at": {"$ne": None, "$lte": now_iso},
        }
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.campaign_jobs.find(query, {"_id": 0}).limit(limit)
                return await cursor.to_list(length=limit)
            except Exception as e:
                logger.error(f"Error finding expired campaign job leases: {e}")

        results = []
        for job in self._memory_campaign_jobs.values():
            lease = job.get("lease_expires_at")
            if job.get("status") in in_flight_statuses and lease and str(lease) <= now_iso:
                results.append(dict(job))
        return results[:limit]

    async def bulk_update_campaign_jobs(
        self, campaign_id: str, from_statuses: List[str], updates: Dict[str, Any]
    ) -> int:
        """Moves every job of a campaign in `from_statuses` to a new state."""
        if self.is_connected and self.db is not None:
            try:
                result = await self.db.campaign_jobs.update_many(
                    {"campaign_id": campaign_id, "status": {"$in": from_statuses}},
                    {"$set": updates}
                )
                return result.modified_count
            except Exception as e:
                logger.error(f"Error bulk updating campaign jobs in MongoDB: {e}")

        changed = 0
        for job in self._memory_campaign_jobs.values():
            if job.get("campaign_id") == campaign_id and job.get("status") in from_statuses:
                job.update(updates)
                changed += 1
        return changed

    # ============ Permission Grants & Audit Log ============
    # Grants record what a user authorized; the audit log records every decision
    # made under one and every action taken. Neither ever holds a credential --
    # OAuth material stays in the integration's own encrypted store.

    async def save_permission_grant(self, grant: Dict[str, Any]) -> Dict[str, Any]:
        grant_id = grant.get("grant_id")
        copy = dict(grant)
        if self.is_connected and self.db is not None:
            try:
                await self.db.permission_grants.update_one(
                    {"grant_id": grant_id}, {"$set": copy}, upsert=True
                )
                return copy
            except Exception as e:
                logger.error(f"Error saving permission grant in MongoDB: {e}")
        self._memory_permissions[grant_id] = copy
        return copy

    async def get_permission_grant(self, grant_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                doc = await self.db.permission_grants.find_one(
                    {"grant_id": grant_id}, {"_id": 0}
                )
                if doc:
                    return doc
            except Exception as e:
                logger.error(f"Error reading permission grant from MongoDB: {e}")
        grant = self._memory_permissions.get(grant_id)
        return dict(grant) if grant else None

    async def list_permission_grants(
        self,
        user_id: Optional[str] = None,
        integration: Optional[str] = None,
        campaign_id: Optional[str] = None,
        include_revoked: bool = False,
        skip: int = 0,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if user_id:
            query["user_id"] = user_id
        if integration:
            query["integration"] = integration
        if campaign_id:
            query["campaign_id"] = campaign_id
        if not include_revoked:
            query["revoked"] = False

        if self.is_connected and self.db is not None:
            try:
                cursor = (
                    self.db.permission_grants.find(query, {"_id": 0})
                    .sort("granted_at", -1)
                    .skip(skip)
                    .limit(limit)
                )
                return await cursor.to_list(length=limit)
            except Exception as e:
                logger.error(f"Error listing permission grants in MongoDB: {e}")

        matched = []
        for grant in self._memory_permissions.values():
            if user_id and grant.get("user_id") != user_id:
                continue
            if integration and grant.get("integration") != integration:
                continue
            if campaign_id and grant.get("campaign_id") != campaign_id:
                continue
            if not include_revoked and grant.get("revoked"):
                continue
            matched.append(dict(grant))
        matched.sort(key=lambda g: str(g.get("granted_at", "")), reverse=True)
        return matched[skip : skip + limit]

    async def count_permission_grants(
        self, user_id: Optional[str] = None, include_revoked: bool = False
    ) -> int:
        grants = await self.list_permission_grants(
            user_id=user_id, include_revoked=include_revoked, skip=0, limit=100000
        )
        return len(grants)

    async def save_audit_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        copy = dict(event)
        if self.is_connected and self.db is not None:
            try:
                await self.db.audit_events.insert_one(dict(copy))
                return copy
            except Exception as e:
                logger.error(f"Error writing audit event to MongoDB: {e}")
        self._memory_audit.append(copy)
        return copy

    async def list_audit_events(
        self,
        user_id: Optional[str] = None,
        event: Optional[str] = None,
        campaign_id: Optional[str] = None,
        grant_id: Optional[str] = None,
        integration: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        for field, value in (
            ("user_id", user_id),
            ("event", event),
            ("campaign_id", campaign_id),
            ("grant_id", grant_id),
            ("integration", integration),
        ):
            if value:
                query[field] = value

        if self.is_connected and self.db is not None:
            try:
                cursor = (
                    self.db.audit_events.find(query, {"_id": 0})
                    .sort("at", -1)
                    .skip(skip)
                    .limit(limit)
                )
                return await cursor.to_list(length=limit)
            except Exception as e:
                logger.error(f"Error listing audit events in MongoDB: {e}")

        matched = []
        for record in self._memory_audit:
            if any(
                value and record.get(field) != value
                for field, value in (
                    ("user_id", user_id),
                    ("event", event),
                    ("campaign_id", campaign_id),
                    ("grant_id", grant_id),
                    ("integration", integration),
                )
            ):
                continue
            matched.append(dict(record))
        matched.sort(key=lambda e: str(e.get("at", "")), reverse=True)
        return matched[skip : skip + limit]


    # ============ Automation Agent Runs Persistence ============
    # One document per automation workflow run. This is what makes the graph
    # resumable across a process restart: the run's phase and every structured
    # step output are stored here, not held in the graph's memory.

    async def save_automation_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        run_id = run.get("run_id")
        run_copy = dict(run)
        run_copy["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.automation_runs.update_one(
                    {"run_id": run_id}, {"$set": run_copy}, upsert=True
                )
                return run_copy
            except Exception as e:
                logger.error(f"Error saving automation run in MongoDB: {e}")
        self._memory_automation_runs[run_id] = run_copy
        return run_copy

    async def get_automation_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                doc = await self.db.automation_runs.find_one({"run_id": run_id}, {"_id": 0})
                if doc:
                    return doc
            except Exception as e:
                logger.error(f"Error reading automation run from MongoDB: {e}")
        run = self._memory_automation_runs.get(run_id)
        return dict(run) if run else None

    async def list_automation_runs(
        self, phase: Optional[str] = None, skip: int = 0, limit: int = 50
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if phase:
            query["phase"] = phase
        if self.is_connected and self.db is not None:
            try:
                cursor = (
                    self.db.automation_runs.find(query, {"_id": 0})
                    .sort("created_at", -1)
                    .skip(skip)
                    .limit(limit)
                )
                return await cursor.to_list(length=limit)
            except Exception as e:
                logger.error(f"Error listing automation runs in MongoDB: {e}")

        matched = [
            dict(r) for r in self._memory_automation_runs.values()
            if phase is None or r.get("phase") == phase
        ]
        matched.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return matched[skip : skip + limit]


    # ============ Campaign Progress Persistence ============

    async def save_campaign_progress(self, progress: Dict[str, Any]) -> Dict[str, Any]:
        campaign_id = progress.get("campaign_id")
        if self.is_connected and self.db is not None:
            try:
                await self.db.campaign_progress.update_one(
                    {"campaign_id": campaign_id}, {"$set": progress}, upsert=True
                )
                return progress
            except Exception as e:
                logger.error(f"Error saving campaign progress in MongoDB: {e}")
        self._memory_campaign_progress[campaign_id] = dict(progress)
        return progress

    async def get_campaign_progress(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                doc = await self.db.campaign_progress.find_one(
                    {"campaign_id": campaign_id}, {"_id": 0}
                )
                if doc:
                    return doc
            except Exception as e:
                logger.error(f"Error reading campaign progress from MongoDB: {e}")
        progress = self._memory_campaign_progress.get(campaign_id)
        return dict(progress) if progress else None


    # ==================== Integration Accounts Persistence ====================
    # Documents here carry OAuth credentials as ciphertext only. The encryption
    # and decryption happen in the integration service layer; this manager never
    # sees plaintext tokens and must never be given any.

    async def save_integration_account(self, account: Dict[str, Any]) -> Dict[str, Any]:
        key = f"{account.get('provider')}:{account.get('account_id')}"
        acc_copy = dict(account)
        acc_copy["updated_at"] = datetime.now(timezone.utc)
        if self.is_connected and self.db is not None:
            try:
                await self.db.integration_accounts.update_one(
                    {"provider": account.get("provider"), "account_id": account.get("account_id")},
                    {"$set": acc_copy},
                    upsert=True
                )
                return acc_copy
            except Exception as e:
                logger.error(f"MongoDB save_integration_account error: {e}")
        self._memory_integrations[key] = acc_copy
        return acc_copy

    async def get_integration_account(self, provider: str, account_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                doc = await self.db.integration_accounts.find_one(
                    {"provider": provider, "account_id": account_id},
                    {"_id": 0}
                )
                if doc:
                    return doc
            except Exception as e:
                logger.error(f"MongoDB get_integration_account error: {e}")
        return self._memory_integrations.get(f"{provider}:{account_id}")

    async def delete_integration_account(self, provider: str, account_id: str) -> bool:
        deleted = False
        if self.is_connected and self.db is not None:
            try:
                result = await self.db.integration_accounts.delete_one(
                    {"provider": provider, "account_id": account_id}
                )
                deleted = result.deleted_count > 0
            except Exception as e:
                logger.error(f"MongoDB delete_integration_account error: {e}")
        if self._memory_integrations.pop(f"{provider}:{account_id}", None) is not None:
            deleted = True
        return deleted


    # ==================== Datasets Persistence ====================

    async def save_dataset(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        d_id = dataset.get("dataset_id")
        d_copy = dict(dataset)
        if self.is_connected and self.db is not None:
            try:
                await self.db.datasets.update_one(
                    {"dataset_id": d_id},
                    {"$set": d_copy},
                    upsert=True
                )
                return d_copy
            except Exception as e:
                logger.error(f"Error saving dataset to MongoDB: {e}")
        self._memory_datasets[d_id] = d_copy
        return d_copy

    async def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                d = await self.db.datasets.find_one({"dataset_id": dataset_id})
                if d:
                    d.pop("_id", None)
                    return d
            except Exception as e:
                logger.error(f"Error getting dataset from MongoDB: {e}")
        return self._memory_datasets.get(dataset_id)

    async def list_datasets(self, skip: int = 0, limit: int = 20) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.datasets.find().sort("uploaded_at", -1).skip(skip).limit(limit)
                results = await cursor.to_list(length=limit)
                for r in results:
                    r.pop("_id", None)
                return results
            except Exception as e:
                logger.error(f"Error listing datasets from MongoDB: {e}")
        matched = list(self._memory_datasets.values())
        matched.sort(key=lambda x: str(x.get("uploaded_at", "")), reverse=True)
        return matched[skip : skip + limit]

    async def count_datasets(self) -> int:
        if self.is_connected and self.db is not None:
            try:
                return await self.db.datasets.count_documents({})
            except Exception as e:
                logger.error(f"Error counting datasets in MongoDB: {e}")
        return len(self._memory_datasets)
 
    # ==================== Suppression Persistence ====================

    async def save_suppression(self, record: Dict[str, Any]) -> Dict[str, Any]:
        email = str(record.get("email", "")).strip().lower()
        rec_copy = dict(record)
        rec_copy["email"] = email
        if "created_at" not in rec_copy:
            rec_copy["created_at"] = datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.suppressions.update_one(
                    {"email": email},
                    {"$set": rec_copy},
                    upsert=True
                )
                return rec_copy
            except Exception as e:
                logger.error(f"Error saving suppression to MongoDB: {e}")
        self._memory_suppressions[email] = rec_copy
        return rec_copy

    async def get_suppression(self, email: str) -> Optional[Dict[str, Any]]:
        norm_email = str(email).strip().lower()
        if self.is_connected and self.db is not None:
            try:
                rec = await self.db.suppressions.find_one({"email": norm_email})
                if rec:
                    rec.pop("_id", None)
                    return rec
            except Exception as e:
                logger.error(f"Error getting suppression from MongoDB: {e}")
        return self._memory_suppressions.get(norm_email)

    async def delete_suppression(self, email: str) -> bool:
        norm_email = str(email).strip().lower()
        deleted = False
        if self.is_connected and self.db is not None:
            try:
                res = await self.db.suppressions.delete_one({"email": norm_email})
                deleted = res.deleted_count > 0
            except Exception as e:
                logger.error(f"Error deleting suppression from MongoDB: {e}")
        if self._memory_suppressions.pop(norm_email, None) is not None:
            deleted = True
        return deleted

    async def list_suppressions(self, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.suppressions.find().sort("created_at", -1).skip(skip).limit(limit)
                items = await cursor.to_list(length=limit)
                for item in items:
                    item.pop("_id", None)
                return items
            except Exception as e:
                logger.error(f"Error listing suppressions from MongoDB: {e}")
        matched = list(self._memory_suppressions.values())
        matched.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
        return matched[skip : skip + limit]

    async def count_suppressions(self) -> int:
        if self.is_connected and self.db is not None:
            try:
                return await self.db.suppressions.count_documents({})
            except Exception as e:
                logger.error(f"Error counting suppressions in MongoDB: {e}")
        return len(self._memory_suppressions)

    # ==================== Safety Audit Persistence ====================

    async def save_safety_audit(self, event: Dict[str, Any]) -> Dict[str, Any]:
        evt_copy = dict(event)
        if "timestamp" not in evt_copy:
            evt_copy["timestamp"] = datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.safety_audits.insert_one(dict(evt_copy))
                evt_copy.pop("_id", None)
                return evt_copy
            except Exception as e:
                logger.error(f"Error saving safety audit to MongoDB: {e}")
        evt_copy.pop("_id", None)
        self._memory_safety_audits.append(evt_copy)
        return evt_copy

    async def list_safety_audits(
        self,
        campaign_id: Optional[str] = None,
        event_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                query: Dict[str, Any] = {}
                if campaign_id:
                    query["campaign_id"] = campaign_id
                if event_type:
                    query["event_type"] = event_type
                cursor = self.db.safety_audits.find(query).sort("timestamp", -1).skip(skip).limit(limit)
                items = await cursor.to_list(length=limit)
                for item in items:
                    item.pop("_id", None)
                return items
            except Exception as e:
                logger.error(f"Error listing safety audits from MongoDB: {e}")
        matched = [
            e for e in self._memory_safety_audits
            if (not campaign_id or e.get("campaign_id") == campaign_id)
            and (not event_type or e.get("event_type") == event_type)
        ]
        matched.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        return matched[skip : skip + limit]

    # ==================== Campaign Activity Persistence ====================

    async def save_campaign_activity_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        evt_copy = dict(event)
        if "timestamp" not in evt_copy:
            evt_copy["timestamp"] = datetime.now(timezone.utc).isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.campaign_activity_events.insert_one(dict(evt_copy))
                evt_copy.pop("_id", None)
                return evt_copy
            except Exception as e:
                logger.error(f"Error saving campaign activity event to MongoDB: {e}")
        evt_copy.pop("_id", None)
        self._memory_campaign_activities.append(evt_copy)
        return evt_copy

    async def list_campaign_activity_events(
        self,
        campaign_id: Optional[str] = None,
        event_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                query: Dict[str, Any] = {}
                if campaign_id:
                    query["campaign_id"] = campaign_id
                if event_type:
                    query["event_type"] = event_type
                cursor = self.db.campaign_activity_events.find(query).sort("timestamp", -1).skip(skip).limit(limit)
                items = await cursor.to_list(length=limit)
                for item in items:
                    item.pop("_id", None)
                return items
            except Exception as e:
                logger.error(f"Error listing campaign activity events from MongoDB: {e}")
        matched = [
            e for e in self._memory_campaign_activities
            if (not campaign_id or e.get("campaign_id") == campaign_id)
            and (not event_type or e.get("event_type") == event_type)
        ]
        matched.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        return matched[skip : skip + limit]

    async def count_campaign_activity_events(
        self,
        campaign_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> int:
        if self.is_connected and self.db is not None:
            try:
                query: Dict[str, Any] = {}
                if campaign_id:
                    query["campaign_id"] = campaign_id
                if event_type:
                    query["event_type"] = event_type
                return await self.db.campaign_activity_events.count_documents(query)
            except Exception as e:
                logger.error(f"Error counting campaign activity events in MongoDB: {e}")
        matched = [
            e for e in self._memory_campaign_activities
            if (not campaign_id or e.get("campaign_id") == campaign_id)
            and (not event_type or e.get("event_type") == event_type)
        ]
        return len(matched)

    # ==================== General Execution Tasks ====================

    async def save_task(self, task_data: Dict[str, Any]) -> str:
        """Persists or upserts an ExecutionTask."""
        task_id = task_data.get("task_id")
        data = dict(task_data)
        if self.is_connected and self.db is not None:
            try:
                await self.db.tasks.update_one(
                    {"task_id": task_id},
                    {"$set": data},
                    upsert=True
                )
                return task_id
            except Exception as e:
                logger.error(f"Error saving task '{task_id}' to MongoDB: {e}")
        self._memory_tasks[task_id] = data
        return task_id

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single ExecutionTask by task_id."""
        if self.is_connected and self.db is not None:
            try:
                task = await self.db.tasks.find_one({"task_id": task_id})
                if task:
                    task.pop("_id", None)
                    return task
            except Exception as e:
                logger.error(f"Error fetching task '{task_id}' from MongoDB: {e}")
        return self._memory_tasks.get(task_id)

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """Applies partial updates to an existing task."""
        if self.is_connected and self.db is not None:
            try:
                res = await self.db.tasks.update_one(
                    {"task_id": task_id},
                    {"$set": updates}
                )
                if res.matched_count > 0:
                    return True
            except Exception as e:
                logger.error(f"Error updating task '{task_id}' in MongoDB: {e}")
        if task_id in self._memory_tasks:
            self._memory_tasks[task_id].update(updates)
            return True
        return False

    async def list_tasks(
        self,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Lists execution tasks with optional filtering."""
        if self.is_connected and self.db is not None:
            try:
                query: Dict[str, Any] = {}
                if user_id:
                    query["user_id"] = user_id
                if status:
                    query["status"] = status
                cursor = self.db.tasks.find(query).sort("created_at", -1).skip(skip).limit(limit)
                tasks = await cursor.to_list(length=limit)
                for t in tasks:
                    t.pop("_id", None)
                return tasks
            except Exception as e:
                logger.error(f"Error listing tasks from MongoDB: {e}")
        matched = [
            t for t in self._memory_tasks.values()
            if (not user_id or t.get("user_id") == user_id)
            and (not status or t.get("status") == status)
        ]
        matched.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
        return matched[skip : skip + limit]

    async def save_task_job(self, job_data: Dict[str, Any]) -> str:
        """Persists or updates an individual action job."""
        job_id = job_data.get("job_id")
        data = dict(job_data)
        if self.is_connected and self.db is not None:
            try:
                await self.db.task_jobs.update_one(
                    {"job_id": job_id},
                    {"$set": data},
                    upsert=True
                )
                return job_id
            except Exception as e:
                logger.error(f"Error saving task job '{job_id}' to MongoDB: {e}")
        self._memory_task_jobs[job_id] = data
        return job_id

    async def get_task_jobs(self, task_id: str) -> List[Dict[str, Any]]:
        """Lists all action jobs belonging to a task."""
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.task_jobs.find({"task_id": task_id}).sort("created_at", 1)
                jobs = await cursor.to_list(length=500)
                for j in jobs:
                    j.pop("_id", None)
                return jobs
            except Exception as e:
                logger.error(f"Error fetching task jobs for '{task_id}' from MongoDB: {e}")
        matched = [j for j in self._memory_task_jobs.values() if j.get("task_id") == task_id]
        matched.sort(key=lambda x: str(x.get("created_at", "")))
        return matched

    async def save_task_event(self, event_data: Dict[str, Any]) -> str:
        """Appends a task lifecycle event."""
        event_id = event_data.get("event_id")
        data = dict(event_data)
        if self.is_connected and self.db is not None:
            try:
                await self.db.task_events.insert_one(data)
                return event_id
            except Exception as e:
                logger.error(f"Error saving task event to MongoDB: {e}")
        self._memory_task_events.append(data)
        return event_id

    async def get_task_events(self, task_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves timeline events for a task."""
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.task_events.find({"task_id": task_id}).sort("timestamp", 1).limit(limit)
                events = await cursor.to_list(length=limit)
                for e in events:
                    e.pop("_id", None)
                return events
            except Exception as e:
                logger.error(f"Error fetching events for task '{task_id}': {e}")
        matched = [e for e in self._memory_task_events if e.get("task_id") == task_id]
        matched.sort(key=lambda x: str(x.get("timestamp", "")))
        return matched[-limit:]

db_manager = MongoDBManager()
