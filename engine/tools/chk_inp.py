import sys
from swmm.toolkit import solver
for n in sys.argv[1:]:
    solver.swmm_open(f"/work/{n}.inp", f"/tmp/{n}.rpt", f"/tmp/{n}.out"); solver.swmm_start(0)
    for _ in range(40): solver.swmm_step()
    solver.swmm_end(); solver.swmm_close(); print(n, "OK")
