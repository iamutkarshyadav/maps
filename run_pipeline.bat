@echo off
echo Starting Pipeline...
cd scripts


echo.
echo Running Distance Matrix Calculation...
python dh_distance_matrix.py

echo.
echo Running VRP Solver...
python vrp_solver.py

echo.
echo Generating Final Master Plan...
python generate_zepto_report.py

echo.
echo Generating Optimized Routes Map...
python visualize_optimized_routes.py

cd ..
echo.
echo Pipeline Complete!
