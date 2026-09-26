from __future__ import annotations

from types import SimpleNamespace

from src.rag.qdrant_store import (
    BM25_MODEL,
    BM25_VECTOR_NAME,
    DENSE_VECTOR_NAME,
    QdrantStore,
    RRF_K,
)


class QdrantClientContractFake:
    def __init__(self):
        self.collections = {}
        self.payload_indexes = []
        self.points = []
        self.query = None
        self.deleted = None

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, *, collection_name, vectors_config, sparse_vectors_config):
        self.collections[collection_name] = SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(vectors=vectors_config, sparse_vectors=sparse_vectors_config)
            ),
            payload_schema={},
        )

    def create_payload_index(self, *, collection_name, field_name, field_schema, wait):
        del field_schema, wait
        self.payload_indexes.append((collection_name, field_name))
        self.collections[collection_name].payload_schema[field_name] = object()

    def get_collection(self, name):
        return self.collections[name]

    def upsert(self, *, collection_name, points, wait):
        assert wait is True
        assert collection_name in self.collections
        self.points.extend(points)

    def count(self, *, collection_name, count_filter, exact):
        assert collection_name in self.collections
        assert exact is True
        self.count_filter = count_filter
        return SimpleNamespace(count=len(self.points))

    def query_points(self, **kwargs):
        self.query = kwargs
        return SimpleNamespace(points=[SimpleNamespace(payload={"chunk_id": "c-1"})])

    def delete(self, **kwargs):
        self.deleted = kwargs

    def close(self):
        pass


def test_qdrant_named_vectors_filters_and_rrf_use_pinned_client_contract():
    client = QdrantClientContractFake()
    store = QdrantStore(client=client)
    store.upsert_chunks(
        collection="rag-collection",
        dimension=4,
        tenant_id="tenant-1",
        owner_id="owner-1",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        index_version_id="version-1",
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "page_start": 2,
                "page_end": 2,
                "section": "主要业务",
                "text": "中文文本检索样例",
            }
        ],
        vectors=[[0.1, 0.2, 0.3, 0.4]],
    )

    assert client.collections["rag-collection"].config.params.vectors[DENSE_VECTOR_NAME].size == 4
    point = client.points[0]
    assert set(point.vector) == {DENSE_VECTOR_NAME, BM25_VECTOR_NAME}
    assert point.vector[BM25_VECTOR_NAME].model == BM25_MODEL
    assert point.payload["index_version_id"] == "version-1"
    assert {field for _collection, field in client.payload_indexes} == {
        "tenant_id", "owner_id", "knowledge_base_id", "document_id", "index_version_id",
        "page_start", "page_end",
    }
    assert store.count_index_version("rag-collection", "version-1") == 1

    result = store.hybrid_search(
        collection="rag-collection",
        query="主要业务是什么？",
        query_vector=[0.4, 0.3, 0.2, 0.1],
        tenant_id="tenant-1",
        owner_id="owner-1",
        knowledge_base_ids=["kb-1"],
        index_version_ids=["version-1"],
        page_ranges=[(7, 7)],
        limit=5,
    )
    assert result[0].payload["chunk_id"] == "c-1"
    assert len(client.query["prefetch"]) == 2
    assert client.query["prefetch"][1].query.model == BM25_MODEL
    assert client.query["query"].rrf.k == RRF_K
    required = {item.key for item in client.query["query_filter"].must}
    assert required == {"tenant_id", "owner_id", "knowledge_base_id", "index_version_id"}
    page_filter = client.query["query_filter"].should[0]
    assert {condition.key for condition in page_filter.must} == {"page_start", "page_end"}
    assert page_filter.must[0].range.lte == 7
    assert page_filter.must[1].range.gte == 7

    store.delete_index_version("rag-collection", "version-1")
    assert client.deleted["points_selector"].filter.must[0].key == "index_version_id"
    store.close()
