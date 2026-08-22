"""KuaiSearch 商品数据的流式筛选与 Seekora 目录格式转换。"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4


DEFAULT_ELECTRONICS_CATEGORY_ID = 30
DEFAULT_SNAPSHOT_AT = "2026-02-12T00:00:00Z"
DEFAULT_EXCLUDED_PRODUCT_TYPES = ("phone_case",)


@dataclass(frozen=True)
class KuaiSearchConversionReport:
    """记录一次转换的输入规模、筛选结果和输出校验信息。"""

    source_rows: int
    matched_rows: int
    selected_rows: int
    invalid_rows: int
    excluded_rows: int
    excluded_product_type_counts: dict[str, int]
    product_type_counts: dict[str, int]
    category_level1_id: int
    category_level1_names: tuple[str, ...]
    category_level2_counts: dict[str, int]
    output_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_rows": self.source_rows,
            "matched_rows": self.matched_rows,
            "selected_rows": self.selected_rows,
            # 未进入输出快照的过滤、超限和无效记录统一计为跳过；invalid_rows
            # 另列为失败数，便于导入命令直接输出“数据/跳过/失败”三项。
            "skipped_rows": self.source_rows - self.selected_rows - self.invalid_rows,
            "failed_rows": self.invalid_rows,
            "invalid_rows": self.invalid_rows,
            "excluded_rows": self.excluded_rows,
            "excluded_product_type_counts": self.excluded_product_type_counts,
            "product_type_counts": self.product_type_counts,
            "category_level1_id": self.category_level1_id,
            "category_level1_names": list(self.category_level1_names),
            "category_level2_counts": self.category_level2_counts,
            "output_sha256": self.output_sha256,
        }


def _sample_key(item_id: str, seed: int) -> int:
    """生成跨进程稳定的采样键，避免依赖 Python 的随机哈希。"""
    payload = f"{seed}:{item_id}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def _stable_number(item_id: str, namespace: str) -> int:
    payload = f"{namespace}:{item_id}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def _pick(item_id: str, namespace: str, values: tuple[Any, ...]) -> Any:
    return values[_stable_number(item_id, namespace) % len(values)]


def _classify_product_type(
    raw: dict[str, Any],
) -> tuple[str, tuple[int, int], tuple[str, ...]]:
    """根据原始标题和类目确定商品类型，供排除规则与合成规则共同使用。"""
    title = str(raw.get("item_title") or "")
    level2 = str(raw.get("category_level2_name") or "")
    level3 = str(raw.get("category_level3_name") or "")
    text = f"{level2} {level3} {title}".lower()

    if any(token in text for token in ("手机壳", "保护壳")):
        return "phone_case", (9, 199), ("mobile",)
    elif any(token in text for token in ("手机膜", "保护膜", "钢化膜")):
        return "screen_protector", (5, 99), ("mobile",)
    elif any(token in text for token in ("笔记本整机", "笔记本电脑", "轻薄本", "游戏本", "laptop")):
        return "laptop", (2499, 15999), ("office", "programming")
    elif any(token in text for token in ("台式机", "一体机", "主机整机")):
        return "desktop", (1999, 13999), ("office", "creation")
    elif "平板" in text:
        return "tablet", (699, 6999), ("mobile", "study")
    elif any(token in text for token in ("手机设备", "智能手机", "手机整机")):
        return "phone", (599, 9999), ("mobile", "photography")
    elif any(token in text for token in ("显示器", "显示屏")):
        return "display", (399, 5999), ("office", "gaming")
    elif any(token in text for token in ("键盘", "鼠标")):
        return "keyboard_mouse", (29, 1299), ("office", "gaming")
    elif any(token in text for token in ("硬盘", "u盘", "存储")):
        return "storage", (29, 2999), ("storage", "office")
    elif any(token in text for token in ("路由", "交换机", "网络设备")):
        return "network", (59, 2999), ("network", "office")
    elif any(token in text for token in ("耳机", "音箱", "麦克风", "影音")):
        return "audio", (29, 3999), ("music", "gaming")
    elif any(token in text for token in ("相机", "摄影", "摄像")):
        return "camera", (399, 19999), ("photography", "creation")
    elif level2 == "智能设备":
        return "smart_device", (59, 4999), ("smart_home",)
    return "digital_accessory", (9, 1999), ("digital",)


def _add_synthetic(
    attributes: dict[str, Any], fields: dict[str, Any]
) -> None:
    """集中登记合成值，确保新增字段不会遗漏数据来源标记。"""
    attributes.update(fields)
    attributes["synthetic_fields"].extend(fields)


def _synthetic_test_attributes(raw: dict[str, Any], item_id: str) -> dict[str, Any]:
    """按商品类型生成可复现测试值；这些值不代表 KuaiSearch 的真实事实。"""
    product_type, price_range, use_cases = _classify_product_type(raw)

    low, high = price_range
    price = low + _stable_number(item_id, "price") % (high - low + 1)
    attributes: dict[str, Any] = {
        "product_type": product_type,
        "price": float(price),
        "currency": "CNY",
        "inventory_count": 1 + _stable_number(item_id, "inventory") % 200,
        "sales_30d": _stable_number(item_id, "sales") % 5000,
        "rating": round(3.5 + (_stable_number(item_id, "rating") % 16) / 10, 1),
        "review_count": _stable_number(item_id, "reviews") % 20_000,
        "use_cases": list(use_cases),
        "synthetic_test_data": True,
        "synthetic_fields": [
            "price", "inventory_count", "sales_30d", "rating",
            "review_count", "use_cases", "warranty_months",
            "shipping_days", "return_window_days",
        ],
        "warranty_months": _pick(item_id, "warranty", (3, 6, 12, 24)),
        "shipping_days": _pick(item_id, "shipping", (1, 2, 3, 5, 7)),
        "return_window_days": _pick(item_id, "return", (7, 15, 30)),
    }
    if product_type in {"laptop", "desktop", "tablet", "phone"}:
        _add_synthetic(attributes, {
            "memory_gb": _pick(item_id, "memory", (4, 8, 16, 32, 64)),
            "storage_gb": _pick(item_id, "storage", (128, 256, 512, 1024, 2048)),
        })
    if product_type == "laptop":
        _add_synthetic(attributes, {
            "screen_size_inch": _pick(item_id, "screen", (13.3, 14.0, 15.6, 16.0)),
            "weight_kg": _pick(item_id, "weight", (1.15, 1.3, 1.5, 1.8, 2.2, 2.5)),
            "battery_hours": _pick(item_id, "battery", (6, 8, 10, 12, 15)),
            "cpu_tier": _pick(item_id, "cpu", ("entry", "mainstream", "performance")),
            "gpu_tier": _pick(item_id, "gpu", ("integrated", "entry", "performance")),
        })
    elif product_type == "desktop":
        _add_synthetic(attributes, {
            "cpu_tier": _pick(item_id, "cpu", ("entry", "mainstream", "performance")),
            "gpu_tier": _pick(item_id, "gpu", ("integrated", "entry", "performance")),
            "power_w": _pick(item_id, "power", (300, 450, 550, 650, 750)),
        })
    elif product_type in {"phone", "tablet"}:
        _add_synthetic(attributes, {
            "battery_mah": _pick(item_id, "battery", (4000, 4500, 5000, 6000, 8000)),
            "screen_size_inch": _pick(item_id, "screen", (6.1, 6.5, 6.7, 8.3, 10.9, 12.9)),
            "refresh_rate_hz": _pick(item_id, "refresh", (60, 90, 120, 144)),
            "cellular": _pick(item_id, "cellular", ("wifi", "4g", "5g")),
        })
    elif product_type == "display":
        _add_synthetic(attributes, {
            "screen_size_inch": _pick(item_id, "screen", (21.5, 24.0, 27.0, 32.0)),
            "refresh_rate_hz": _pick(item_id, "refresh", (60, 75, 120, 144, 165)),
            "resolution": _pick(item_id, "resolution", ("1920x1080", "2560x1440", "3840x2160")),
            "panel_type": _pick(item_id, "panel", ("IPS", "VA", "OLED")),
        })
    elif product_type == "screen_protector":
        _add_synthetic(attributes, {
            "material": _pick(item_id, "material", ("tempered_glass", "PET", "TPU")),
            "hardness_h": _pick(item_id, "hardness", (6, 8, 9)),
            "finish": _pick(item_id, "finish", ("clear", "matte", "privacy")),
            "pack_count": _pick(item_id, "pack", (1, 2, 3, 5)),
        })
    elif product_type == "audio":
        _add_synthetic(attributes, {
            "connectivity": _pick(item_id, "connectivity", ("wired", "bluetooth", "2.4g")),
            "bluetooth_version": _pick(item_id, "bluetooth", ("5.0", "5.2", "5.3", "5.4")),
            "battery_hours": _pick(item_id, "audio_battery", (8, 16, 24, 36, 48)),
            "noise_cancellation": _pick(item_id, "anc", ("none", "passive", "active")),
        })
    elif product_type == "camera":
        _add_synthetic(attributes, {
            "camera_type": _pick(item_id, "camera_type", ("compact", "mirrorless", "action", "security")),
            "sensor_megapixels": _pick(item_id, "sensor", (12, 20, 24, 33, 48)),
            "video_resolution": _pick(item_id, "video", ("1080p", "4k", "8k")),
            "stabilization": _pick(item_id, "stabilization", ("none", "electronic", "optical")),
        })
    elif product_type == "storage":
        _add_synthetic(attributes, {
            "storage_gb": _pick(item_id, "capacity", (64, 128, 256, 512, 1024, 2048, 4096)),
            "storage_type": _pick(item_id, "storage_type", ("usb_flash", "ssd", "hdd", "memory_card")),
            "interface": _pick(item_id, "interface", ("USB-A", "USB-C", "SATA", "NVMe")),
            "read_speed_mbps": _pick(item_id, "read_speed", (100, 500, 1000, 3500, 7000)),
        })
    elif product_type == "network":
        _add_synthetic(attributes, {
            "wifi_standard": _pick(item_id, "wifi", ("wifi5", "wifi6", "wifi6e", "wifi7")),
            "bands": _pick(item_id, "bands", ("2.4g", "dual_band", "tri_band")),
            "ethernet_ports": _pick(item_id, "ports", (1, 2, 4, 8, 16)),
            "coverage_sqm": _pick(item_id, "coverage", (50, 90, 120, 180, 250)),
        })
    elif product_type == "keyboard_mouse":
        _add_synthetic(attributes, {
            "connectivity": _pick(item_id, "connectivity", ("wired", "bluetooth", "2.4g")),
            "switch_type": _pick(item_id, "switch", ("membrane", "red", "brown", "blue")),
            "dpi": _pick(item_id, "dpi", (800, 1600, 3200, 6400, 12000)),
        })
    elif product_type == "smart_device":
        _add_synthetic(attributes, {
            "connectivity": _pick(item_id, "connectivity", ("wifi", "bluetooth", "zigbee", "matter")),
            "power_type": _pick(item_id, "power_type", ("battery", "usb", "mains")),
            "ecosystem": _pick(item_id, "ecosystem", ("generic", "homekit", "xiaomi", "huawei")),
        })
    else:
        _add_synthetic(attributes, {
            "compatible_device_type": _pick(item_id, "compatible", ("phone", "tablet", "computer", "universal")),
            "material": _pick(item_id, "material", ("plastic", "metal", "silicone", "fabric")),
        })
    return attributes


def _convert_item(
    raw: dict[str, Any], snapshot_at: str, tenant_id: str
) -> dict[str, Any]:
    item_id = str(raw["item_id"])
    title = str(raw["item_title"]).strip()
    if not item_id or not title:
        raise ValueError("item_id and item_title are required")

    category_names = [
        str(raw.get("category_level1_name") or "").strip(),
        str(raw.get("category_level2_name") or "").strip(),
        str(raw.get("category_level3_name") or "").strip(),
    ]
    usable_categories = [
        name for name in category_names if name and name.upper() != "UNKNOWN"
    ]
    category = usable_categories[-1] if usable_categories else "unknown"
    brand = str(raw.get("brand_name") or "UNKNOWN").strip()
    seller = str(raw.get("seller_name") or "UNKNOWN").strip()
    description = "；".join(
        part for part in (brand, seller, " / ".join(usable_categories)) if part
    )
    attributes = {
        "source": "KuaiSearch-Lite",
        "source_item_id": item_id,
        "brand_id": raw.get("brand_id"),
        "brand": brand,
        "seller_id": raw.get("seller_id"),
        "seller": seller,
        "category_level1_id": raw.get("category_level1_id"),
        "category_level1_name": category_names[0],
        "category_level2_id": raw.get("category_level2_id"),
        "category_level2_name": category_names[1],
        "category_level3_id": raw.get("category_level3_id"),
        "category_level3_name": category_names[2],
    }
    attributes.update(_synthetic_test_attributes(raw, item_id))
    return {
        "item_id": f"kuaisearch-{item_id}",
        "tenant_id": tenant_id,
        "title": title,
        "description": description,
        "category": category,
        "attributes": attributes,
        "status": "active",
        "permission_tags": ["public"],
        "updated_at": snapshot_at,
        "quality_score": 0.0,
    }


def _write_jsonl(stream: TextIO, items: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in items:
        encoded = (
            json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        stream.write(encoded.decode("utf-8"))
        digest.update(encoded)
    return digest.hexdigest()


def convert_kuaisearch_items(
    source: str | Path,
    output: str | Path,
    *,
    category_level1_id: int = DEFAULT_ELECTRONICS_CATEGORY_ID,
    limit: int = 50_000,
    seed: int = 20260819,
    snapshot_at: str = DEFAULT_SNAPSHOT_AT,
    tenant_id: str = "demo",
    excluded_product_types: tuple[str, ...] = DEFAULT_EXCLUDED_PRODUCT_TYPES,
) -> KuaiSearchConversionReport:
    """筛选指定一级类目，并确定性采样为 Seekora JSONL 目录。"""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not tenant_id.strip():
        raise ValueError("tenant_id must not be empty")
    source_path = Path(source)
    output_path = Path(output)
    if source_path.resolve() == output_path.resolve():
        raise ValueError("source and output paths must be different")

    # 最大堆只保留采样键最小的 N 条，内存占用由 limit 控制。
    selected: list[tuple[int, int, dict[str, Any]]] = []
    source_rows = 0
    matched_rows = 0
    invalid_rows = 0
    excluded_counts: Counter[str] = Counter()
    category_names: set[str] = set()
    secondary_counts: Counter[str] = Counter()

    with source_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            source_rows += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON at {source_path}:{line_number}: {exc}"
                ) from exc
            if raw.get("category_level1_id") != category_level1_id:
                continue
            matched_rows += 1
            category_names.add(str(raw.get("category_level1_name") or "UNKNOWN"))
            secondary_counts[str(raw.get("category_level2_name") or "UNKNOWN")] += 1
            product_type, _, _ = _classify_product_type(raw)
            if product_type in excluded_product_types:
                excluded_counts[product_type] += 1
                continue
            try:
                item = _convert_item(raw, snapshot_at, tenant_id)
                numeric_id = int(raw["item_id"])
            except (KeyError, TypeError, ValueError):
                invalid_rows += 1
                continue
            key = _sample_key(str(raw["item_id"]), seed)
            entry = (-key, -numeric_id, item)
            if len(selected) < limit:
                heapq.heappush(selected, entry)
            elif entry > selected[0]:
                heapq.heapreplace(selected, entry)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 唯一临时文件避免并发转换或上次异常遗留文件互相阻塞。
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid4().hex}.tmp"
    )
    ordered_items = sorted((entry[2] for entry in selected), key=lambda item: item["item_id"])
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
            output_sha256 = _write_jsonl(stream, ordered_items)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return KuaiSearchConversionReport(
        source_rows=source_rows,
        matched_rows=matched_rows,
        selected_rows=len(ordered_items),
        invalid_rows=invalid_rows,
        excluded_rows=sum(excluded_counts.values()),
        excluded_product_type_counts=dict(excluded_counts),
        product_type_counts=dict(Counter(
            str(item["attributes"]["product_type"]) for item in ordered_items
        ).most_common()),
        category_level1_id=category_level1_id,
        category_level1_names=tuple(sorted(category_names)),
        category_level2_counts=dict(secondary_counts.most_common()),
        output_sha256=output_sha256,
    )
