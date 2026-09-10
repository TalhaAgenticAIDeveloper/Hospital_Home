"""
Standalone script to clean up orphan files in uploads/patient_documents.
Keeps all files currently registered in PostgreSQL patient_documents table,
and safely deletes test artifacts or unlinked files.
"""

import asyncio
import os
from pathlib import Path
from sqlalchemy import select
from app.core.database import async_session_maker
from app.models.patient_document import PatientDocument
from app.services.patient_document_service import PatientDocumentService, PATIENT_DOCUMENTS_DIR


async def run_cleanup():
    print("=" * 60)
    print("PATIENT DOCUMENTS STORAGE CLEANUP")
    print("=" * 60)

    upload_dir = Path(PATIENT_DOCUMENTS_DIR)
    if not upload_dir.exists():
        print(f"Directory '{upload_dir}' does not exist. Nothing to clean.")
        return

    files_on_disk = [f for f in upload_dir.iterdir() if f.is_file()]
    print(f"Files currently on disk in '{upload_dir}': {len(files_on_disk)}")

    async with async_session_maker() as session:
        # Fetch all registered documents
        result = await session.execute(select(PatientDocument))
        docs = result.scalars().all()
        print(f"\nActive Documents in PostgreSQL database: {len(docs)}")
        for doc in docs:
            print(f"  - ID: {doc.id} | File: {doc.original_filename} ({doc.file_size} bytes) -> Stored: {doc.stored_filename}")

        # Run service cleanup
        stats = await PatientDocumentService.cleanup_orphan_files(session)

    print("\nCleanup Summary:")
    print(f"  - Total files scanned:   {stats['scanned_files']}")
    print(f"  - Database valid files:  {stats['db_registered']}")
    print(f"  - Orphan files deleted:  {stats['deleted_orphans']}")
    reclaimed_mb = stats['reclaimed_bytes'] / (1024 * 1024)
    print(f"  - Space reclaimed:       {stats['reclaimed_bytes']} bytes ({reclaimed_mb:.3f} MB)")

    # Verify remaining files on disk
    remaining_files = [f for f in upload_dir.iterdir() if f.is_file()]
    print(f"\nRemaining files on disk now: {len(remaining_files)}")
    for f in remaining_files:
        print(f"  * {f.name} ({f.stat().st_size} bytes)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_cleanup())
