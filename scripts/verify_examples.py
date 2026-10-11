"""Repository-only scientific regression runner; excluded from end-user releases."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lab_service import LabService

parser = argparse.ArgumentParser()
parser.add_argument('kind', choices=['vendi', 'mannkendall'])
parser.add_argument('--data-dir', required=True)
parser.add_argument('--python', required=True)
args = parser.parse_args()
if args.kind == 'vendi':
    from vendi_example import run_demo
else:
    from mannkendall_example import run_demo
result = run_demo(LabService(args.data_dir), args.python)
print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
sys.exit(0 if result['success'] else 1)
