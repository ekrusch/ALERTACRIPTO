from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClusterConfig:
    id: str
    name: str
    exchange: str
    symbols: list[str]
    rule: str
    timeframes: list[str]
    settings: dict[str, Any]


@dataclass(frozen=True)
class RadarConfig:
    exchanges: dict[str, dict[str, Any]]
    clusters: list[ClusterConfig]


def load_config(path: str | Path = "config/clusters.json") -> RadarConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    clusters = [
        ClusterConfig(
            id=item["id"],
            name=item["name"],
            exchange=item["exchange"],
            symbols=item["symbols"],
            rule=item["rule"],
            timeframes=item.get("timeframes", []),
            settings=item.get("settings", {}),
        )
        for item in raw["clusters"]
    ]
    return RadarConfig(exchanges=raw["exchanges"], clusters=clusters)
