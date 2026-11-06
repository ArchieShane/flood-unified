#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Spatial routing for L2opt candidates: route pipes around buildings/sea using roads.

Replaces straight-line × 1.2 with GIS-aware paths. Plugs into l2opt_candidates.py.

Interface:
    router = SpatialRouter(gis_dir, cache_roads=True)
    result = router.route(x0, y0, x1, y1)
    # result['length'] replaces hypot(dx,dy) * ROUTE_K
    # result['feasible'] False → skip this candidate
"""
from __future__ import annotations
import geopandas as gpd
import networkx as nx
import numpy as np
from pathlib import Path
from shapely.geometry import Point, LineString
import math

class SpatialRouter:
    def __init__(self, gis_dir: Path, snap_tolerance_m=50, cache_roads=True):
        self.gis_dir = Path(gis_dir)
        self.snap_tol = snap_tolerance_m
        self.G = None
        self.buildings = None
        self.sea = None
        self._cache_roads = cache_roads

        if cache_roads:
            self._load_roads()
        self._load_buildings()
        self._load_sea()

    def _load_roads(self):
        f = self.gis_dir / "roads.gpkg"
        if not f.exists():
            self.G = nx.Graph()
            return
        roads = gpd.read_file(f)
        self.G = nx.Graph()
        for _, r in roads.iterrows():
            coords = list(r.geometry.coords)
            for i in range(len(coords) - 1):
                n1, n2 = coords[i], coords[i+1]
                L = math.hypot(n2[0]-n1[0], n2[1]-n1[1])
                self.G.add_edge(n1, n2, weight=L)

    def _load_buildings(self):
        f = self.gis_dir / "buildings.gpkg"
        self.buildings = gpd.read_file(f) if f.exists() else gpd.GeoDataFrame()

    def _load_sea(self):
        f = self.gis_dir / "sea.gpkg"
        if f.exists():
            self.sea = gpd.read_file(f, layer='surface')
        else:
            self.sea = gpd.GeoDataFrame()

    def _nearest_node(self, x, y):
        """Find nearest road node within snap_tol."""
        if self.G.number_of_nodes() == 0:
            return None
        nodes = np.array(list(self.G.nodes()))
        dist = np.hypot(nodes[:,0] - x, nodes[:,1] - y)
        i = dist.argmin()
        return tuple(nodes[i]) if dist[i] < self.snap_tol else None

    def _check_building_intersection(self, geom):
        """Check if pipe intersects buildings."""
        if len(self.buildings) == 0:
            return False
        return self.buildings.intersects(geom).any()

    def _check_sea_length(self, geom):
        """Measure subsea segment length."""
        if len(self.sea) == 0:
            return 0.0
        subsea = 0.0
        for _, s in self.sea.iterrows():
            inter = geom.intersection(s.geometry)
            if not inter.is_empty:
                if inter.geom_type == 'LineString':
                    subsea += inter.length
                elif inter.geom_type == 'MultiLineString':
                    subsea += sum(g.length for g in inter.geoms)
        return subsea

    def route(self, x0, y0, x1, y1, allow_subsea_m=500):
        """
        Route pipe from (x0,y0) to (x1,y1).

        Returns:
            {
                'length': float,       # routed length (m)
                'feasible': bool,      # False if crosses building or subsea > limit
                'reason': str or None, # why infeasible
                'path': LineString,    # geometry
                'method': str          # 'road' or 'direct'
            }
        """
        straight = LineString([(x0, y0), (x1, y1)])
        straight_len = straight.length

        # Try road routing if network available
        if self.G and self.G.number_of_nodes() > 0:
            n0 = self._nearest_node(x0, y0)
            n1 = self._nearest_node(x1, y1)

            if n0 and n1:
                try:
                    path_nodes = nx.astar_path(
                        self.G, n0, n1,
                        heuristic=lambda a, b: math.hypot(b[0]-a[0], b[1]-a[1]),
                        weight='weight'
                    )
                    full_path = [(x0, y0)] + path_nodes + [(x1, y1)]
                    routed_geom = LineString(full_path)
                    routed_len = routed_geom.length

                    # Check constraints on routed path
                    if self._check_building_intersection(routed_geom):
                        # Road path crosses building → fallback to straight and check
                        pass
                    else:
                        subsea = self._check_sea_length(routed_geom)
                        if subsea > allow_subsea_m:
                            return {
                                'length': routed_len,
                                'feasible': False,
                                'reason': f'subsea {subsea:.0f}m > {allow_subsea_m}m',
                                'path': routed_geom,
                                'method': 'road'
                            }
                        return {
                            'length': routed_len,
                            'feasible': True,
                            'reason': None,
                            'path': routed_geom,
                            'method': 'road'
                        }
                except nx.NetworkXNoPath:
                    pass

        # Fallback: check straight line
        if self._check_building_intersection(straight):
            return {
                'length': straight_len,
                'feasible': False,
                'reason': 'crosses building',
                'path': straight,
                'method': 'direct'
            }

        subsea = self._check_sea_length(straight)
        if subsea > allow_subsea_m:
            return {
                'length': straight_len,
                'feasible': False,
                'reason': f'subsea {subsea:.0f}m > {allow_subsea_m}m',
                'path': straight,
                'method': 'direct'
            }

        return {
            'length': straight_len,
            'feasible': True,
            'reason': None,
            'path': straight,
            'method': 'direct'
        }
