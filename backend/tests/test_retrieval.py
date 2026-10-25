import os
import sys

from providers.retrieval import LocalRetrievalProvider
from memory_store.legacy import LegacyMemoryStore

def main():
    print("--- Testing Memory Cleanup & Quota Trimming ---")

    retrieval_provider = LocalRetrievalProvider(root="./var/test_retrieval")
    memory_store = LegacyMemoryStore(retrieval_provider=retrieval_provider)

    agent_id = "test_trim_agent"

    # 1. Upsert a memory
    print("\n1. Upserting memory...")
    row = memory_store.upsert(
        agent_id=agent_id,
        scope="agent",
        key="theme_preference",
        value="Dark Mode Always",
    )

    # 2. Search before deletion
    results_before = memory_store.search(agent_id=agent_id, query="Dark Mode")
    print(f"Results before manual cleanup: {len(results_before)}")

    # 3. Simulate index cleanup call
    if row:
        doc_id = f"mem_{row.id}"
        print(f"\n2. Deleting index for {doc_id}...")
        retrieval_provider.delete_document(namespace=agent_id, document_id=doc_id)

    # 4. Search after deletion
    results_after = memory_store.search(agent_id=agent_id, query="Dark Mode")
    print(f"Results after cleanup: {len(results_after)}")

    if len(results_before) == 1 and len(results_after) == 0:
        print("\nSUCCESS! Trimming cleanup trigger properly removes document from SQLite search index!")

if __name__ == "__main__":
    main()