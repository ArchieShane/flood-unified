#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""End-to-end test: validate existing candidates with spatial constraints"""

import sys
import json
from pathlib import Path
from shapely.geometry import LineString
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parent))
from simple_constraint_checker import SimpleConstraintChecker

ROOT = Path(__file__).resolve().parent.parent

def load_existing_candidates(run="RDA_96B"):
    """Load existing candidate library"""
    file = ROOT / "out" / "l2opt" / run / "candidates.json"
    print(f"Loading candidates: {file}")
    
    if not file.exists():
        print(f"  File not found")
        return []
    
    with open(file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    candidates = data.get('candidates', [])
    print(f"  Loaded: {len(candidates)} candidates")
    
    return candidates

def add_simple_geometry(candidates):
    """Add simple pipe geometry (straight lines)"""
    print(f"\nAdding geometry...")
    
    # WGS84 -> UTM 32640
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    
    count_with_pipe = 0
    
    for cand in candidates:
        kind = cand.get('kind')
        
        # Only pipe-related types
        if kind == 'pump_outfall':
            # Start: well (from lon/lat)
            if 'lon' in cand and 'lat' in cand:
                x0, y0 = transformer.transform(cand['lon'], cand['lat'])
            else:
                continue
            
            # End: outfall
            if 'of_xy' in cand:
                x1, y1 = cand['of_xy']
            else:
                continue
            
            # Create straight pipe
            pipe_geom = LineString([(x0, y0), (x1, y1)])
            pipe_length = pipe_geom.length
            
            cand['pipe_geom'] = pipe_geom
            cand['pipe_length'] = pipe_length
            
            count_with_pipe += 1
    
    print(f"  Added geometry for {count_with_pipe} candidates")
    
    return candidates

def test_existing_candidates(run="RDA_96B"):
    """Test existing candidate library"""
    print("=" * 80)
    print(f"E2E Test: Validate Candidates ({run})")
    print("=" * 80)
    
    # 1. Load candidates
    candidates = load_existing_candidates(run)
    
    if len(candidates) == 0:
        print("\nNo candidates, test aborted")
        return
    
    # 2. Add geometry
    candidates = add_simple_geometry(candidates)
    
    # 3. Initialize checker
    print("\nInitializing constraint checker...")
    gis_dir = ROOT / "data" / "gis"
    checker = SimpleConstraintChecker(gis_dir)
    
    # 4. Batch check
    print("\nStarting batch check...")
    results = checker.batch_check(candidates)
    
    # 5. Detailed stats
    print("\n" + "=" * 80)
    print("Detailed Statistics")
    print("=" * 80)
    
    # By constraint type
    violation_stats = {}
    
    for result in results['results']:
        for violation in result['violations']:
            constraint = violation['constraint']
            if constraint not in violation_stats:
                violation_stats[constraint] = {
                    'count': 0,
                    'severity': violation['severity']
                }
            violation_stats[constraint]['count'] += 1
    
    if violation_stats:
        print("\nViolations by constraint:")
        for constraint, stats in sorted(violation_stats.items()):
            print(f"  {constraint} ({stats['severity']}): {stats['count']}")
    else:
        print("\nNo violations found")
    
    # By facility type
    print("\nBy facility type:")
    by_kind = {}
    
    for i, result in enumerate(results['results']):
        kind = candidates[i].get('kind', 'unknown')
        
        if kind not in by_kind:
            by_kind[kind] = {'total': 0, 'feasible': 0, 'infeasible': 0}
        
        by_kind[kind]['total'] += 1
        if result['feasible']:
            by_kind[kind]['feasible'] += 1
        else:
            by_kind[kind]['infeasible'] += 1
    
    for kind, stats in sorted(by_kind.items()):
        feasible_rate = stats['feasible'] / stats['total'] * 100
        print(f"  {kind}:")
        print(f"    - Total: {stats['total']}")
        print(f"    - Feasible: {stats['feasible']} ({feasible_rate:.1f}%)")
        print(f"    - Infeasible: {stats['infeasible']} ({100-feasible_rate:.1f}%)")
    
    # 6. Save report
    report = {
        'run': run,
        'total_candidates': results['total'],
        'feasible': results['feasible'],
        'infeasible': results['infeasible'],
        'feasible_rate': results['feasible_rate'],
        'violation_stats': violation_stats,
        'by_kind': by_kind
    }
    
    report_file = ROOT / "out" / "l2opt" / run / "constraint_check_report.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\nReport saved: {report_file}")
    
    # 7. Key conclusions
    print("\n" + "=" * 80)
    print("Key Conclusions")
    print("=" * 80)
    
    print(f"\nOriginal library: {results['total']} items")
    print(f"Feasible: {results['feasible']} ({results['feasible_rate']:.1f}%)")
    print(f"Infeasible: {results['infeasible']} ({100-results['feasible_rate']:.1f}%)")
    
    if results['feasible_rate'] < 50:
        print("\nWARNING: Over half of candidates are infeasible!")
    
    if 'C001_NO_BUILDING' in violation_stats:
        count = violation_stats['C001_NO_BUILDING']['count']
        print(f"\nBuilding intersection: {count} ({count/results['total']*100:.1f}%)")
    
    if 'C003_SEA_MAX_LENGTH' in violation_stats:
        count = violation_stats['C003_SEA_MAX_LENGTH']['count']
        print(f"\nSubsea pipe too long: {count} ({count/results['total']*100:.1f}%)")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    import sys
    run = sys.argv[1] if len(sys.argv) > 1 else "RDA_96B"
    test_existing_candidates(run)
