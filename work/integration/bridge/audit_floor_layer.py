#!/usr/bin/env python3
"""Read-only comparison of an Odin binary XYZ PCD and a jie_3d_nav map package."""

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np


def read_xyz_pcd(path):
    with path.open("rb") as stream:
        header = {}
        for _ in range(32):
            line = stream.readline(4096).decode("ascii").strip()
            if not line:
                raise ValueError("Incomplete PCD header")
            if line.startswith("#"):
                continue
            key, *values = line.split()
            header[key] = values
            if key == "DATA":
                break
        expected = {
            "FIELDS": ["x", "y", "z"], "SIZE": ["4"] * 3, "TYPE": ["F"] * 3,
            "COUNT": ["1"] * 3, "DATA": ["binary"],
        }
        if any(header.get(key) != value for key, value in expected.items()):
            raise ValueError("Expected the Odin export format: binary XYZ float32 PCD")
        data = stream.read()
    points = np.frombuffer(data, dtype="<f4").reshape(-1, 3)
    if header.get("POINTS") != [str(len(points))] or not len(points):
        raise ValueError("PCD header point count does not match a nonempty payload")
    return points


class BinaryOcTree:
    """Look up leaf states without expanding pruned leaves into full-size grids."""

    def __init__(self, data):
        offset = 0

        def read_node(depth):
            nonlocal offset
            if offset + 2 > len(data) or depth >= 16:
                raise ValueError("Invalid binary OcTree payload")
            bits = int.from_bytes(data[offset:offset + 2], "little")
            offset += 2
            states = [(bits >> (2 * child)) & 3 for child in range(8)]
            return tuple(read_node(depth + 1) if state == 3 else state for state in states)

        self.root = read_node(0) if data else (0,) * 8
        if offset != len(data):
            raise ValueError("Trailing bytes in binary OcTree payload")

    def state(self, cell):
        x, y, z = (int(index) + 32768 for index in cell)
        if not all(0 <= index < 65536 for index in (x, y, z)):
            return "unknown"
        node = self.root
        for shift in range(15, -1, -1):
            child = ((x >> shift) & 1) + 2 * ((y >> shift) & 1) + 4 * ((z >> shift) & 1)
            node = node[child]
            if isinstance(node, int):
                return ("unknown", "free", "occupied")[node]
        raise ValueError("Invalid binary OcTree depth")


def grid_indices(points, resolution):
    return np.floor(points.astype(np.float64) / resolution).astype(np.int64)


def component_sizes(cells):
    remaining = set(cells)
    sizes = []
    while remaining:
        pending = [remaining.pop()]
        size = 0
        while pending:
            x, y = pending.pop()
            size += 1
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        sizes.append(size)
    return sorted(sizes, reverse=True)


def audit(pcd, package, center, radius, floor_z, tolerance):
    with np.load(package / "octomap_msg.npz") as message:
        resolution = float(message["resolution"].item())
        if (message["frame_id"].item() != "map" or message["octomap_id"].item() != "OcTree"
                or not message["binary"].item() or not np.isfinite(resolution) or resolution <= 0):
            raise ValueError("Expected a binary OcTree in frame map with positive resolution")
        tree = BinaryOcTree(message["data"].tobytes())
    with np.load(package / "layers.npz") as layers:
        layer = layers["traversable_points"]
        if layer.shape == (0,):
            # The upstream manager saves an empty marker as a 1-D NumPy array.
            layer = layer.reshape(0, 3)
        if (layers["traversable_frame_id"].item() != "map"
                or not np.allclose(layers["traversable_scale"], resolution)
                or layer.ndim != 2 or layer.shape[1] != 3 or not np.isfinite(layer).all()):
            raise ValueError("Traversable layer does not match the OctoMap grid and frame")
        traversable = set(map(tuple, grid_indices(layer, resolution)))

    points = read_xyz_pcd(pcd)
    finite = np.isfinite(points).all(axis=1)
    points_in_band = points[finite & (np.abs(points[:, 2] - floor_z) <= tolerance)]
    cells, point_counts = np.unique(
        grid_indices(points_in_band, resolution), axis=0, return_counts=True
    )
    # Select by voxel center so all three layers use the same XY neighborhood.
    xy_centers = (cells[:, :2] + 0.5) * resolution
    nearby = np.linalg.norm(xy_centers - center, axis=1) <= radius
    bands = []
    for z in np.unique(cells[nearby, 2]):
        mask = nearby & (cells[:, 2] == z)
        floor_states = {tuple(cell): tree.state(cell) for cell in cells[mask]}
        ground_states = Counter(floor_states.values())
        supported = [(x, y, z + 1) for (x, y, z), state in floor_states.items()
                     if state == "occupied"]
        above_states = Counter(tree.state(cell) for cell in supported)
        traversable_above = [(x, y) for x, y, z in supported if (x, y, z) in traversable]
        band = {
            "floor_cell_center_z_m": float((z + 0.5) * resolution),
            "directly_above_cell_center_z_m": float((z + 1.5) * resolution),
            "pcd_points": int(point_counts[mask].sum()),
            "pcd_cells": len(floor_states),
            "floor_cell_states": dict(ground_states),
            "above_occupied_floor_cell_states": dict(above_states),
            "traversable_above_occupied_floor": len(traversable_above),
            "traversable_4_neighbor_component_sizes": component_sizes(traversable_above),
        }
        if traversable_above:
            xy = (np.array(traversable_above) + 0.5) * resolution
            distances = np.linalg.norm(xy - center, axis=1)
            nearest = int(np.argmin(distances))
            band["nearest_traversable_xyz_m"] = [*xy[nearest], band["directly_above_cell_center_z_m"]]
            band["nearest_traversable_xy_distance_m"] = float(distances[nearest])
        bands.append(band)
    return {
        "pcd": str(pcd), "package": str(package), "frame": "map", "resolution_m": resolution,
        "center_xy_m": list(center), "radius_m": radius, "floor_z_m": floor_z,
        "floor_tolerance_m": tolerance, "floor_bands": bands,
        "scope": "Saved traversable markers with direct support from observed floor cells; "
                 "4-neighbor connectivity is a diagnostic and does not validate A* paths or clearance.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcd", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--center-xy", nargs=2, type=float, required=True, metavar=("X", "Y"))
    parser.add_argument("--radius", type=float, required=True)
    parser.add_argument("--floor-z", type=float, required=True)
    parser.add_argument("--floor-tolerance", type=float, required=True)
    args = parser.parse_args()
    if (not np.isfinite([*args.center_xy, args.radius, args.floor_z, args.floor_tolerance]).all()
            or min(args.radius, args.floor_tolerance) <= 0):
        parser.error("Require finite coordinates, positive radius and positive floor tolerance")
    try:
        report = audit(args.pcd, args.package, args.center_xy, args.radius,
                       args.floor_z, args.floor_tolerance)
    except (OSError, KeyError, ValueError) as error:
        parser.exit(1, f"Floor audit failed: {error}\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
