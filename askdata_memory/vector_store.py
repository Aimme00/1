from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Sequence

import numpy as np

from .objects import LongTermMemory, MemoryKind


class LongTermVectorIndex(Protocol):
    def upsert(self, memory: LongTermMemory) -> None: ...

    def search(
        self,
        *,
        user_id: str,
        query_embedding: np.ndarray,
        top_k: int,
        kinds: Optional[Sequence[MemoryKind]] = None,
    ) -> List[tuple[str, float]]: ...

    def delete(self, *, user_id: str, memory_id: str) -> None: ...


@dataclass
class MilvusLongTermMemoryConfig:
    uri: str | Path = "runtime_data/long_term_memory_vectors.db"
    token: str = ""
    collection_name: str = "askdata_long_term_memory"
    embedding_dimensions: int = 1024
    metric_type: str = "COSINE"


class MilvusLongTermVectorIndex:
    """Milvus/Milvus Lite 长期记忆向量索引，SQLite 继续作为原文与元信息源。"""

    def __init__(self, config: Optional[MilvusLongTermMemoryConfig] = None):
        self.config = config or MilvusLongTermMemoryConfig()
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise ImportError("使用 Milvus 长期记忆索引需要安装 pymilvus 和 milvus-lite") from exc
        uri = str(self.config.uri)
        if "://" not in uri:
            path = Path(uri)
            path.parent.mkdir(parents=True, exist_ok=True)
            uri = str(path)
        self.client = MilvusClient(uri=uri, token=self.config.token or None)
        self._prepare_collection()

    def _prepare_collection(self) -> None:
        from pymilvus import DataType, MilvusClient

        name = self.config.collection_name
        if self.client.has_collection(name):
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("user_id", DataType.VARCHAR, max_length=256)
        schema.add_field("kind", DataType.VARCHAR, max_length=32)
        schema.add_field("summary", DataType.VARCHAR, max_length=8192)
        schema.add_field(
            "embedding",
            DataType.FLOAT_VECTOR,
            dim=self.config.embedding_dimensions,
        )
        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type=self.config.metric_type,
        )
        self.client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )

    def upsert(self, memory: LongTermMemory) -> None:
        vector = np.asarray(memory.embedding, dtype=np.float32).reshape(-1)
        if vector.size != self.config.embedding_dimensions:
            raise ValueError(
                f"长期记忆向量维度应为 {self.config.embedding_dimensions}，实际为 {vector.size}"
            )
        self.client.upsert(
            collection_name=self.config.collection_name,
            data=[
                {
                    "id": memory.id,
                    "user_id": memory.user_id,
                    "kind": memory.kind.value,
                    "summary": memory.summary[:8192],
                    "embedding": vector.astype(float).tolist(),
                }
            ],
        )
        self.client.flush(collection_name=self.config.collection_name)

    def search(
        self,
        *,
        user_id: str,
        query_embedding: np.ndarray,
        top_k: int,
        kinds: Optional[Sequence[MemoryKind]] = None,
    ) -> List[tuple[str, float]]:
        filter_parts = [f"user_id == {json.dumps(user_id, ensure_ascii=False)}"]
        if kinds:
            kind_values = ", ".join(json.dumps(kind.value) for kind in kinds)
            filter_parts.append(f"kind in [{kind_values}]")
        results = self.client.search(
            collection_name=self.config.collection_name,
            data=[np.asarray(query_embedding, dtype=np.float32).astype(float).tolist()],
            anns_field="embedding",
            filter=" and ".join(filter_parts),
            limit=top_k,
            output_fields=["id"],
        )
        if not results:
            return []
        hits: List[tuple[str, float]] = []
        for hit in results[0]:
            memory_id = str(hit.get("id") or hit.get("entity", {}).get("id"))
            hits.append((memory_id, float(hit.get("distance", hit.get("score", 0.0)))))
        return hits

    def delete(self, *, user_id: str, memory_id: str) -> None:
        expression = (
            f"id == {json.dumps(memory_id)} and "
            f"user_id == {json.dumps(user_id, ensure_ascii=False)}"
        )
        self.client.delete(collection_name=self.config.collection_name, filter=expression)
