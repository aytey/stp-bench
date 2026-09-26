#!/bin/bash
while ps -eo args | grep -q "[r]un_comparison.py configs/fp_abstraction_ab_head"; do sleep 30; done
cd /home/avj/clones/stp/benchmark-scripts && python3 ./run_comparison.py configs/fp_abstraction_levers.yaml --no-override --file-list /home/avj/fp-abs-results/hard-64-128.list --timeout 60 --runs 1 --no-revalidate --workers 12 --output /home/avj/fp-abs-results/levers-hard.csv --force > /home/avj/fp-abs-results/levers-hard.log 2>&1
echo "=== levers done: $(date) ===" >> /home/avj/fp-abs-results/levers-hard.log
