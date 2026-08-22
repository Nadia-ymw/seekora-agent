"""SQLite + sqlite-vec 实现的单文件本地向量索引。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Sequence

from ...application.semantic import VectorHit, VectorIndexMismatch


SQLITE_VECTOR_SCHEMA_VERSION = 1
SQLITE_VECTOR_INDEX_VERSION = "sqlite-vec-cosine-v1"


def _load_sqlite_vec(connection: sqlite3.Connection):
    """按需加载可选扩展，普通 BM25/TF-IDF 启动不依赖它。"""
    try:
        import sqlite_vec
    except ImportError as exc:  # pragma: no cover - 取决于本机可选依赖
        raise OSError(
            "sqlite-vec is required for the SQLite vector index; "
            "install the semantic extra with: pip install -e '.[semantic]'"
        ) from exc

    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)
    return sqlite_vec


class SQLiteVectorIndex:
    """用普通 SQLite 表管理版本，用 vec0 虚拟表执行余弦 KNN。"""

    def __init__(self, path: str | Path, connection: sqlite3.Connection) -> None:
        self.path = Path(path)
        self._connection = connection
        self._sqlite_vec = _load_sqlite_vec(connection)
        self._lock = threading.RLock()
        self._closed = False
        self._reload_metadata()

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        embedding_version: str,
        dimension: int,
        embedding_model_id: str | None = None,
        embedding_revision: str | None = None,
        query_instruction: str | None = None,
        overwrite: bool = False,
    ) -> "SQLiteVectorIndex":
        if dimension <= 0:
            raise ValueError("vector dimension must be positive")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(target)
        if overwrite:
            target.unlink(missing_ok=True)
            Path(f"{target}-wal").unlink(missing_ok=True)
            Path(f"{target}-shm").unlink(missing_ok=True)

        connection = cls._connect(target)
        _load_sqlite_vec(connection)
        try:
            connection.executescript(
                """
                CREATE TABLE seekora_vector_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL,
                    index_version TEXT NOT NULL,
                    embedding_version TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    embedding_model_id TEXT NOT NULL,
                    embedding_revision TEXT NOT NULL,
                    query_instruction TEXT,
                    catalog_snapshot_sha256 TEXT,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    built_at TEXT,
                    last_synchronized_at TEXT
                );
                CREATE TABLE seekora_vector_items (
                    vector_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL
                );
                """
            )
            # dimension 经过正整数校验；SQLite 参数不能用于虚拟表类型声明。
            connection.execute(
                "CREATE VIRTUAL TABLE seekora_vectors USING vec0("
                "vector_rowid INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{int(dimension)}] distance_metric=cosine)"
            )
            connection.execute(
                """
                INSERT INTO seekora_vector_metadata (
                    singleton, schema_version, index_version, embedding_version,
                    dimension, embedding_model_id, embedding_revision,
                    query_instruction, item_count
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    SQLITE_VECTOR_SCHEMA_VERSION,
                    SQLITE_VECTOR_INDEX_VERSION,
                    embedding_version,
                    dimension,
                    embedding_model_id or embedding_version,
                    embedding_revision or "unknown",
                    query_instruction,
                ),
            )
            connection.commit()
        except BaseException:
            connection.close()
            target.unlink(missing_ok=True)
            raise
        return cls(target, connection)

    @classmethod
    def load(
        cls,
        path: str | Path,
        expected_embedding_version: str | None = None,
        expected_dimension: int | None = None,
        expected_catalog_snapshot_sha256: str | None = None,
        expected_item_count: int | None = None,
    ) -> "SQLiteVectorIndex":
        target = Path(path)
        if not target.is_file():
            raise FileNotFoundError(target)
        try:
            index = cls(target, cls._connect(target))
        except sqlite3.DatabaseError as exc:
            raise VectorIndexMismatch("invalid SQLite vector index") from exc
        try:
            if index.schema_version != SQLITE_VECTOR_SCHEMA_VERSION:
                raise VectorIndexMismatch("unsupported SQLite vector index schema version")
            if (
                expected_embedding_version
                and index.embedding_version != expected_embedding_version
            ):
                raise VectorIndexMismatch(
                    "embedding model version does not match persisted vector index"
                )
            if expected_dimension and index.dimension != expected_dimension:
                raise VectorIndexMismatch(
                    "embedding dimension does not match persisted vector index"
                )
            if (
                expected_catalog_snapshot_sha256
                and index.catalog_snapshot_sha256
                != expected_catalog_snapshot_sha256
            ):
                raise VectorIndexMismatch(
                    "catalog snapshot does not match persisted vector index"
                )
            actual_count = index.entry_count()
            if expected_item_count is not None:
                if index.item_count != expected_item_count or actual_count != expected_item_count:
                    raise VectorIndexMismatch(
                        "catalog item count does not match SQLite vector index"
                    )
            vector_count = index._connection.execute(
                "SELECT count(*) FROM seekora_vectors"
            ).fetchone()[0]
            if actual_count != int(vector_count):
                raise VectorIndexMismatch(
                    "vector row count does not match SQLite item manifest"
                )
            return index
        except BaseException:
            index.close()
            raise

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            path,
            timeout=30.0,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=30000")
            return connection
        except BaseException:
            connection.close()
            raise

    def _reload_metadata(self) -> None:
        try:
            row = self._connection.execute(
                "SELECT * FROM seekora_vector_metadata WHERE singleton = 1"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise VectorIndexMismatch("not a Seekora SQLite vector index") from exc
        if row is None:
            raise VectorIndexMismatch("SQLite vector index metadata is missing")
        self.schema_version = int(row["schema_version"])
        self._index_version = str(row["index_version"])
        self._embedding_version = str(row["embedding_version"])
        self._dimension = int(row["dimension"])
        self.embedding_model_id = str(row["embedding_model_id"])
        self.embedding_revision = str(row["embedding_revision"])
        self.query_instruction = row["query_instruction"]
        self.catalog_snapshot_sha256 = row["catalog_snapshot_sha256"]
        self.item_count = int(row["item_count"])
        self.built_at = row["built_at"]
        self.last_synchronized_at = row["last_synchronized_at"]

    @property
    def index_version(self) -> str:
        return self._index_version

    @property
    def embedding_version(self) -> str:
        return self._embedding_version

    @property
    def dimension(self) -> int:
        return self._dimension

    def search(self, vector: Sequence[float], top_k: int) -> tuple[VectorHit, ...]:
        if len(vector) != self.dimension:
            raise VectorIndexMismatch(
                f"query dimension {len(vector)} does not match index dimension {self.dimension}"
            )
        if top_k <= 0:
            return ()
        query = self._sqlite_vec.serialize_float32([float(value) for value in vector])
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT items.item_id, neighbors.distance
                FROM (
                    SELECT vector_rowid, distance
                    FROM seekora_vectors
                    WHERE embedding MATCH ? AND k = ?
                ) AS neighbors
                JOIN seekora_vector_items AS items
                  ON items.vector_rowid = neighbors.vector_rowid
                ORDER BY neighbors.distance, items.item_id
                """,
                (query, top_k),
            ).fetchall()
        # sqlite-vec 返回 cosine distance；端口统一暴露 cosine similarity。
        return tuple(
            VectorHit(str(row["item_id"]), 1.0 - float(row["distance"]))
            for row in rows
        )

    def upsert(self, item_id: str, vector: Sequence[float], content_hash: str) -> None:
        self.upsert_many(((item_id, vector, content_hash),))

    def upsert_many(
        self, entries: Iterable[tuple[str, Sequence[float], str]]
    ) -> None:
        prepared = list(entries)
        for _, vector, _ in prepared:
            if len(vector) != self.dimension:
                raise VectorIndexMismatch(
                    f"document dimension {len(vector)} does not match index dimension "
                    f"{self.dimension}"
                )
        with self._lock, self._connection:
            for item_id, vector, content_hash in prepared:
                existing = self._connection.execute(
                    "SELECT vector_rowid FROM seekora_vector_items WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                if existing is None:
                    cursor = self._connection.execute(
                        "INSERT INTO seekora_vector_items (item_id, content_hash) VALUES (?, ?)",
                        (item_id, content_hash),
                    )
                    vector_rowid = int(cursor.lastrowid)
                else:
                    vector_rowid = int(existing["vector_rowid"])
                    self._connection.execute(
                        "DELETE FROM seekora_vectors WHERE vector_rowid = ?",
                        (vector_rowid,),
                    )
                    self._connection.execute(
                        "UPDATE seekora_vector_items SET content_hash = ? WHERE vector_rowid = ?",
                        (content_hash, vector_rowid),
                    )
                self._connection.execute(
                    "INSERT INTO seekora_vectors (vector_rowid, embedding) VALUES (?, ?)",
                    (
                        vector_rowid,
                        self._sqlite_vec.serialize_float32(
                            [float(value) for value in vector]
                        ),
                    ),
                )

    def delete(self, item_id: str) -> bool:
        return bool(self.delete_many((item_id,)))

    def delete_many(self, item_ids: Iterable[str]) -> int:
        deleted = 0
        with self._lock, self._connection:
            for item_id in item_ids:
                row = self._connection.execute(
                    "SELECT vector_rowid FROM seekora_vector_items WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    continue
                vector_rowid = int(row["vector_rowid"])
                self._connection.execute(
                    "DELETE FROM seekora_vectors WHERE vector_rowid = ?",
                    (vector_rowid,),
                )
                self._connection.execute(
                    "DELETE FROM seekora_vector_items WHERE vector_rowid = ?",
                    (vector_rowid,),
                )
                deleted += 1
        return deleted

    def content_hash(self, item_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT content_hash FROM seekora_vector_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        return str(row["content_hash"]) if row else None

    def item_ids(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT item_id FROM seekora_vector_items ORDER BY item_id"
            ).fetchall()
        return tuple(str(row["item_id"]) for row in rows)

    def entry_count(self) -> int:
        with self._lock:
            return int(
                self._connection.execute(
                    "SELECT count(*) FROM seekora_vector_items"
                ).fetchone()[0]
            )

    def mark_synchronized(
        self, catalog_snapshot_sha256: str, item_count: int, synchronized_at: str
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE seekora_vector_metadata
                SET embedding_model_id = ?, embedding_revision = ?,
                    query_instruction = ?, catalog_snapshot_sha256 = ?,
                    item_count = ?, built_at = COALESCE(built_at, ?),
                    last_synchronized_at = ?
                WHERE singleton = 1
                """,
                (
                    self.embedding_model_id,
                    self.embedding_revision,
                    self.query_instruction,
                    catalog_snapshot_sha256,
                    item_count,
                    synchronized_at,
                    synchronized_at,
                ),
            )
        self._reload_metadata()

    def checkpoint(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self.checkpoint()
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SQLiteVectorIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
